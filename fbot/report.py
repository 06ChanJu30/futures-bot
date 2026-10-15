from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def _pct(x):
    return "-" if x is None else f"{x:+.1%}"


def verdict(r, cfg):
    v, s = cfg["verdict"], r["stats"]
    reasons = []
    if s["sharpe"] < v["min_sharpe"]:
        reasons.append(f"샤프 {s['sharpe']:.2f} < {v['min_sharpe']}")
    if r["dsr"] < v["min_dsr"]:
        reasons.append(f"운 아닐 확률 {r['dsr']:.0%} < {v['min_dsr']:.0%}")
    if s["max_drawdown"] < -v["max_drawdown"]:
        reasons.append(f"낙폭 {s['max_drawdown']:.0%}")
    if s["trades"] < v["min_trades"]:
        reasons.append(f"거래 {s['trades']}회 부족")
    if r["costs"].get("killed"):
        reasons.append("비상정지 발동")
    return ("통과 ✅", "") if not reasons else ("탈락", ", ".join(reasons))


def _chart(path, tf, results, bh_idx, bh_eq, synthetic):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    for r in results:
        ax1.plot(r["index"], r["equity"], lw=1.3, label=r["name"])
        s = pd.Series(r["equity"])
        ax2.plot(r["index"], (s / s.cummax() - 1) * 100, lw=1)
    ax1.plot(bh_idx, bh_eq, lw=1, color="gray", ls="--", label="buy & hold (1x)")
    ax1.set_yscale("log")
    ax1.set_ylabel("Equity (USDT, log)")
    ax1.set_title(f"{tf} walk-forward out-of-sample equity" + (" [SYNTHETIC DATA]" if synthetic else ""))
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(alpha=0.3)
    ax2.set_ylabel("Drawdown %")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def write(by_tf, bh, cfg, sr0, synthetic=False, out_root="reports"):
    """by_tf: {tf: [result...]}, bh: {tf: (index, eq, stats)}"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = Path(out_root) / (f"{stamp}_synthetic" if synthetic else stamp)
    out.mkdir(parents=True, exist_ok=True)
    eq0 = cfg["account"]["initial_equity"]
    allr = [r for rs in by_tf.values() for r in rs]
    allr.sort(key=lambda r: r["dsr"], reverse=True)

    L = [f"# 백테스트 리포트 ({stamp})\n"]
    if synthetic:
        L.append("> ⚠️ **가짜(합성) 데이터 결과**입니다. 프로그램 동작 확인용이고 수익률에는 의미가 없어요.\n")
    d, r_ = cfg["data"], cfg["risk"]
    any_tf = next(iter(bh))
    L.append(f"- 종목: {d['exchange']} {d['symbol']} / 검증 봉: {', '.join(by_tf)}")
    L.append(f"- 검증 기간: {bh[any_tf][0][0]:%Y-%m-%d} ~ {bh[any_tf][0][-1]:%Y-%m-%d} (학습 {cfg['walkforward']['train_months']}개월 → 검증 {cfg['walkforward']['test_months']}개월씩 이동)")
    sizing = f"목표 변동성 {r_['target_vol']:.0%}" if r_["sizing"] == "vol_target" else f"1회 위험 {r_['risk_per_trade']:.1%}"
    L.append(f"- 최대 레버리지 {r_['leverage']}배, 포지션 크기: {sizing}, 추적 손절 {'켬' if r_['trailing_stop'] else '끔'}, 일일 손실 한도 {r_['daily_max_loss']:.0%}, 비상정지 {r_['max_drawdown_kill']:.0%}")
    L.append(f"- 시작 자본 {eq0:,} USDT, 수수료 {cfg['costs']['taker_fee']:.2%}, 슬리피지 {cfg['costs']['slippage_bps']}bp, 펀딩비 반영")
    L.append(f"- 비교한 후보: {len(allr)}개 (봉 주기 × 전략), 내부에서 시험한 설정 조합 총 {sum(r['n_trials'] for r in allr)}개\n")

    passed = [r for r in allr if r["verdict"][0].startswith("통과")]
    L.append("## 결론\n")
    if passed:
        L.append("모의매매 후보: " + ", ".join(f"**{r['tf']} {r['name']}**" for r in passed) + "\n")
    else:
        L.append("**통과한 후보 없음.** 아래 표에서 가장 가까운 후보와 탈락 사유를 확인하세요.\n")

    L.append("## 전체 순위 (검증 구간만 이어 붙인 성적, '운 아닐 확률' 순)\n")
    L.append("| 봉 | 전략 | 총수익률 | 연환산 | 최대낙폭 | 샤프 | 운 아닐 확률(DSR) | 거래수 | 시장 노출 | 판정 |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for r in allr:
        s = r["stats"]
        v, why = r["verdict"]
        L.append(f"| {r['tf']} | {r['name']} | {_pct(s['total_return'])} | {_pct(s['cagr'])} | {s['max_drawdown']:.1%} | "
                 f"{s['sharpe']:.2f} | {r['dsr']:.0%} | {s['trades']} | {s['exposure']:.0%} | {v} {why} |")
    for tf, (_, _, b) in bh.items():
        L.append(f"| {tf} | 단순 보유(1배) | {_pct(b['total_return'])} | {_pct(b['cagr'])} | {b['max_drawdown']:.1%} | {b['sharpe']:.2f} | - | - | 100% | 비교 기준 |")
    L.append("")
    L.append("**읽는 법**")
    L.append("- 모든 숫자는 각 시점에 *과거만 보고* 고른 설정으로 거래한 결과예요 (워크포워드).")
    L.append(f"- '운 아닐 확률(DSR)'은 후보 {len(allr)}개 중 제일 좋은 걸 고르는 효과까지 감안한 값이에요. 95% 이상이면 우연일 가능성 5% 미만.")
    L.append("- '시장 노출'은 포지션을 들고 있던 시간 비율이에요. 낮을수록 같은 수익이면 더 효율적이에요.\n")

    L.append("## 비용 분석 (검증 구간 합계, USDT)\n")
    L.append("| 봉 | 전략 | 수수료 | 슬리피지 | 펀딩비 | 비용 합계 | 비용 차감 전 손익 | 강제청산 | 일일한도 발동 |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in allr:
        c = r["costs"]
        cost = c.get("fees", 0) + c.get("slippage", 0) + c.get("funding", 0)
        net = r["stats"]["final_equity"] - eq0
        L.append(f"| {r['tf']} | {r['name']} | {c.get('fees', 0):,.0f} | {c.get('slippage', 0):,.0f} | {c.get('funding', 0):,.0f} | "
                 f"{cost:,.0f} | {net + cost:+,.0f} | {c.get('liquidations', 0)} | {c.get('daily_halts', 0)} |")
    L.append("\n'비용 차감 전 손익'이 플러스인데 최종이 마이너스면, 전략은 방향을 맞혔지만 거래가 너무 잦다는 뜻이에요.\n")

    L.append("## 과최적화 점검\n")
    L.append("| 봉 | 전략 | 검증 수익률 | 전체기간 최적화 시(비현실적) | 같은 설정이 선택된 비율 | 가장 자주 고른 설정 |")
    L.append("|---|---|---:|---:|---:|---|")
    for r in allr:
        L.append(f"| {r['tf']} | {r['name']} | {_pct(r['stats']['total_return'])} | {_pct(r['in_sample_best'])} | {r['stability']:.0%} | {r['top_params']} |")
    L.append("\n- 두 수익률 차이가 클수록, 선택 비율이 낮을수록(구간마다 설정이 바뀜) 과거에 끼워 맞춘 결과일 가능성이 커요.\n")

    for tf, rs in by_tf.items():
        _chart(out / f"equity_{tf}.png", tf, rs, bh[tf][0], bh[tf][1], synthetic)
        L.append(f"![{tf}](equity_{tf}.png)\n")

    D = [f"# 구간별 상세 ({stamp})\n"]
    for r in allr:
        D.append(f"## {r['tf']} {r['name']}\n")
        D.append("| 학습 구간 | 검증 구간 | 고른 설정 | 학습 수익률 | 검증 수익률 |")
        D.append("|---|---|---|---:|---:|")
        for w in r["windows"]:
            D.append(f"| {w['train']} | {w['test']} | {w['params']} | {_pct(w['train_return'])} | {_pct(w['test_return'])} |")
        if r["trades"]:
            t = pd.DataFrame(r["trades"])
            t.to_csv(out / f"trades_{r['tf']}_{r['name']}.csv", index=False)
            D.append("\n청산 사유: " + ", ".join(f"{k} {v}회" for k, v in t["reason"].value_counts().items()))
        D.append("")
    L.append("구간별 선택 설정과 거래 내역: `details.md`, `trades_*.csv`")

    (out / "report.md").write_text("\n".join(L), encoding="utf-8")
    (out / "details.md").write_text("\n".join(D), encoding="utf-8")
    return out
