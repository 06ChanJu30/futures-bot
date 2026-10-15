"""워크포워드 검증.

[학습 12개월]에서 파라미터를 고르고 → 바로 다음 [검증 3개월](처음 보는 데이터)에 적용.
창을 3개월씩 밀면서 반복하고, 검증 구간 결과만 이어 붙인 것이 '실전에 가까운 성적'입니다.
학습 구간 성적과 검증 구간 성적의 차이가 크면 과최적화를 의심해야 합니다.
"""
import itertools

import numpy as np
import pandas as pd

from . import engine
from .metrics import summarize
from .strategies import STRATEGIES

WARMUP_BARS = 300  # 지표(EMA200 등)가 안정될 때까지 건너뛰는 봉 수


def make_params(cfg) -> engine.Params:
    c, r = cfg["costs"], cfg["risk"]
    return engine.Params(
        taker_fee=c["taker_fee"], slippage=c["slippage_bps"] / 10_000, leverage=r["leverage"],
        risk_per_trade=r["risk_per_trade"], daily_max_loss=r["daily_max_loss"],
        max_drawdown_kill=r["max_drawdown_kill"], maintenance_margin=r["maintenance_margin"],
        take_profit_r=r["take_profit_r"],
    )


def market_arrays(df):
    return {
        "open": df["open"].tolist(), "high": df["high"].tolist(), "low": df["low"].tolist(),
        "close": df["close"].tolist(), "funding": df["funding"].tolist(),
        "day": df.index.strftime("%Y-%m-%d").tolist(),
    }


def periods_per_year(df):
    step = pd.Series(df.index).diff().median()
    return pd.Timedelta(days=365) / step


def windows(df, train_m, test_m):
    out = []
    start = df.index[WARMUP_BARS]
    end = df.index[-1]
    while True:
        tr_end = start + pd.DateOffset(months=train_m)
        te_end = tr_end + pd.DateOffset(months=test_m)
        if tr_end >= end:
            break
        te_end = min(te_end, end + pd.Timedelta(seconds=1))
        i0, i1 = df.index.searchsorted(start), df.index.searchsorted(tr_end)
        j1 = df.index.searchsorted(te_end)
        if j1 - i1 < 24 * 7:  # 검증 구간이 1주도 안 되면 생략
            break
        out.append((i0, i1, i1, j1))
        start += pd.DateOffset(months=test_m)
    return out


def param_grid(spec: dict):
    keys = list(spec)
    for vals in itertools.product(*[spec[k] for k in keys]):
        yield dict(zip(keys, vals))


def score(stats, objective):
    return stats.get(objective, stats["sharpe"])


def run_strategy(name, df, m, cfg, log=print):
    p = make_params(cfg)
    wf = cfg["walkforward"]
    ppy = periods_per_year(df)
    eq0 = cfg["account"]["initial_equity"]

    combos = []
    for prm in param_grid(cfg["strategies"][name]):
        sig = STRATEGIES[name](df, **prm)
        if sig is None:
            continue
        combos.append((prm, sig["target"].tolist(), sig["stop_dist"].fillna(0).tolist()))

    wins = windows(df, wf["train_months"], wf["test_months"])
    oos_eq, oos_trades, rows = [], [], []
    equity = eq0
    killed = False
    for (a, b, c_, d) in wins:
        best, best_s, best_stats = None, -np.inf, None
        for prm, tgt, sd in combos:
            r = engine.run(m, tgt, sd, a, b, eq0, p)
            st = summarize(r.equity, r.trades, ppy, eq0)
            if st["trades"] < wf["min_trades"]:
                continue
            s = score(st, wf["objective"])
            if s > best_s:
                best, best_s, best_stats = (prm, tgt, sd), s, st
        if best is None or killed:
            seg = np.full(d - c_, equity)
            test_stats = None
            chosen = "매매 안 함 (조건 맞는 파라미터 없음)" if not killed else "비상정지 상태"
        else:
            r = engine.run(m, best[1], best[2], c_, d, equity, p)
            seg = r.equity
            for t in r.trades:
                t = dict(t)
                t["entry_time"], t["exit_time"] = df.index[t.pop("entry_i")], df.index[t.pop("exit_i")]
                oos_trades.append(t)
            test_stats = summarize(r.equity, r.trades, ppy, equity)
            chosen = ", ".join(f"{k}={v}" for k, v in best[0].items())
            if r.killed_at is not None:
                killed = True
        oos_eq.append(seg)
        equity = float(seg[-1])
        rows.append({
            "train": f"{df.index[a]:%Y-%m-%d} ~ {df.index[b - 1]:%Y-%m-%d}",
            "test": f"{df.index[c_]:%Y-%m-%d} ~ {df.index[d - 1]:%Y-%m-%d}",
            "params": chosen,
            "train_return": best_stats["total_return"] if best_stats else None,
            "train_sharpe": best_stats["sharpe"] if best_stats else None,
            "test_return": test_stats["total_return"] if test_stats else 0.0,
            "test_sharpe": test_stats["sharpe"] if test_stats else 0.0,
        })
        log(f"  [{name}] 검증 {rows[-1]['test']}  {chosen}  → {rows[-1]['test_return']:+.1%}")

    if not wins:
        raise SystemExit("데이터가 너무 짧아 워크포워드 창을 만들 수 없어요. years를 늘려주세요.")
    eq = np.concatenate(oos_eq)
    first, last = wins[0][2], wins[-1][3]
    stats = summarize(eq, oos_trades, ppy, eq0)

    # 참고용: 전체 기간을 다 보고 고른 '최고 파라미터' (= 과최적화된 성적)
    best_full = max(
        (summarize(engine.run(m, t, s, first, last, eq0, p).equity, [], ppy, eq0)["total_return"], prm)
        for prm, t, s in combos
    ) if combos else (0.0, {})
    return {
        "name": name, "index": df.index[first:last], "equity": eq, "stats": stats,
        "windows": rows, "trades": oos_trades, "in_sample_best": best_full,
    }


def buy_and_hold(df, i0, i1, cfg):
    eq0 = cfg["account"]["initial_equity"]
    px = df["close"].to_numpy()[i0:i1]
    eq = eq0 * px / df["open"].to_numpy()[i0]
    return eq, summarize(eq, [], periods_per_year(df), eq0)
