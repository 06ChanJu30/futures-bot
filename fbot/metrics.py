import numpy as np


def summarize(equity: np.ndarray, trades: list, periods_per_year: float, equity0: float) -> dict:
    eq = np.concatenate([[equity0], np.asarray(equity, dtype=float)])
    rets = np.diff(eq) / np.where(eq[:-1] == 0, np.nan, eq[:-1])
    rets = np.nan_to_num(rets)
    years = len(equity) / periods_per_year if periods_per_year else 0
    total = eq[-1] / equity0 - 1
    cagr = (eq[-1] / equity0) ** (1 / years) - 1 if years > 0 and eq[-1] > 0 else -1.0
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / np.where(peak == 0, np.nan, peak)
    mdd = float(np.nanmin(dd)) if len(dd) else 0.0
    sd = rets.std()
    sharpe = float(rets.mean() / sd * np.sqrt(periods_per_year)) if sd > 0 else 0.0
    pnls = np.array([t["pnl"] for t in trades]) if trades else np.array([])
    wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else (np.inf if len(wins) else 0.0)
    return {
        "total_return": float(total),
        "cagr": float(cagr),
        "max_drawdown": mdd,
        "sharpe": sharpe,
        "calmar": float(cagr / -mdd) if mdd < 0 else 0.0,
        "trades": int(len(pnls)),
        "win_rate": float((pnls > 0).mean()) if len(pnls) else 0.0,
        "profit_factor": float(pf),
        "final_equity": float(eq[-1]),
    }
