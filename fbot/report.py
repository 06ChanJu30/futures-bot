from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def _pct(x):
    return "-" if x is None else f"{x:+.1%}"


def write(results, bh_eq, bh_stats, cfg, synthetic=False, out_root="reports"):
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = Path(out_root) / (f"{stamp}_synthetic" if synthetic else stamp)
    out.mkdir(parents=True, exist_ok=True)
    idx = results[0]["index"]

    # 자산 곡선 차트
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    for r in results:
        ax1.plot(r["index"], r["equity"], lw=1.3, label=r["name"])
        s = pd.Series(r["equity"])
        ax2.plot(r["index"], (s / s.cummax() - 1) * 100, lw=1)
    ax1.plot(idx, bh_eq, lw=1, color="gray", ls="--", label="buy & hold (1x)")
    ax1.set_ylabel("Equity (USDT)")
    ax1.set_title("Walk-forward out-of-sample equity" + (" [SYNTHETIC DATA]" if synthetic else ""))
    ax1.legend(loc="upper left")
    ax1.grid(alpha=0.3)
    ax2.set_ylabel("Drawdown %")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "equity.png", dpi=120)
    plt.close(fig)

    d = cfg["data"]
    L = []
    L.append(f"# 백테스트 리포트 ({stamp})\n")
    if synthetic:
        L.append("> ⚠️ **가짜(합성) 데이터 결과**입니다. 프로그램이 돌아가는지 확인하는 용도이고, 수익률에는 의미가 없어요.\n")
    L.append(f"- 종목: {d['exchange']} {d['symbol']} / {d['timeframe']}")
    L.append(f"- 검증 기간: {idx[0]:%Y-%m-%d} ~ {idx[-1]:%Y-%m-%d} (학습 {cfg['walkforward']['train_months']}개월 → 검증 {cfg['walkforward']['test_months']}개월씩 이동)")
    r_ = cfg["risk"]
    L.append(f"- 레버리지 최대 {r_['leverage']}배, 1회 위험 {r_['risk_per_trade']:.1%}, 일일 손실 한도 {r_['daily_max_loss']:.0%}, 비상정지 낙폭 {r_['max_drawdown_kill']:.0%}")
    L.append(f"- 시작 자본 {cfg['account']['initial_equity']:,} USDT, 수수료 {cfg['costs']['taker_fee']:.2%}, 슬리피지 {cfg['costs']['slippage_bps']}bp, 펀딩비 반영\n")

    L.append("## 요약 (검증 구간만 이어 붙인 성적)\n")
    L.append("| 전략 | 총수익률 | 연환산 | 최대낙폭 | 샤프 | 승률 | 손익비(PF) | 거래수 | 참고: 전체기간 최적화 시 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in results:
        s = r["stats"]
        L.append(
            f"| {r['name']} | {_pct(s['total_return'])} | {_pct(s['cagr'])} | {s['max_drawdown']:.1%} | "
            f"{s['sharpe']:.2f} | {s['win_rate']:.0%} | {s['profit_factor']:.2f} | {s['trades']} | {_pct(r['in_sample_best'][0])} |"
        )
    b = bh_stats
    L.append(f"| 단순 보유(현물 1배) | {_pct(b['total_return'])} | {_pct(b['cagr'])} | {b['max_drawdown']:.1%} | {b['sharpe']:.2f} | - | - | - | - |")
    L.append("")
    L.append("**읽는 법**")
    L.append("- '총수익률'은 각 시점에 *과거만 보고* 고른 파라미터로 거래한 결과라 실전에 가까운 숫자예요.")
    L.append("- 맨 오른쪽 '전체기간 최적화 시'는 정답을 보고 고른 경우라 실전에선 나올 수 없는 숫자예요. 이것과 총수익률의 차이가 클수록 과최적화가 심한 거예요.")
    L.append("- 샤프 1 이상, 최대낙폭이 감당 가능한 수준, 단순 보유보다 위험 대비 나은지를 함께 보세요.\n")
    L.append("![equity](equity.png)\n")

    for r in results:
        L.append(f"## {r['name']} — 구간별 선택 파라미터\n")
        L.append("| 학습 구간 | 검증 구간 | 고른 파라미터 | 학습 수익률 | 검증 수익률 |")
        L.append("|---|---|---|---:|---:|")
        for w in r["windows"]:
            L.append(f"| {w['train']} | {w['test']} | {w['params']} | {_pct(w['train_return'])} | {_pct(w['test_return'])} |")
        L.append("")
        if r["trades"]:
            t = pd.DataFrame(r["trades"])
            t.to_csv(out / f"trades_{r['name']}.csv", index=False)
            reasons = t["reason"].value_counts().to_dict()
            L.append("청산 사유: " + ", ".join(f"{k} {v}회" for k, v in reasons.items()) + f" (상세: trades_{r['name']}.csv)\n")

    (out / "report.md").write_text("\n".join(L), encoding="utf-8")
    return out
