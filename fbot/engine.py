"""백테스트 엔진 (봉 단위 시뮬레이션).

규칙
- 봉 i-1 마감 시점의 신호를 봉 i 시가에 시장가로 실행 (미래 참조 없음)
- 진입/청산마다 수수료 + 슬리피지 차감
- 펀딩 시각(기본 UTC 0·8·16시)에 포지션이 있으면 펀딩비 정산 (롱은 양수 펀딩비를 냄)
- 봉 안에서 손절가/청산가/익절가를 고가·저가로 확인. 손절과 익절이 같은 봉에서
  둘 다 닿으면 보수적으로 '손절'로 처리
- 손절당한 방향으로는 신호가 한 번 바뀔 때까지 재진입하지 않음
- 하루 손실 한도, 최대낙폭 비상정지 적용
"""
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Params:
    taker_fee: float
    slippage: float  # 비율 (bps / 10000)
    leverage: float
    risk_per_trade: float
    daily_max_loss: float
    max_drawdown_kill: float
    maintenance_margin: float
    take_profit_r: float | None


@dataclass
class Result:
    equity: np.ndarray
    trades: list = field(default_factory=list)
    fees: float = 0.0
    funding: float = 0.0
    liquidations: int = 0
    killed_at: int | None = None
    daily_halts: int = 0


def _liq_price(direction, qty, entry, cash, mmr):
    if qty <= 0:
        return None
    if direction == 1:
        return (qty * entry - cash) / (qty * (1 - mmr))
    return (cash + qty * entry) / (qty * (1 + mmr))


def run(m: dict, target, stop_dist, i0: int, i1: int, equity0: float, p: Params) -> Result:
    """m: open/high/low/close/day/funding 리스트, target/stop_dist: 전략 신호 리스트.
    [i0, i1) 구간만 시뮬레이션. i0 >= 1 이어야 함."""
    O, H, L, C, DAY, FUND = m["open"], m["high"], m["low"], m["close"], m["day"], m["funding"]
    n = i1 - i0
    eq_curve = np.empty(n)
    res = Result(equity=eq_curve)

    cash = equity0
    direction, qty, entry, stop, tp, entry_fee, entry_i = 0, 0.0, 0.0, 0.0, None, 0.0, 0
    blocked = 0
    cur_day, day_start, halted = None, equity0, False
    peak, killed = equity0, False
    slip, fee, mmr = p.slippage, p.taker_fee, p.maintenance_margin

    def close_pos(i, price, reason, apply_slip=True):
        nonlocal cash, direction, qty, entry, stop, tp
        fill = price * (1 - direction * slip) if apply_slip else price
        pnl = direction * qty * (fill - entry)
        f = qty * fill * fee
        cash += pnl - f
        res.fees += f
        res.trades.append(
            {
                "entry_i": entry_i, "exit_i": i, "side": "long" if direction == 1 else "short",
                "qty": qty, "entry": entry, "exit": fill,
                "pnl": pnl - f - entry_fee, "reason": reason,
            }
        )
        direction, qty, entry, stop, tp = 0, 0.0, 0.0, 0.0, None

    def open_pos(i, price, d, sd):
        nonlocal cash, direction, qty, entry, stop, tp, entry_fee, entry_i
        if not (sd > 0) or cash <= 0:
            return
        fill = price * (1 + d * slip)
        q = min(cash * p.risk_per_trade / sd, cash * p.leverage / fill)
        if q <= 0:
            return
        f = q * fill * fee
        cash -= f
        res.fees += f
        direction, qty, entry, entry_fee, entry_i = d, q, fill, f, i
        stop = fill - d * sd
        tp = fill + d * sd * p.take_profit_r if p.take_profit_r else None

    for k in range(n):
        i = i0 + k
        o, h, l, c = O[i], H[i], L[i], C[i]

        # 0) 날짜 바뀌면 일일 손실 기준 갱신
        if DAY[i] != cur_day:
            cur_day = DAY[i]
            day_start = cash + direction * qty * (o - entry)
            halted = False

        # 1) 펀딩비 정산 (봉 시작 시각 기준)
        if direction != 0 and FUND[i] != 0.0:
            cost = direction * qty * o * FUND[i]
            cash -= cost
            res.funding += cost

        # 2) 직전 봉 신호를 이번 봉 시가에 실행
        want = 0 if (killed or halted) else int(target[i - 1])
        if blocked != 0:
            if want == blocked:
                want = 0 if direction == 0 else direction
            else:
                blocked = 0
        if want != direction:
            if direction != 0:
                close_pos(i, o, "signal")
            if want != 0:
                open_pos(i, o, want, stop_dist[i - 1])

        # 3) 봉 안에서 청산가 / 손절 / 익절 확인
        if direction != 0:
            liq = _liq_price(direction, qty, entry, cash, mmr)
            if direction == 1:
                trig = max(stop, liq if liq is not None else -np.inf)
                if l <= trig:
                    is_liq = liq is not None and trig == liq and liq > stop
                    px = min(o, trig)
                    d = direction
                    close_pos(i, px, "liquidation" if is_liq else "stop")
                    res.liquidations += is_liq
                    blocked = d
                elif tp is not None and h >= tp:
                    close_pos(i, max(o, tp), "take_profit", apply_slip=False)
            else:
                trig = min(stop, liq if liq is not None else np.inf)
                if h >= trig:
                    is_liq = liq is not None and trig == liq and liq < stop
                    px = max(o, trig)
                    d = direction
                    close_pos(i, px, "liquidation" if is_liq else "stop")
                    res.liquidations += is_liq
                    blocked = d
                elif tp is not None and l <= tp:
                    close_pos(i, min(o, tp), "take_profit", apply_slip=False)

        # 4) 종가 기준 평가 + 안전장치
        eq = cash + direction * qty * (c - entry)
        if not halted and not killed and eq < day_start * (1 - p.daily_max_loss):
            if direction != 0:
                close_pos(i, c, "daily_limit")
            halted = True
            res.daily_halts += 1
            eq = cash
        peak = max(peak, eq)
        if not killed and (eq <= 0 or eq < peak * (1 - p.max_drawdown_kill)):
            if direction != 0:
                close_pos(i, c, "kill_switch")
            killed = True
            res.killed_at = i
            eq = cash
        eq_curve[k] = eq

    # 구간 끝에 열린 포지션은 마지막 종가로 정리
    if direction != 0:
        close_pos(i1 - 1, C[i1 - 1], "end")
        eq_curve[-1] = cash
    return res
