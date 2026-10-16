"""견고성 검사 — 후보 전략이 '우연히 BTC에서만 잘 된 것'인지 확인.

1) 고정 설정: 최적화 없이 정해진 설정 하나로 전체 기간 실행
2) 다른 종목: 전략을 고를 때 쓰지 않은 ETH·SOL·BNB·XRP에 그대로 적용
3) 주변 설정: 기간·손절폭을 바꿔도 성적이 유지되는지 (한 점에서만 좋으면 과최적화)
4) 비용 스트레스: 수수료 2배, 슬리피지 3배에서도 버티는지
5) 연도별 성적: 특정 시기에만 몰려 있는지

  python robustness.py              # 필요한 종목 데이터를 자동으로 받고 검사 (처음엔 몇 분)
  python robustness.py --synthetic  # 가짜 데이터로 동작 확인
"""
import argparse
import copy
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

from fbot import data, engine, walkforward as wfm
from fbot.metrics import summarize
from fbot.strategies import tsmom_ensemble

# ===== 검사할 후보 (1차 워크포워드에서 가장 자주 선택된 설정) =====
BASE = {"lookbacks": [10, 30, 60], "short_mode": "none", "atr_mult": 5.0}
TF = "1d"
SYMBOLS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT", "XRP/USDT:USDT"]
NEIGHBORS = {
    "lookbacks": [[5, 15, 30], [7, 21, 42], [10, 30, 60], [14, 42, 84], [20, 60, 120], [10, 30, 90], [10, 60]],
    "short_mode": ["none", "consensus"],
    "atr_mult": [3.0, 4.0, 5.0, 6.0],
}


def sym_cfg(cfg, symbol):
    c = copy.deepcopy(cfg)
    c["data"]["symbol"] = symbol
    return c


def load_symbol(cfg, symbol, synthetic, seed):
    if synthetic:
        df, _ = data.make_synthetic(seed=seed)
        c = cfg["costs"]
        hours = df.index.hour.isin(c["funding_hours_utc"])
        df["funding"] = np.where(hours, c["default_funding_rate"], 0.0)
        return df
    c = sym_cfg(cfg, symbol)
    path, _ = data._paths(c)
    if not path.exists():
        print(f"  {symbol} 데이터가 없어서 받는 중...")
        data.fetch(c, log=lambda *_: None)
    return data.load(c)


def run_fixed(df, cfg, prm, p=None):
    """설정 하나로 [워밍업 이후 ~ 끝] 실행."""
    bpd = data.bars_per_day(df)
    m = wfm.market_arrays(df, bpd)
    sig = tsmom_ensemble(df, bpd, **prm)
    i0 = max(1, int(cfg["walkforward"]["warmup_days"] * bpd))
    p = p or wfm.make_params(cfg)
    eq0 = cfg["account"]["initial_equity"]
    r = engine.run(m, sig["target"].tolist(), sig["stop_dist"].fillna(0).tolist(), i0, len(df), eq0, p,
                   size=sig["size"].tolist())
    ppy = wfm.periods_per_year(df)
    st = summarize(r.equity, r.trades, ppy, eq0)
    st["exposure"] = r.bars_in_market / (len(df) - i0)
    st["killed"] = r.killed_at is not None
    st["costs"] = r.fees + r.slippage + r.funding
    idx = df.index[i0:]
    bh = eq0 * df["close"].to_numpy()[i0:] / df["open"].to_numpy()[i0]
    return idx, r.equity, st, bh, summarize(bh, [], ppy, eq0)


def yearly(idx, eq, eq0):
    """연도별 수익률: (그해 마지막 자산 / 전해 마지막 자산) - 1. 첫해는 시작 자본 기준."""
    end = pd.Series(np.asarray(eq, float), index=idx).groupby(idx.year).last()
    start = end.shift(1).fillna(eq0)
    return end / start - 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    eq0 = cfg["account"]["initial_equity"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = Path("reports") / f"{stamp}_robustness{'_synthetic' if args.synthetic else ''}"
    out.mkdir(parents=True, exist_ok=True)
    base_txt = f"lookbacks={BASE['lookbacks']}, short_mode={BASE['short_mode']}, atr_mult={BASE['atr_mult']}"
    print(f"검사 대상: {TF} tsmom_ensemble ({base_txt})\n")

    # ---------- 1·2·5) 종목별 고정 설정 ----------
    dfs, rows, curves, years = {}, [], {}, {}
    for k, sym in enumerate(SYMBOLS):
        try:
            df = data.resample(load_symbol(cfg, sym, args.synthetic, seed=7 + k), TF)
        except Exception as e:
            print(f"  {sym} 건너뜀: {e}")
            continue
        dfs[sym] = df
        idx, eq, st, bh, bst = run_fixed(df, cfg, BASE)
        curves[sym] = (idx, eq, bh)
        years[sym] = yearly(idx, eq, eq0)
        better = st["sharpe"] > bst["sharpe"] and st["max_drawdown"] > bst["max_drawdown"]
        rows.append((sym, idx, st, bst, better))
        print(f"{sym:<15} {idx[0]:%Y-%m}~  수익 {st['total_return']:+8.1%} (보유 {bst['total_return']:+8.1%})  "
              f"낙폭 {st['max_drawdown']:6.1%} (보유 {bst['max_drawdown']:6.1%})  샤프 {st['sharpe']:5.2f} (보유 {bst['sharpe']:5.2f})"
              f"{'  ⛔비상정지' if st['killed'] else ''}")

    # ---------- 3) 주변 설정 (모든 종목) ----------
    print("\n주변 설정 검사 중...")
    grid = list(wfm.param_grid(NEIGHBORS))
    sens = []
    for prm in grid:
        srs = {sym: run_fixed(df, cfg, prm)[2]["sharpe"] for sym, df in dfs.items()}
        sens.append((prm, srs))

    # ---------- 4) 비용 스트레스 (모든 종목) ----------
    stress = {}
    for label, fee_x, slip_x in [("기본", 1, 1), ("수수료 2배", 2, 1), ("수수료 2배 + 슬리피지 3배", 2, 3)]:
        c = copy.deepcopy(cfg)
        c["costs"]["taker_fee"] *= fee_x
        c["costs"]["slippage_bps"] *= slip_x
        p = wfm.make_params(c)
        stress[label] = {sym: run_fixed(df, cfg, BASE, p)[2] for sym, df in dfs.items()}

    # ---------- 판정 ----------
    n = len(rows)
    n_better = sum(r[4] for r in rows)
    n_pos = sum(r[2]["total_return"] > 0 for r in rows)
    all_sr = [s for _, srs in sens for s in srs.values()]
    frac_sens = float(np.mean([s > 0.3 for s in all_sr])) if all_sr else 0
    worst = stress["수수료 2배 + 슬리피지 3배"]
    n_stress = sum(s["sharpe"] > 0 for s in worst.values())
    checks = [
        ("다른 종목에서도 보유보다 위험 대비 우수 (샤프↑·낙폭↓)", n_better >= max(1, int(np.ceil(n * 0.6))), f"{n_better}/{n} 종목"),
        ("수익이 플러스인 종목", n_pos >= max(1, int(np.ceil(n * 0.6))), f"{n_pos}/{n} 종목"),
        ("주변 설정에서도 샤프 0.3 이상 유지", frac_sens >= 0.6, f"{frac_sens:.0%} (설정 {len(grid)}개 × 종목 {n}개)"),
        ("비용 최악 조건에서도 샤프 플러스", n_stress >= max(1, int(np.ceil(n * 0.6))), f"{n_stress}/{n} 종목"),
    ]
    passed = sum(c[1] for c in checks)

    # ---------- 리포트 ----------
    L = [f"# 견고성 검사 ({stamp})\n"]
    if args.synthetic:
        L.append("> ⚠️ 가짜 데이터 결과 (동작 확인용)\n")
    L.append(f"- 대상: **{TF} tsmom_ensemble** — {base_txt}")
    L.append(f"- 최적화 없이 이 설정 하나로 각 종목의 전체 기간 실행 (워밍업 {cfg['walkforward']['warmup_days']}일 제외)")
    r_ = cfg["risk"]
    L.append(f"- 위험 설정: 최대 {r_['leverage']}배, 목표 변동성 {r_['target_vol']:.0%}, 비상정지 {r_['max_drawdown_kill']:.0%}\n")
    L.append(f"## 판정: {passed}/4 통과 → " + ("**모의매매 진행 권장**" if passed >= 3 else "**보완 필요**") + "\n")
    L.append("| 항목 | 결과 | 기준 충족 |")
    L.append("|---|---|---|")
    for name, ok, val in checks:
        L.append(f"| {name} | {val} | {'✅' if ok else '❌'} |")
    L.append("")

    L.append("## 종목별 결과 (고정 설정)\n")
    L.append("| 종목 | 시작 | 총수익률 | 보유 수익률 | 최대낙폭 | 보유 낙폭 | 샤프 | 보유 샤프 | 거래 | 노출 | 비고 |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for sym, idx, st, bst, better in rows:
        note = ("BTC: 전략 고른 종목" if sym.startswith("BTC") else "처음 적용") + (" ⛔비상정지" if st["killed"] else "")
        L.append(f"| {sym.split('/')[0]} | {idx[0]:%Y-%m} | {st['total_return']:+.0%} | {bst['total_return']:+.0%} | "
                 f"{st['max_drawdown']:.0%} | {bst['max_drawdown']:.0%} | {st['sharpe']:.2f} | {bst['sharpe']:.2f} | "
                 f"{st['trades']} | {st['exposure']:.0%} | {note} |")
    L.append("")

    L.append("## 연도별 수익률\n")
    ys = sorted({y for s in years.values() for y in s.index})
    L.append("| 종목 | " + " | ".join(str(y) for y in ys) + " |")
    L.append("|---|" + "---:|" * len(ys))
    for sym, s in years.items():
        L.append(f"| {sym.split('/')[0]} | " + " | ".join(f"{s[y]:+.0%}" if y in s.index else "-" for y in ys) + " |")
    L.append("\n(첫해·마지막 해는 일부 기간만 포함)\n")

    L.append("## 주변 설정 민감도 (샤프)\n")
    L.append("기준 설정 하나만 좋고 주변이 나쁘면 과최적화예요. 대부분 플러스면 견고한 거예요.\n")
    syms = list(dfs)
    L.append("| lookbacks | short | atr | " + " | ".join(s.split("/")[0] for s in syms) + " | 평균 |")
    L.append("|---|---|---:|" + "---:|" * (len(syms) + 1))
    for prm, srs in sorted(sens, key=lambda x: -np.mean(list(x[1].values()))):
        mark = " ⬅ 기준" if prm == BASE else ""
        L.append(f"| {prm['lookbacks']} | {prm['short_mode']} | {prm['atr_mult']} | "
                 + " | ".join(f"{srs[s]:.2f}" for s in syms) + f" | {np.mean(list(srs.values())):.2f}{mark} |")
    L.append("")

    L.append("## 비용 스트레스\n")
    L.append("| 조건 | " + " | ".join(f"{s.split('/')[0]} 수익 / 샤프" for s in syms) + " |")
    L.append("|---|" + "---|" * len(syms))
    for label, res in stress.items():
        L.append(f"| {label} | " + " | ".join(f"{res[s]['total_return']:+.0%} / {res[s]['sharpe']:.2f}" for s in syms) + " |")
    L.append("\n![equity](equity.png)\n")

    fig, axes = plt.subplots(len(curves), 1, figsize=(11, 2.6 * len(curves)), squeeze=False)
    for ax, (sym, (idx, eq, bh)) in zip(axes[:, 0], curves.items()):
        ax.plot(idx, eq, lw=1.3, label="strategy")
        ax.plot(idx, bh, lw=1, ls="--", color="gray", label="buy & hold")
        ax.set_yscale("log")
        ax.set_title(sym, fontsize=10, loc="left")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(out / "equity.png", dpi=105)
    plt.close(fig)

    (out / "report.md").write_text("\n".join(L), encoding="utf-8")
    print(f"\n판정: {passed}/4 통과")
    for name, ok, val in checks:
        print(f"  {'✅' if ok else '❌'} {name}: {val}")
    print(f"\n리포트: {out / 'report.md'}")


if __name__ == "__main__":
    main()
