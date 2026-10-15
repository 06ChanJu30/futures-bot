"""워크포워드 검증.

[학습 N개월]에서 파라미터를 고르고 → 바로 다음 [검증 M개월](처음 보는 데이터)에 적용.
창을 M개월씩 밀면서 반복하고, 검증 구간 결과만 이어 붙인 것이 '실전에 가까운 성적'입니다.
"""
import itertools
from collections import Counter

import numpy as np
import pandas as pd

from . import engine, ml
from .metrics import summarize
from .strategies import STRATEGIES, funding_filter

ML_STRATEGIES = {"ml_gbm"}


def make_params(cfg) -> engine.Params:
    c, r = cfg["costs"], cfg["risk"]
    return engine.Params(
        taker_fee=c["taker_fee"], slippage=c["slippage_bps"] / 10_000, leverage=r["leverage"],
        sizing=r["sizing"], risk_per_trade=r["risk_per_trade"], target_vol=r["target_vol"],
        trailing_stop=r["trailing_stop"], daily_max_loss=r["daily_max_loss"],
        max_drawdown_kill=r["max_drawdown_kill"], maintenance_margin=r["maintenance_margin"],
        take_profit_r=r["take_profit_r"],
    )


def periods_per_year(df):
    return pd.Timedelta(days=365) / pd.Series(df.index).diff().median()


def market_arrays(df, bpd):
    lr = np.log(df["close"]).diff()
    vol = lr.rolling(max(2, int(30 * bpd))).std() * np.sqrt(365 * bpd)  # 최근 30일 연환산 변동성
    return {
        "open": df["open"].tolist(), "high": df["high"].tolist(), "low": df["low"].tolist(),
        "close": df["close"].tolist(), "funding": df["funding"].tolist(),
        "vol": vol.fillna(0).tolist(), "day": df.index.strftime("%Y-%m-%d").tolist(),
    }


def windows(df, train_m, test_m, warmup_bars):
    out = []
    if warmup_bars >= len(df):
        return out
    start, end = df.index[warmup_bars], df.index[-1]
    while True:
        tr_end = start + pd.DateOffset(months=train_m)
        if tr_end >= end:
            break
        te_end = min(tr_end + pd.DateOffset(months=test_m), end + pd.Timedelta(seconds=1))
        i0, i1, j1 = (df.index.searchsorted(x) for x in (start, tr_end, te_end))
        if (df.index[j1 - 1] - df.index[i1]) < pd.Timedelta(days=14):  # 검증 구간 2주 미만이면 생략
            break
        out.append((i0, i1, i1, j1))
        start += pd.DateOffset(months=test_m)
    return out


def param_grid(spec: dict):
    """설정의 각 항목은 '후보 목록'. 모든 조합을 만듦. (목록이 아닌 값은 고정값)"""
    keys = list(spec)
    vals = [v if isinstance(v, list) else [v] for v in spec.values()]
    for combo in itertools.product(*vals):
        yield dict(zip(keys, combo))


def _fmt(prm):
    return ", ".join(f"{k}={v}" for k, v in prm.items())


def _rule_combos(name, df, bpd, cfg):
    combos = []
    zf = (cfg.get("filters") or {}).get("funding_z_max")
    for prm in param_grid(cfg["strategies"][name]):
        sig = STRATEGIES[name](df, bpd, **prm)
        if sig is None:
            continue
        if zf:
            sig = funding_filter(sig, df, bpd, zf)
        size = sig["size"].tolist() if "size" in sig else None
        combos.append((prm, sig["target"].tolist(), sig["stop_dist"].fillna(0).tolist(), size))
    return combos


def run_strategy(name, df, m, bpd, cfg, log=print):
    p = make_params(cfg)
    wf = cfg["walkforward"]
    ppy = periods_per_year(df)
    eq0 = cfg["account"]["initial_equity"]
    warm = int(wf["warmup_days"] * bpd)
    wins = windows(df, wf["train_months"], wf["test_months"], warm)
    if not wins:
        raise SystemExit("데이터가 너무 짧아 워크포워드 창을 만들 수 없어요. data.years를 늘려주세요.")
    is_ml = name in ML_STRATEGIES

    if is_ml:
        X = ml.features(df, bpd)
        grid = list(param_grid(cfg["strategies"][name]))
        ys = {h: ml.labels(df, bpd, h) for h in {g["horizon_days"] for g in grid}}
        atr_cache = {}

        def ml_signal(prm, fit_lo, fit_hi, pred_lo, pred_hi):
            from .indicators import atr
            proba = ml.fit_predict(X, ys[prm["horizon_days"]], bpd, prm["horizon_days"], fit_lo, fit_hi, pred_lo, pred_hi)
            tgt = np.zeros(len(df), dtype=np.int8)
            if proba is not None:
                tgt[pred_lo:pred_hi] = ml.to_target(proba, prm["threshold"], prm.get("allow_short", True))
            if prm["atr_mult"] not in atr_cache:
                atr_cache[prm["atr_mult"]] = (atr(df) * prm["atr_mult"]).fillna(0).tolist()
            return tgt.tolist(), atr_cache[prm["atr_mult"]]
        n_trials = len(grid)
    else:
        combos = _rule_combos(name, df, bpd, cfg)
        n_trials = len(combos)

    oos_eq, oos_trades, rows, chosen_list = [], [], [], []
    costs = Counter()
    bars_total = 0
    equity, peak, killed = eq0, eq0, False

    for (a, b, c_, d) in wins:
        min_tr = max(2, round(wf["min_trades_per_year"] * (b - a) / ppy))
        best, best_s, best_stats = None, -np.inf, None
        if is_ml:
            v0 = a + int((b - a) * 0.75)
            min_tr_v = max(2, round(wf["min_trades_per_year"] * (b - v0) / ppy))
            for prm in grid:
                tgt, sd = ml_signal(prm, a, v0, v0 - 1, b)
                r = engine.run(m, tgt, sd, v0, b, eq0, p)
                st = summarize(r.equity, r.trades, ppy, eq0)
                if st["trades"] >= min_tr_v and st[wf["objective"]] > best_s:
                    best, best_s, best_stats = prm, st[wf["objective"]], st
        else:
            for prm, tgt, sd, sz in combos:
                r = engine.run(m, tgt, sd, a, b, eq0, p, size=sz)
                st = summarize(r.equity, r.trades, ppy, eq0)
                if st["trades"] >= min_tr and st[wf["objective"]] > best_s:
                    best, best_s, best_stats = (prm, tgt, sd, sz), st[wf["objective"]], st

        if best is None or killed:
            seg = np.full(d - c_, equity)
            test_stats = None
            chosen = "비상정지 상태" if killed else "매매 안 함 (조건 맞는 설정 없음)"
        else:
            if is_ml:
                tgt, sd = ml_signal(best, a, b, c_ - 1, d)
                r = engine.run(m, tgt, sd, c_, d, equity, p, peak0=peak)
                prm = best
            else:
                prm, tgt, sd, sz = best
                r = engine.run(m, tgt, sd, c_, d, equity, p, peak0=peak, size=sz)
            peak = r.peak
            seg = r.equity
            for t in r.trades:
                t = dict(t)
                t["entry_time"], t["exit_time"] = df.index[t.pop("entry_i")], df.index[t.pop("exit_i")]
                oos_trades.append(t)
            costs.update({"fees": r.fees, "slippage": r.slippage, "funding": r.funding,
                          "liquidations": r.liquidations, "daily_halts": r.daily_halts,
                          "bars_in_market": r.bars_in_market})
            test_stats = summarize(r.equity, r.trades, ppy, equity)
            chosen = _fmt(prm)
            chosen_list.append(chosen)
            if r.killed_at is not None:
                killed = True
                costs["killed"] = 1
        bars_total += d - c_
        oos_eq.append(seg)
        equity = float(seg[-1])
        rows.append({
            "train": f"{df.index[a]:%Y-%m-%d} ~ {df.index[b - 1]:%Y-%m-%d}",
            "test": f"{df.index[c_]:%Y-%m-%d} ~ {df.index[d - 1]:%Y-%m-%d}",
            "params": chosen,
            "train_return": best_stats["total_return"] if best_stats else None,
            "test_return": test_stats["total_return"] if test_stats else 0.0,
        })
        log(f"    {rows[-1]['test']}  {chosen}  → {rows[-1]['test_return']:+.1%}")

    eq = np.concatenate(oos_eq)
    first, last = wins[0][2], wins[-1][3]
    stats = summarize(eq, oos_trades, ppy, eq0)
    stats["exposure"] = costs["bars_in_market"] / bars_total if bars_total else 0.0
    top = Counter(chosen_list).most_common(1)
    stability = top[0][1] / len(chosen_list) if top else 0.0

    in_sample = None
    if not is_ml and combos:
        in_sample = max(
            summarize(engine.run(m, t, s, first, last, eq0, p, size=z).equity, [], ppy, eq0)["total_return"]
            for _, t, s, z in combos
        )
    return {
        "name": name, "index": df.index[first:last], "equity": eq, "stats": stats,
        "windows": rows, "trades": oos_trades, "in_sample_best": in_sample,
        "costs": dict(costs), "n_trials": n_trials, "stability": stability,
        "top_params": top[0][0] if top else "-",
    }


def buy_and_hold(df, i0, i1, cfg):
    eq0 = cfg["account"]["initial_equity"]
    eq = eq0 * df["close"].to_numpy()[i0:i1] / df["open"].to_numpy()[i0]
    return eq, summarize(eq, [], periods_per_year(df), eq0)
