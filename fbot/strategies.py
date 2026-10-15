"""매매 전략.

각 전략은 봉이 '마감된 시점'의 데이터로 목표 포지션을 정합니다.
  target    :  1 = 롱, -1 = 숏, 0 = 포지션 없음
  stop_dist :  진입 시 손절까지의 가격 거리 (ATR × 배수)
엔진은 이 신호를 '다음 봉 시가'에 실행하므로 미래 데이터를 볼 수 없습니다.
"""
import numpy as np
import pandas as pd

from .indicators import atr, ema, rsi


def _hold_state(enter_long, enter_short, exit_long, exit_short) -> np.ndarray:
    """진입/청산 이벤트를 받아 '현재 들고 있어야 할 방향'으로 바꿈."""
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


def trend_ema(df: pd.DataFrame, fast: int, slow: int, atr_mult: float) -> pd.DataFrame:
    """추세 추종: 단기 EMA가 장기 EMA 위면 롱, 아래면 숏."""
    if fast >= slow:
        return None
    f, s = ema(df["close"], fast), ema(df["close"], slow)
    target = np.where(f > s, 1, np.where(f < s, -1, 0))
    target = np.where(f.isna() | s.isna(), 0, target)
    return pd.DataFrame({"target": target, "stop_dist": atr(df) * atr_mult}, index=df.index)


def donchian_breakout(df: pd.DataFrame, lookback: int, atr_mult: float) -> pd.DataFrame:
    """돌파: 최근 N봉 최고가를 넘으면 롱, 최저가를 깨면 숏. N/2봉 반대 채널에서 청산."""
    hi = df["high"].rolling(lookback).max().shift(1)
    lo = df["low"].rolling(lookback).min().shift(1)
    half = max(2, lookback // 2)
    exit_lo = df["low"].rolling(half).min().shift(1)
    exit_hi = df["high"].rolling(half).max().shift(1)
    c = df["close"]
    target = _hold_state(
        (c > hi).to_numpy(), (c < lo).to_numpy(), (c < exit_lo).to_numpy(), (c > exit_hi).to_numpy()
    )
    return pd.DataFrame({"target": target, "stop_dist": atr(df) * atr_mult}, index=df.index)


def rsi_reversion(df: pd.DataFrame, rsi_len: int, oversold: float, atr_mult: float) -> pd.DataFrame:
    """역추세: 큰 추세(EMA200) 방향으로만, RSI 과매도에 롱 / 과매수에 숏. RSI 50 복귀 시 청산."""
    r = rsi(df["close"], rsi_len)
    trend = ema(df["close"], 200)
    up = df["close"] > trend
    overbought = 100 - oversold
    target = _hold_state(
        ((r < oversold) & up).to_numpy(),
        ((r > overbought) & ~up & trend.notna()).to_numpy(),
        (r > 50).to_numpy(),
        (r < 50).to_numpy(),
    )
    return pd.DataFrame({"target": target, "stop_dist": atr(df) * atr_mult}, index=df.index)


STRATEGIES = {
    "trend_ema": trend_ema,
    "donchian_breakout": donchian_breakout,
    "rsi_reversion": rsi_reversion,
}
