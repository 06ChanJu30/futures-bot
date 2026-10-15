"""매매 전략 (규칙 기반).

각 전략은 봉이 '마감된 시점'의 데이터로 목표 포지션을 정합니다.
  target    :  1 = 롱, -1 = 숏, 0 = 포지션 없음
  stop_dist :  손절까지의 가격 거리 (ATR × 배수). 추적 손절을 켜면 매 봉 갱신
  size      :  (선택) 0~1, 신호 강도에 따른 포지션 크기 배율
엔진은 신호를 '다음 봉 시가'에 실행하므로 미래 데이터를 볼 수 없습니다.

기간 파라미터는 '일(day)' 단위로 받고, bpd(하루당 봉 수)로 봉 개수로 바꿉니다.
그래서 4시간봉이든 일봉이든 같은 설정을 그대로 쓸 수 있어요.

참고한 자료
- trend_ema / tsmom_ensemble : Grayscale(2023) 50일·150일 이동평균 추세 신호,
  WisdomTree 1개월+3개월 모멘텀 혼합, 시계열 모멘텀(TSMOM) 연구
- trend_pullback : 장기 추세 위에서 RSI(2) 단기 눌림목 매수 (Connors 계열, setup4alpha 사례)
- donchian_breakout : 터틀 트레이딩식 채널 돌파 (비교 기준용)
"""
import numpy as np
import pandas as pd

from .indicators import atr, ema, rsi


def _n(days, bpd):
    return max(2, int(round(days * bpd)))


def _hold_state(enter_long, enter_short, exit_long, exit_short) -> np.ndarray:
    """진입/청산 이벤트를 '현재 들고 있어야 할 방향'으로 변환."""
    n = len(enter_long)
    out = np.zeros(n, dtype=np.int8)
    cur = 0
    for i in range(n):
        if cur == 1 and exit_long[i]:
            cur = 0
        elif cur == -1 and exit_short[i]:
            cur = 0
        if enter_long[i]:
            cur = 1
        elif enter_short[i]:
            cur = -1
        out[i] = cur
    return out


def _out(df, target, atr_mult, size=None):
    d = {"target": np.asarray(target, dtype=np.int8), "stop_dist": atr(df) * atr_mult}
    if size is not None:
        d["size"] = np.asarray(size, dtype=float)
    return pd.DataFrame(d, index=df.index)


def trend_ema(df, bpd, fast_days, slow_days, atr_mult, allow_short=True):
    """추세 추종: 단기 EMA가 장기 EMA 위면 롱, 아래면 숏(허용 시)."""
    if fast_days >= slow_days:
        return None
    f, s = ema(df["close"], _n(fast_days, bpd)), ema(df["close"], _n(slow_days, bpd))
    target = np.where(f > s, 1, np.where((f < s) & allow_short, -1, 0))
    target = np.where(f.isna() | s.isna(), 0, target)
    return _out(df, target, atr_mult)


def tsmom_ensemble(df, bpd, lookbacks, short_mode, atr_mult):
    """여러 기간 모멘텀 앙상블.
    각 기간(예: 10·30·60일) 수익률의 부호를 평균 → -1~1 점수.
    점수 > 0 이면 롱, 크기는 점수에 비례(동의하는 기간이 많을수록 크게).
    숏은 short_mode='consensus'일 때 모든 기간이 하락에 동의할 때만 (상승 편향 반영)."""
    c = df["close"]
    signs = [np.sign(c / c.shift(_n(d, bpd)) - 1) for d in lookbacks]
    score = pd.concat(signs, axis=1).mean(axis=1)
    ready = pd.concat(signs, axis=1).notna().all(axis=1)
    target = np.where(score > 0, 1, 0)
    if short_mode == "consensus":
        target = np.where(score <= -0.999, -1, target)
    target = np.where(ready, target, 0)
    size = np.where(target != 0, score.abs().fillna(0), 0)
    return _out(df, target, atr_mult, size)


def trend_pullback(df, bpd, trend_days, entry_rsi, exit_rsi, atr_mult, allow_short=False):
    """추세 방향 눌림목: 가격이 장기 이동평균 위 + RSI(2) 과매도 → 롱, RSI(2) 회복 시 청산."""
    c = df["close"]
    ma = c.rolling(_n(trend_days, bpd)).mean()
    r = rsi(c, 2)
    up, down = c > ma, (c < ma) & ma.notna()
    target = _hold_state(
        ((r < entry_rsi) & up).to_numpy(),
        ((r > 100 - entry_rsi) & down).to_numpy() if allow_short else np.zeros(len(c), bool),
        ((r > exit_rsi) | down).to_numpy(),
        ((r < 100 - exit_rsi) | up).to_numpy(),
    )
    return _out(df, target, atr_mult)


def donchian_breakout(df, bpd, entry_days, atr_mult):
    """채널 돌파: 최근 N일 최고가 돌파 시 롱, 최저가 붕괴 시 숏. N/2일 반대 채널에서 청산."""
    n = _n(entry_days, bpd)
    hi, lo = df["high"].rolling(n).max().shift(1), df["low"].rolling(n).min().shift(1)
    h = max(2, n // 2)
    exit_lo, exit_hi = df["low"].rolling(h).min().shift(1), df["high"].rolling(h).max().shift(1)
    c = df["close"]
    target = _hold_state(
        (c > hi).to_numpy(), (c < lo).to_numpy(), (c < exit_lo).to_numpy(), (c > exit_hi).to_numpy()
    )
    return _out(df, target, atr_mult)


STRATEGIES = {
    "tsmom_ensemble": tsmom_ensemble,
    "trend_ema": trend_ema,
    "trend_pullback": trend_pullback,
    "donchian_breakout": donchian_breakout,
}


def funding_filter(sig: pd.DataFrame, df: pd.DataFrame, bpd: float, z_max: float) -> pd.DataFrame:
    """펀딩비 과열 필터 (BitMEX 리서치 참고, 근거는 약함 → 기본 꺼짐).
    최근 3일 펀딩비 합이 과거 90일 대비 z_max 표준편차 이상 높으면 롱 회피, 낮으면 숏 회피."""
    f3 = df["funding"].rolling(_n(3, bpd)).sum()
    z = (f3 - f3.rolling(_n(90, bpd)).mean()) / f3.rolling(_n(90, bpd)).std()
    sig = sig.copy()
    t = sig["target"].to_numpy().copy()
    t[(t == 1) & (z > z_max).to_numpy()] = 0
    t[(t == -1) & (z < -z_max).to_numpy()] = 0
    sig["target"] = t
    return sig
