"""LEVIA 코어 알림 + 서포터 가이드북 규칙 시뮬레이션 (매매 없음)

LEVIA 알림에는 '종료' 신호가 없어서, 가이드북의 운용 원칙이 곧 전략이에요.
그 원칙을 코드로 옮겨서 거래소 1분봉으로 '그대로 했으면 어땠을지' 계산합니다.

가이드북 규칙 (방장 개인 운용 예시)
  1. 비트코인만, 레버리지 10배 고정
  2. 유형 L → 기준가에 지정가 롱 (S → 지정가 숏)
  3. 체결 전에 마지막 중단선(롱=가장 높은 선, 숏=가장 낮은 선)에 닿으면 진입 포기
  4. 새 같은 방향 알림이 오면 미체결 주문을 새 기준가로 교체
  5. 1차 50% 진입 → 새 알림 기준가가 평단보다 유리하면 2차 진입(100%)
  6. 손절: 시드 대비 -2~-8% 중 선택 / 익절: 손절폭 이상 (손익비 1:1+)
  7. 2회 연속 손절 → 그날 매매 종료
  8. 익절 수익의 50% 출금, 나머지는 복리
  (BTC·알트 같은 방향 알림 상호 참고는 cross_ref 실험으로)

가이드북에 없는 부분(반대 알림 처리, 미체결 주문 유효시간 등)은 config.yaml에 가정으로 두고 여러 조합을 비교해요.

  python signals/levia_sim.py                   # 기록된 LEVIA 알림으로 실행
  python signals/levia_sim.py --since 2026-09-01
  python signals/levia_sim.py --demo            # 가짜 알림·시세로 동작 확인
"""
import argparse
import itertools
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from importer import load  # noqa: E402
from score import Prices  # noqa: E402
from sigparse import KST, Signal  # noqa: E402


@dataclass
class Alert:
    t: pd.Timestamp          # 주문을 넣을 수 있는 시각 (알림 시각 + 지연)
    side: int                # +1 롱, -1 숏
    price: float | None      # 기준가 (None이면 시장가 즉시 진입 = cross_ref 가상 알림)
    cancel: float | None     # 마지막 중단선
    src: str = ""


@dataclass
class Order:
    side: int
    price: float | None
    cancel: float | None
    frac: float
    created: pd.Timestamp
    is_add: bool
    first_bar: bool = True


@dataclass
class Position:
    side: int
    qty: float
    avg: float
    e0: float                # 1차 진입 시점 거래 자본 (손절·익절 금액 기준)
    opened: pd.Timestamp
    frac: float
    fees: float = 0.0
    adds: int = 0
    mae: float = 0.0


@dataclass
class Result:
    trades: list = field(default_factory=list)
    equity: float = 0.0
    withdrawn: float = 0.0
    curve: list = field(default_factory=list)
    n: dict = field(default_factory=lambda: {k: 0 for k in (
        "alerts", "orders", "fills", "cancel_line", "expired", "replaced", "opp_cancel", "adds", "halted_days", "skipped_halt")})


def simulate(bars: pd.DataFrame, alerts: list[Alert], P: dict, C: dict) -> Result:
    """bars: 1분봉 (index=UTC 시각). P: 이번 조합(stop_seed, tp_ratio, on_opposite). C: levia 공통 설정."""
    T = bars.index
    O, H, L, Cl = (bars[k].to_numpy() for k in ("open", "high", "low", "close"))
    kst_day = (T + pd.Timedelta(hours=9)).strftime("%Y-%m-%d").to_numpy()
    lev, fm, f1 = C["leverage"], C["full_position_margin"], C["first_entry"]
    mk, tk, sl_ = C["maker_fee"], C["taker_fee"], C["slippage"]
    max_age = pd.Timedelta(hours=C["order_max_hours"])
    max_hold = pd.Timedelta(hours=C["max_hold_hours"])
    R = Result(equity=C["start_equity"])
    R.curve.append((T[0], R.equity, 0.0))
    order: Order | None = None
    pos: Position | None = None
    streak, streak_day, halted_day = 0, None, None

    alerts = sorted(alerts, key=lambda a: a.t)
    starts = np.searchsorted(T.values, np.array([a.t.to_datetime64() for a in alerts]))
    ai = 0
    i = int(starts[0]) if len(starts) else len(T)

    def close(i, px, reason, fee_rate, slip=True):
        nonlocal pos, streak, streak_day, halted_day, order
        d = pos.side
        fill = px * (1 - d * sl_) if slip else px
        gross = d * pos.qty * (fill - pos.avg)
        fee = pos.qty * fill * fee_rate
        pnl = gross - fee - pos.fees          # 진입 수수료(1·2차)까지 포함한 거래 손익
        R.equity += pnl
        if pnl > 0:
            w = pnl * C["withdraw_ratio"]
            R.withdrawn += w
            R.equity -= w
        R.trades.append({"open": pos.opened, "close": T[i], "side": "long" if d == 1 else "short", "avg": pos.avg,
                         "exit": fill, "qty": pos.qty, "frac": pos.frac, "adds": pos.adds, "pnl": pnl,
                         "ret_seed": pnl / pos.e0, "reason": reason, "mae_price": pos.mae,
                         "hold_min": (T[i] - pos.opened).total_seconds() / 60})
        R.curve.append((T[i], R.equity, R.withdrawn))
        day = kst_day[i]
        if streak_day != day:
            streak, streak_day = 0, day
        if reason == "stop":
            streak += 1
            if streak >= C["consecutive_stop_limit"] and halted_day != day:
                halted_day = day
                R.n["halted_days"] += 1
                order = None
        elif pnl > 0:
            streak = 0
        pos = None

    def fill(i, px, fee_rate, slip):
        nonlocal pos, order
        o = order
        d = o.side
        px = px * (1 + d * sl_) if slip else px
        margin_full = R.equity * fm if pos is None else pos.e0 * fm
        qty = margin_full * o.frac * lev / px
        fee = qty * px * fee_rate
        R.n["fills"] += 1
        if pos is None:
            pos = Position(side=d, qty=qty, avg=px, e0=R.equity, opened=T[i], frac=o.frac, fees=fee)
        else:
            pos.avg = (pos.avg * pos.qty + px * qty) / (pos.qty + qty)
            pos.qty += qty
            pos.frac += o.frac
            pos.fees += fee
            pos.adds += 1
            R.n["adds"] += 1
        order = None

    while i < len(T):
        t = T[i]
        # 1) 이 시각까지 도착한 알림 처리
        while ai < len(alerts) and starts[ai] <= i:
            a = alerts[ai]
            ai += 1
            R.n["alerts"] += 1
            if halted_day == kst_day[i]:
                R.n["skipped_halt"] += 1
                continue
            if pos is None:
                if order is not None:
                    R.n["replaced"] += 1
                order = Order(a.side, a.price, a.cancel, f1, t, False)
                R.n["orders"] += 1
            elif a.side == pos.side:
                better = a.price is not None and (a.price - pos.avg) * pos.side < 0
                if C["add_when_better"] and pos.frac < 0.999 and better:
                    if order is not None:
                        R.n["replaced"] += 1
                    order = Order(a.side, a.price, a.cancel, 1 - pos.frac, t, True)
                    R.n["orders"] += 1
            else:  # 반대 방향 알림
                if P["on_opposite"] in ("cancel_pending", "close") and order is not None:
                    order = None
                    R.n["opp_cancel"] += 1
                if P["on_opposite"] == "close":
                    close(i, O[i], "opposite", tk)
                    if halted_day != kst_day[i]:
                        order = Order(a.side, a.price, a.cancel, f1, t, False)
                        R.n["orders"] += 1

        o, h, l, c = O[i], H[i], L[i], Cl[i]
        filled_now = False
        # 2) 미체결 주문
        if order is not None:
            od = order
            if t - od.created > max_age:
                order = None
                R.n["expired"] += 1
            elif od.price is None:  # 시장가 즉시 진입 (cross_ref)
                fill(i, o, tk, True)
                filled_now = True
            else:
                d = od.side
                if od.first_bar and od.cancel is not None and (o - od.cancel) * d >= 0:
                    order = None  # 알림 도착 시 이미 마지막 중단선을 지남
                    R.n["cancel_line"] += 1
                elif od.first_bar and (o - od.price) * d <= 0:
                    fill(i, o, tk, True)  # 이미 기준가보다 유리 → 즉시 체결 (시장가)
                    filled_now = True
                else:
                    od.first_bar = False
                    hit_cancel = od.cancel is not None and ((h if d == 1 else l) - od.cancel) * d >= 0
                    hit_fill = ((l if d == 1 else h) - od.price) * d <= 0
                    if hit_cancel:  # 같은 봉에서 둘 다 닿으면 보수적으로 '취소'
                        order = None
                        R.n["cancel_line"] += 1
                    elif hit_fill:
                        fill(i, od.price, mk, False)
                        filled_now = True
        # 3) 포지션: 손절 / 익절 / 강제청산 / 최대 보유시간
        if pos is not None:
            d = pos.side
            loss_amt = P["stop_seed"] * pos.e0
            sl_px = pos.avg - d * loss_amt / pos.qty
            tp_px = pos.avg + d * loss_amt * P["tp_ratio"] / pos.qty
            liq_px = pos.avg * (1 - d * (1 / lev - C["maintenance_margin"]))
            worst = l if d == 1 else h
            pos.mae = max(pos.mae, -d * (worst / pos.avg - 1))
            stop_line = max(sl_px, liq_px) if d == 1 else min(sl_px, liq_px)
            if (worst - stop_line) * d <= 0:
                is_liq = (stop_line == liq_px) and (liq_px - sl_px) * d > 0
                px = min(o, stop_line) if d == 1 else max(o, stop_line)
                close(i, px, "liquidation" if is_liq else "stop", tk)
            elif not filled_now and (((h if d == 1 else l) - tp_px) * d >= 0):
                px = max(o, tp_px) if d == 1 else min(o, tp_px)
                close(i, px, "take_profit", mk, slip=False)
            elif t - pos.opened > max_hold:
                close(i, c, "time_limit", tk)
        # 다음 봉으로 (할 일이 없으면 다음 알림까지 건너뜀)
        if pos is None and order is None:
            if ai < len(alerts):
                i = max(i + 1, int(starts[ai]))
            else:
                break
        else:
            i += 1
    if pos is not None:
        close(len(T) - 1, Cl[-1], "end", tk)
    return R


def summarize(R: Result, start):
    tr = pd.DataFrame(R.trades)
    wealth = np.array([start] + [e + w for _, e, w in R.curve[1:]])
    peak = np.maximum.accumulate(wealth)
    out = {"trades": len(tr), "final_equity": R.equity, "withdrawn": R.withdrawn,
           "wealth": R.equity + R.withdrawn, "total": (R.equity + R.withdrawn) / start - 1,
           "mdd": float((wealth / peak - 1).min()) if len(wealth) else 0.0}
    if len(tr):
        out.update({"win": float((tr.pnl > 0).mean()), "stops": int((tr.reason == "stop").sum()),
                    "tps": int((tr.reason == "take_profit").sum()), "liq": int((tr.reason == "liquidation").sum()),
                    "avg_hold": float(tr.hold_min.median()),
                    "pf": float(tr.loc[tr.pnl > 0, "pnl"].sum() / -tr.loc[tr.pnl <= 0, "pnl"].sum()) if (tr.pnl <= 0).any() else float("inf")})
    else:
        out.update({"win": 0.0, "stops": 0, "tps": 0, "liq": 0, "avg_hold": 0.0, "pf": 0.0})
    return out


def to_alerts(sigs: list[Signal], delay):
    return [Alert(pd.Timestamp(s.signal_time) + pd.Timedelta(seconds=delay), 1 if s.side == "long" else -1,
                  s.price, s.cancel_level, s.source or "") for s in sigs]


def get_bars(prices, pair, t0, t1):
    return prices.window(pair, t0 - pd.Timedelta(minutes=2), t1)


def pct(x, nd=1):
    return "-" if x is None else f"{x * 100:+.{nd}f}%"


def lvl(x):
    """손절·익절 폭 표시 (7.5% → 7.5%, 5% → 5%)"""
    return f"{round(x * 100, 2):g}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(HERE / "config.yaml", encoding="utf-8"))
    C = cfg["levia"]
    now = pd.Timestamp.now(tz="UTC").floor("min")

    if args.demo:
        sigs, prices = demo_data()
    else:
        sigs = [s for s in load() if s.kind == "alert"]
        prices = Prices(cfg["price_exchange"])
        if not sigs:
            sys.exit("기록된 LEVIA 알림이 없어요. importer.py 나 listen.py 로 먼저 모아주세요.")
    if args.since:
        t0 = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        sigs = [s for s in sigs if s.ts >= t0]
    by_sym = {}
    for s in sigs:
        by_sym.setdefault(s.symbol, []).append(s)
    targets = [x for x in C["symbols"] if x in by_sym]
    if not targets:
        sys.exit(f"{C['symbols']} 알림이 없어요. 기록된 종목: {sorted(by_sym)}")

    t_first = pd.Timestamp(min(s.signal_time for s in sigs))
    t_last = pd.Timestamp(max(s.signal_time for s in sigs))
    t_end = min(now, t_last + pd.Timedelta(hours=C["max_hold_hours"] + C["order_max_hours"]))
    start = C["start_equity"]
    combos = [dict(zip(C["grid"], v)) for v in itertools.product(*C["grid"].values())]

    results = {}
    for sym in targets:
        print(f"{sym} 1분봉 준비 중 ({t_first:%m-%d} ~ {t_end:%m-%d})...")
        bars = get_bars(prices, f"{sym}/USDT:USDT", t_first, t_end)
        sides = {"long": 1, "short": -1}
        allowed = {sides[x] for x in C.get("sides", ["long", "short"])}
        own = [a for a in to_alerts(by_sym[sym], C["delay_sec"]) if a.side in allowed]
        alts = [a for x, ss in by_sym.items() if x != sym for a in to_alerts(ss, C["delay_sec"]) if a.side in allowed]
        cross = [Alert(a.t, a.side, None, None, "cross:" + a.src) for a in alts]
        for P in combos:
            al = own + (cross if P["cross_ref"] else [])
            R = simulate(bars, al, P, C)
            results[(sym, tuple(P.items()))] = (P, R, summarize(R, start))
        print(f"  {len(combos)}개 조합 완료")

    alt_rows = []
    if C.get("also_test_alts"):
        for sym, ss in by_sym.items():
            if sym in targets:
                continue
            try:
                b = get_bars(prices, f"{sym}/USDT:USDT", pd.Timestamp(min(s.signal_time for s in ss)), t_end)
            except Exception as e:
                print(f"  {sym} 건너뜀: {e}")
                continue
            R = simulate(b, to_alerts(ss, C["delay_sec"]), {**C["base"], "cross_ref": False}, C)
            alt_rows.append((sym, len(ss), summarize(R, start)))

    stamp = now.tz_convert(KST).strftime("%Y%m%d_%H%M")
    out = ROOT / "reports" / f"levia_{stamp}{'_demo' if args.demo else ''}"
    out.mkdir(parents=True, exist_ok=True)

    L = [f"# LEVIA 알림 × 가이드북 규칙 시뮬레이션 ({stamp} KST)\n"]
    if args.demo:
        L.append("> ⚠️ **가짜 알림·시세로 만든 데모**예요. 숫자에는 의미가 없어요.\n")
    n_al = {s: len(v) for s, v in by_sym.items()}
    L.append(f"- 기간: {t_first.tz_convert(KST):%Y-%m-%d} ~ {t_last.tz_convert(KST):%Y-%m-%d}, 알림 수: " + ", ".join(f"{k} {v}" for k, v in sorted(n_al.items(), key=lambda x: -x[1])))
    L.append(f"- 공통: 레버리지 {C['leverage']}배, 100% 진입 증거금 = 자본의 {C['full_position_margin']:.0%}, 1차 {C['first_entry']:.0%}, "
             f"알림 후 {C['delay_sec']}초 뒤 주문, 미체결 {C['order_max_hours']}시간 유지, 최대 보유 {C['max_hold_hours']}시간, "
             f"{C['consecutive_stop_limit']}연속 손절 시 당일 종료, 익절 수익 {C['withdraw_ratio']:.0%} 출금")
    L.append(f"- 비용: 지정가 {C['maker_fee']:.2%}, 시장가 {C['taker_fee']:.2%} + 슬리피지 {C['slippage']:.2%}. "
             "같은 1분봉에서 손절·익절이 둘 다 닿으면 손절, 체결·취소선이 둘 다 닿으면 취소로 처리 (보수적)\n")

    for sym in targets:
        bk = next((k for k in results if k[0] == sym and dict(k[1]) == C["base"]), None)
        P, R, S = results[bk]
        L.append(f"## {sym} — 가이드북 기본 사례 (손절 -{lvl(P['stop_seed'])} / 익절 +{lvl(P['stop_seed'] * P['tp_ratio'])}, 시드 대비)\n")
        L.append("| 항목 | 값 |")
        L.append("|---|---|")
        L.append(f"| 거래 수 | {S['trades']} (익절 {S['tps']} / 손절 {S['stops']} / 강제청산 {S['liq']}) |")
        L.append(f"| 승률 | {S['win']:.0%} |")
        L.append(f"| 최종: 거래 자본 + 출금 누적 | {S['final_equity']:,.0f} + {S['withdrawn']:,.0f} = **{S['wealth']:,.0f} USDT** ({pct(S['total'])}) |")
        L.append(f"| 최대낙폭 (출금 포함 총자산 기준) | {pct(S['mdd'])} |")
        L.append(f"| 손익비 (PF) | {S['pf']:.2f} |")
        L.append(f"| 보유 시간 중간값 | {S['avg_hold']:.0f}분 |")
        n = R.n
        L.append(f"| 주문 흐름 | 알림 {n['alerts']} → 주문 {n['orders']} → 체결 {n['fills']} (2차 진입 {n['adds']}) / "
                 f"중단선 취소 {n['cancel_line']}, 교체 {n['replaced']}, 만료 {n['expired']}, 반대알림 취소 {n['opp_cancel']} |")
        L.append(f"| 연속 손절로 쉰 날 | {n['halted_days']}일 (그날 무시한 알림 {n['skipped_halt']}) |")
        L.append("")
        tr = pd.DataFrame(R.trades)
        if len(tr):
            tr.to_csv(out / f"trades_{sym}_base.csv", index=False, encoding="utf-8-sig")
            L.append(f"- 진입 후 최대 역행폭(가격): 중간값 {tr.mae_price.median() * 100:.2f}%, 최대 {tr.mae_price.max() * 100:.2f}% "
                     f"→ 10배·전액 기준 강제청산선은 약 {(1 / C['leverage'] - C['maintenance_margin']) * 100:.1f}% 역행")
        L.append("")

        L.append(f"### {sym} — 조합 비교 (가이드북이 범위만 주거나 정하지 않은 부분)\n")
        L.append("| 손절(시드) | 익절 | 반대 알림 | 알트 참고 | 거래 | 승률 | 총수익(출금 포함) | 최대낙폭 | PF |")
        L.append("|---:|---:|---|---|---:|---:|---:|---:|---:|")
        opp_kr = {"ignore": "무시", "cancel_pending": "미체결만 취소", "close": "청산"}
        rows = [(P, S) for k, (P, R_, S) in results.items() if k[0] == sym]
        rows.sort(key=lambda x: -x[1]["total"])
        for P, S in rows:
            mark = " ⬅ 기본" if P == C["base"] else ""
            L.append(f"| -{lvl(P['stop_seed'])} | +{lvl(P['stop_seed'] * P['tp_ratio'])} | {opp_kr[P['on_opposite']]} | {'O' if P['cross_ref'] else '-'} | "
                     f"{S['trades']} | {S['win']:.0%} | {pct(S['total'])} | {pct(S['mdd'])} | {S['pf']:.2f}{mark} |")
        pos_share = np.mean([S["total"] > 0 for _, S in rows])
        L.append(f"\n- 수익이 플러스인 조합: **{pos_share:.0%}** ({len(rows)}개 중). 일부 조합만 좋다면 우연일 가능성이 커요 — 표 맨 위 조합을 고르면 과최적화예요.\n")

        fig, ax = plt.subplots(figsize=(11, 4.5))
        cv = R.curve
        ax.plot([x[0] for x in cv], [x[1] + x[2] for x in cv], label="equity + withdrawn")
        ax.plot([x[0] for x in cv], [x[1] for x in cv], ls="--", label="trading equity")
        ax.axhline(start, color="gray", lw=0.8)
        ax.set_title(f"LEVIA {sym} guidebook base case" + (" [DEMO]" if args.demo else ""))
        ax.legend()
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(out / f"equity_{sym}.png", dpi=110)
        plt.close(fig)
        L.append(f"![{sym}](equity_{sym}.png)\n")

    if alt_rows:
        L.append("## 참고: 알트코인 알림에 같은 규칙(기본 사례)을 적용했다면\n")
        L.append("가이드북은 BTC만 거래하지만, 알림의 질을 보는 참고 자료예요.\n")
        L.append("| 종목 | 알림 | 거래 | 승률 | 총수익 | 최대낙폭 |")
        L.append("|---|---:|---:|---:|---:|---:|")
        for sym, na, S in sorted(alt_rows, key=lambda x: -x[1]):
            L.append(f"| {sym} | {na} | {S['trades']} | {S['win']:.0%} | {pct(S['total'])} | {pct(S['mdd'])} |")
        L.append("")
    ntr = max(results[k][2]["trades"] for k in results)
    if ntr < 50:
        L.append(f"> 거래가 최대 {ntr}건뿐이라 결과가 우연일 가능성이 커요. 최소 50~100건은 모아서 판단하세요.")
    (out / "report.md").write_text("\n".join(L), encoding="utf-8")

    for sym in targets:
        bk = next(k for k in results if k[0] == sym and dict(k[1]) == C["base"])
        S = results[bk][2]
        print(f"\n[{sym} 기본 사례] 거래 {S['trades']}, 승률 {S['win']:.0%}, 총수익(출금 포함) {pct(S['total'])}, 최대낙폭 {pct(S['mdd'])}")
    print(f"리포트: {out / 'report.md'}")


# ------------------------------------------------------------------ 데모
class _DemoPrices(Prices):
    def __init__(self, series):
        super().__init__("demo")
        self.series = series

    def window(self, pair, t0, t1):
        df = self.series[pair]
        return df[(df.index >= pd.Timestamp(t0).floor("min")) & (df.index <= pd.Timestamp(t1))]


def demo_data(days=40, seed=5):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-08-01", periods=days * 1440, freq="1min", tz="UTC")
    series, sigs = {}, []
    for sym, p0, every in (("BTC", 80000, 9), ("ETH", 3000, 14), ("SOL", 150, 20)):
        r = rng.normal(0, 0.0007, len(idx)) + np.repeat(rng.normal(0, 0.00004, len(idx) // 720 + 1), 720)[:len(idx)]
        c = p0 * np.exp(np.cumsum(r))
        o = np.concatenate([[c[0]], c[:-1]])
        w = np.abs(rng.normal(0, 0.0004, len(idx))) * c
        df = pd.DataFrame({"open": o, "high": np.maximum(o, c) + w, "low": np.minimum(o, c) - w, "close": c}, index=idx)
        series[f"{sym}/USDT:USDT"] = df
        # 알림: 최근 2시간 저점/고점 근처에서 L/S
        t = idx[300]
        while t < idx[-600]:
            t = (t + pd.Timedelta(hours=float(rng.uniform(every * 0.5, every * 1.5)))).floor("s")
            k = idx.searchsorted(t)
            if k >= len(idx) - 10:
                break
            win = df.iloc[k - 120:k]
            side = "long" if rng.random() < 0.55 else "short"
            base = win["low"].min() if side == "long" else win["high"].max()
            d = 1 if side == "long" else -1
            levels = sorted(base * (1 + d * x) for x in np.sort(rng.uniform(0.002, 0.012, 3)))
            sigs.append(Signal(ts=t.to_pydatetime(), kind="alert", side=side, symbol=sym, pair=f"{sym}/USDT:USDT",
                               price=float(base), levels=levels, calc_time=t.to_pydatetime(), source=f"demo {sym}"))
    return sigs, _DemoPrices(series)


if __name__ == "__main__":
    main()
