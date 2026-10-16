"""
TsmomEnsemble — 여러 기간 모멘텀 앙상블 (일봉, USDT 무기한 선물)

futures-bot 백테스트 엔진(fbot)의 tsmom_ensemble 전략을 Freqtrade로 그대로 옮긴 것.
견고성 검사(robustness.py)를 통과한 고정 설정을 사용합니다. 설정을 바꾸지 마세요.

규칙
- 점수 = 10·30·60일 수익률 부호의 평균 (-1 ~ +1)
- 점수 > 0 → 롱 / 점수 ≤ 0 → 청산 (숏 없음)
- 포지션 크기 = (페어별 자본) × 목표변동성 40% ÷ 최근 30일 변동성 × |점수|, 최대 2배
- 손절 = 진입가 - ATR(14)×5, 이후 매일 종가 - ATR×5 로 따라 올림 (내려가지 않음)
- 손절당하면 신호가 한 번 꺼질 때까지 같은 방향 재진입 금지
- 안전장치: 하루(UTC) 손실 5% → 그날 청산·매매 중지 / 고점 대비 30% → 영구 정지
  (영구 정지 해제: user_data/kill_switch_state.<거래소>.<dryrun|live>.json 삭제 후 재시작)
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from pandas import DataFrame

from freqtrade.enums import RunMode
from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, stoploss_from_absolute

logger = logging.getLogger(__name__)

STOP_REASONS = {"stop_loss", "stoploss_on_exchange", "trailing_stop_loss", "liquidation"}


class TsmomEnsemble(IStrategy):
    INTERFACE_VERSION = 3

    # ===== 고정 설정 (견고성 검사 통과 설정) =====
    LOOKBACKS = (10, 30, 60)      # 일
    SHORT_MODE = "none"           # "none" | "consensus"
    ATR_LEN = 14
    ATR_MULT = 5.0
    VOL_DAYS = 30
    TARGET_VOL = 0.40             # 연 변동성 목표
    MAX_LEVERAGE = 2.0
    DAILY_MAX_LOSS = 0.05
    MAX_DRAWDOWN_KILL = 0.30

    timeframe = "1d"
    can_short = SHORT_MODE == "consensus"
    startup_candle_count = 200
    process_only_new_candles = True

    minimal_roi = {"0": 100}      # ROI로는 청산하지 않음 (신호·손절로만)
    stoploss = -0.99              # 비상용 바닥값. 실제 손절은 custom_stoploss
    use_custom_stoploss = True
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = True

    order_types = {
        "entry": "market",
        "exit": "market",
        "emergency_exit": "market",
        "force_entry": "market",
        "force_exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": True,          # 봇이 꺼져도 거래소에 손절 주문이 남아 있음
        "stoploss_on_exchange_interval": 60,
    }

    # ---------------------------------------------------------------- 지표
    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        c = dataframe["close"]
        signs = pd.concat([np.sign(c / c.shift(n) - 1) for n in self.LOOKBACKS], axis=1)
        dataframe["score"] = signs.mean(axis=1)
        ready = signs.notna().all(axis=1)

        target = np.where(dataframe["score"] > 0, 1, 0)
        if self.SHORT_MODE == "consensus":
            target = np.where(dataframe["score"] <= -0.999, -1, target)
        dataframe["target"] = np.where(ready, target, 0)
        dataframe["size"] = np.where(dataframe["target"] != 0, dataframe["score"].abs().fillna(0), 0.0)

        prev_close = c.shift(1)
        tr = pd.concat(
            [dataframe["high"] - dataframe["low"], (dataframe["high"] - prev_close).abs(),
             (dataframe["low"] - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / self.ATR_LEN, adjust=False, min_periods=self.ATR_LEN).mean()
        dataframe["stop_dist"] = atr * self.ATR_MULT

        lr = np.log(c).diff()
        dataframe["vol"] = lr.rolling(self.VOL_DAYS).std() * np.sqrt(365)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["target"] == 1, ["enter_long", "enter_tag"]] = (1, "tsmom_long")
        if self.can_short:
            dataframe.loc[dataframe["target"] == -1, ["enter_short", "enter_tag"]] = (1, "tsmom_short")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[dataframe["target"] != 1, "exit_long"] = 1
        if self.can_short:
            dataframe.loc[dataframe["target"] != -1, "exit_short"] = 1
        return dataframe

    # ---------------------------------------------------------------- 크기·레버리지
    def _last(self, pair):
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        return df, (df.iloc[-1] if len(df) else None)

    def _pair_equity(self):
        n = max(1, len(self.dp.current_whitelist())) if self.dp else 1
        return self.wallets.get_total_stake_amount() / n

    def _target_notional(self, pair):
        _, last = self._last(pair)
        if last is None or not (last["vol"] > 0) or not (last["size"] > 0):
            return 0.0, 0.0
        eq = self._pair_equity()
        notional = eq * self.TARGET_VOL / last["vol"] * last["size"]
        return min(notional, eq * self.MAX_LEVERAGE), eq

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float, entry_tag: str | None,
                 side: str, **kwargs) -> float:
        # 필요한 만큼만 레버리지 사용 (1~2배). 낮을수록 강제청산 가격이 멀어짐
        notional, eq = self._target_notional(pair)
        lev = notional / eq if eq > 0 else 1.0
        return float(min(max(1.0, lev), self.MAX_LEVERAGE, max_leverage))

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float | None, max_stake: float,
                            leverage: float, entry_tag: str | None, side: str, **kwargs) -> float:
        notional, _ = self._target_notional(pair)
        stake = notional / max(leverage, 1.0)
        if min_stake and stake < min_stake:
            logger.info(f"{pair}: 목표 금액 {stake:.2f}이 거래소 최소 {min_stake:.2f}보다 작아 진입 생략")
            return 0
        return min(stake, max_stake)

    # ---------------------------------------------------------------- 손절
    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, after_fill: bool,
                        **kwargs) -> float | None:
        df, last = self._last(pair)
        if last is None or not (last["stop_dist"] > 0):
            return None
        d = -1 if trade.is_short else 1
        # 진입 직전 봉의 ATR로 정한 초기 손절가
        before = df[df["date"] < trade.open_date_utc]
        sd0 = before["stop_dist"].iloc[-1] if len(before) else last["stop_dist"]
        init = trade.open_rate - d * sd0
        # 마지막 마감 봉 기준 추적 손절가
        trail = last["close"] - d * last["stop_dist"]
        stop_price = max(init, trail) if d == 1 else min(init, trail)
        sl = stoploss_from_absolute(stop_price, current_rate, is_short=trade.is_short,
                                    leverage=trade.leverage)
        return sl if sl > 0 else None

    # ---------------------------------------------------------------- 안전장치
    def bot_start(self, **kwargs) -> None:
        # 비상정지 상태는 거래소별·모의/실거래별로 따로 저장 (서로 영향 없음)
        mode = "dryrun" if self.config.get("dry_run", True) else "live"
        ex = self.config["exchange"]["name"]
        self._state_path = Path(self.config["user_data_dir"]) / f"kill_switch_state.{ex}.{mode}.json"
        self._persist = self.dp.runmode in (RunMode.DRY_RUN, RunMode.LIVE)
        self._state = {"peak": None, "killed": False, "day": None, "day_start": None, "halted_day": None}
        if self._persist and self._state_path.exists():
            self._state.update(json.loads(self._state_path.read_text()))
            if self._state["killed"]:
                logger.warning(f"⛔ 비상정지 상태로 시작합니다. 해제하려면 {self._state_path.name} 삭제 후 재시작")

    def _save(self):
        if self._persist:
            self._state_path.write_text(json.dumps(self._state))

    def _equity(self):
        eq = self.wallets.get_starting_balance() + Trade.get_total_closed_profit()
        for t in Trade.get_trades_proxy(is_open=True):
            _, last = self._last(t.pair)
            if last is not None:
                rate = self.dp.ticker(t.pair).get("last") if self._persist else None
                eq += t.calc_profit(rate or last["close"])
        return eq

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        s = self._state
        try:
            eq = self._equity()
        except Exception as e:  # 시세 조회 실패 등 → 이번 루프는 건너뜀
            logger.debug(f"자산 계산 실패: {e}")
            return
        today = current_time.astimezone(timezone.utc).strftime("%Y-%m-%d")
        changed = False
        if s["day"] != today:
            s["day"], s["day_start"] = today, eq
            changed = True
        if s["peak"] is None or eq > s["peak"]:
            s["peak"] = eq
            changed = True
        if not s["killed"] and s["peak"] and eq < s["peak"] * (1 - self.MAX_DRAWDOWN_KILL):
            s["killed"] = True
            changed = True
            logger.warning(f"⛔ 비상정지: 자산 {eq:.2f}이 고점 {s['peak']:.2f} 대비 "
                           f"{self.MAX_DRAWDOWN_KILL:.0%} 이상 하락. 모든 포지션 청산, 신규 진입 중지")
        if s["halted_day"] != today and s["day_start"] and eq < s["day_start"] * (1 - self.DAILY_MAX_LOSS):
            s["halted_day"] = today
            changed = True
            logger.warning(f"일일 손실 한도 도달 ({eq:.2f} < {s['day_start']:.2f}×{1 - self.DAILY_MAX_LOSS:.2f}). "
                           "오늘(UTC)은 청산 후 매매 중지")
        if changed:
            self._save()

    def _blocked_today(self, current_time):
        s = self._state
        return s["killed"] or s["halted_day"] == current_time.astimezone(timezone.utc).strftime("%Y-%m-%d")

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime, current_rate: float,
                    current_profit: float, **kwargs):
        if self._state["killed"]:
            return "kill_switch"
        if self._blocked_today(current_time):
            return "daily_limit"
        return None

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: str | None,
                            side: str, **kwargs) -> bool:
        if self._blocked_today(current_time):
            return False
        # 손절당한 방향으로는 신호가 한 번 꺼질 때까지 재진입 금지
        closed = [t for t in Trade.get_trades_proxy(pair=pair, is_open=False) if t.close_date_utc]
        if closed:
            last = max(closed, key=lambda t: t.close_date_utc)
            if last.exit_reason in STOP_REASONS and (last.is_short == (side == "short")):
                df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                day0 = last.close_date_utc.replace(hour=0, minute=0, second=0, microsecond=0)
                want = -1 if side == "short" else 1
                since = df[df["date"] >= day0]  # 손절당한 날 이후 마감된 봉들
                if len(since) == 0 or (since["target"] == want).all():
                    return False
        return True
