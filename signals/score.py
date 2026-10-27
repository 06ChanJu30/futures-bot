"""시그널 채점 — '이 방을 그대로 따라 했으면 실제로 얼마였을까'

signals/data/signals.csv (importer.py 또는 listen.py가 만든 기록)를 읽어서
거래소의 실제 1분봉 시세로 다시 계산해요.

  python signals/score.py                 # 전체 기간
  python signals/score.py --since 2026-09-01
  python signals/score.py --demo          # 가짜 시세로 동작 확인 (예시 시그널 필요 없음)

결과: reports/signals_날짜/report.md, equity.png, trades.csv
"""
import argparse
import sys
import time
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
from sigparse import Signal, Trade, pair_trades  # noqa: E402

CACHE = HERE / "data" / "ohlcv_1m"
MIN = timedelta(minutes=1)


# ------------------------------------------------------------------ 시세
class Prices:
    """1분봉 시세. 날짜(UTC) 단위로 받아서 파일에 저장해두고 재사용."""

    def __init__(self, exchange: str):
        self.exchange_id = exchange
        self._ex = None
        self._mem = {}

    @property
    def ex(self):
        if self._ex is None:
            import ccxt

            self._ex = getattr(ccxt, self.exchange_id)({"enableRateLimit": True, "options": {"defaultType": "swap"}})
            self._ex.load_markets()
        return self._ex

    def _day(self, pair, day):
        key = (pair, day)
        if key in self._mem:
            return self._mem[key]
        safe = pair.replace("/", "").replace(":", "_")
        path = CACHE / self.exchange_id / safe / f"{day:%Y-%m-%d}.csv"
        today = datetime.now(timezone.utc).date()
        if path.exists():
            df = pd.read_csv(path)
        else:
            if pair not in self.ex.markets:
                raise KeyError(f"{self.exchange_id}에 {pair} 선물이 없어요")
            since = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)
            rows, cur = [], since
            while cur < since + 86_400_000:
                b = self.ex.fetch_ohlcv(pair, "1m", since=cur, limit=1000)
                b = [x for x in b if x[0] < since + 86_400_000]
                if not b:
                    break
                rows += b
                cur = b[-1][0] + 60_000
                time.sleep(self.ex.rateLimit / 1000)
            df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts")
            if day < today:  # 오늘 데이터는 아직 안 끝나서 저장 안 함
                path.parent.mkdir(parents=True, exist_ok=True)
                df.to_csv(path, index=False)
        df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
        self._mem[key] = df
        return df

    def window(self, pair, t0, t1):
        t0, t1 = pd.Timestamp(t0), pd.Timestamp(t1)
        days = pd.date_range(t0.date(), t1.date(), freq="D")
        parts = [self._day(pair, d.date()) for d in days]
        df = pd.concat(parts) if parts else pd.DataFrame()
        return df[(df.index >= t0.floor("min")) & (df.index <= t1)]


class DemoPrices(Prices):
    """가짜 시세: 시그널 가격 근처에서 움직이는 랜덤워크 (동작 확인용)"""

    def __init__(self, trades, seed=1):
        super().__init__("demo")
        self.rng = np.random.default_rng(seed)
        self.series = {}
        for t in trades:
            end = t.close.ts if t.close else t.open.ts + timedelta(hours=2)
            idx = pd.date_range(pd.Timestamp(t.open.ts).floor("min") - 5 * MIN, pd.Timestamp(end).floor("min") + 5 * MIN, freq="1min")
            p0, p1 = t.open.price, (t.close.price if t.close else t.open.price)
            n = len(idx)
            path = np.linspace(np.log(p0), np.log(p1), n) + np.cumsum(self.rng.normal(0, 0.0015, n)) * np.sin(np.linspace(0, np.pi, n))
            c = np.exp(path)
            o = np.concatenate([[c[0]], c[:-1]])
            w = np.abs(self.rng.normal(0, 0.0008, n)) * c
            df = pd.DataFrame({"open": o, "high": np.maximum(o, c) + w, "low": np.minimum(o, c) - w, "close": c}, index=idx)
            self.series.setdefault(t.pair, []).append(df)

    def window(self, pair, t0, t1):
        t0, t1 = pd.Timestamp(t0), pd.Timestamp(t1)
        df = pd.concat(self.series[pair]).sort_index()
        df = df[~df.index.duplicated()]
        return df[(df.index >= t0.floor("min")) & (df.index <= t1)]


# ------------------------------------------------------------------ 한 건 계산
def fill_after(df, t, delay):
    """t + 지연 이후 처음 시작하는 1분봉의 시가와 그 시각."""
    after = df[df.index >= t + timedelta(seconds=delay)]
    if after.empty:
        return None, None
    return float(after["open"].iloc[0]), after.index[0]


def score_trade(tr: Trade, prices: Prices, cfg, now):
    c, sz = cfg["costs"], cfg["sizing"]
    d = 1 if tr.side == "long" else -1
    slip = c["slippage_major"] if tr.open.symbol in c["majors"] else c["slippage_alt"]
    sig_lev = tr.leverage or 1
    lev = min(sig_lev, sz["max_leverage"])
    t_open = pd.Timestamp(max(tr.open.ts, tr.open.entry_time or tr.open.ts))
    t_close = pd.Timestamp(tr.close.ts if tr.close else now)
    row = {"open_time": t_open, "close_time": tr.close.ts if tr.close else None, "symbol": tr.open.symbol,
           "side": tr.side, "sig_leverage": sig_lev, "our_leverage": lev,
           "sig_entry": tr.open.price, "sig_exit": tr.close.price if tr.close else None,
           "reported_return": tr.close.reported_return if tr.close else None, "status": "closed" if tr.close else "open"}
    try:
        df = prices.window(tr.pair, t_open - 2 * MIN, t_close + 5 * MIN)
    except Exception as e:
        row["error"] = str(e)
        return row
    e_px, e_t = fill_after(df, t_open, cfg["delay_sec"])
    if tr.close:
        x_px, x_t = fill_after(df, pd.Timestamp(tr.close.ts), cfg["delay_sec"])
    else:
        x_px, x_t = (float(df["close"].iloc[-1]), df.index[-1]) if len(df) else (None, None)
    if e_px is None or x_px is None:
        row["error"] = "시세 없음"
        return row
    e_fill, x_fill = e_px * (1 + d * slip), x_px * (1 - d * slip)
    held = df[(df.index >= e_t) & (df.index < x_t)]
    # 진입~청산 사이 가장 불리했던 가격 변동 (최대 역행폭)
    if len(held):
        worst = held["low"].min() if d == 1 else held["high"].max()
        mae = max(0.0, -d * (worst / e_fill - 1))
    else:
        mae = 0.0
    # 펀딩 시각을 몇 번 지났는지
    fund_n = sum(1 for h in pd.date_range(e_t.floor("h"), x_t, freq="h") if h.hour % 8 == 0 and e_t < h <= x_t)
    gross = d * (x_fill / e_fill - 1)
    fee = c["taker_fee"] * (1 + x_fill / e_fill)
    funding = d * fund_n * c["funding_8h"]
    net = gross - fee - funding
    # 시그널 가격이 그 시각 거래소 가격 범위 안에 있었는지 (조작·다른 거래소 가격 확인)
    around = df[(df.index >= t_open.floor("min") - MIN) & (df.index <= t_open.floor("min") + MIN)]
    in_range = bool(len(around)) and around["low"].min() * 0.998 <= tr.open.price <= around["high"].max() * 1.002
    sig_move = d * (tr.close.price / tr.open.price - 1) if tr.close else None
    liq_move = 1 / sig_lev - sz["maintenance_margin"]
    row.update({
        "entry_fill": e_fill, "exit_fill": x_fill, "hold_min": (x_t - e_t).total_seconds() / 60,
        "delay_cost": d * (e_fill / tr.open.price - 1) if tr.open.price else None,
        "sig_move": sig_move, "our_move_net": net, "fees": fee, "funding": funding, "mae": mae,
        "sig_price_ok": in_range,
        "liq_at_sig_lev": mae >= liq_move,
        "liq_at_our_lev": mae >= 1 / lev - sz["maintenance_margin"],
    })
    for s in cfg["stop_candidates"]:
        if s is None:
            continue
        row[f"net_stop_{s:g}"] = (-s - slip - c["taker_fee"] * 2) if mae >= s else net
    return row


# ------------------------------------------------------------------ 포트폴리오
def equity_curve(rows, cfg, col):
    sz = cfg["sizing"]
    eq = sz["start_equity"]
    pts = []
    for r in sorted((r for r in rows if r.get(col) is not None and r["status"] == "closed"), key=lambda r: r["close_time"]):
        margin = eq * sz["margin_per_trade"]
        lev = r["our_leverage"]
        # 손실이 증거금보다 클 수 없음 (격리 마진 → 강제청산)
        pnl = max(-margin, margin * lev * r[col])
        eq += pnl
        pts.append((r["close_time"], eq))
    return pts


def stats_of(pts, start):
    if not pts:
        return {"final": start, "total": 0.0, "mdd": 0.0}
    eq = np.array([start] + [p[1] for p in pts])
    peak = np.maximum.accumulate(eq)
    return {"final": eq[-1], "total": eq[-1] / start - 1, "mdd": float((eq / peak - 1).min())}


def pct(x, nd=1):
    return "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:+.{nd}f}%"


def amt(x, nd=1):
    """부호 없이 크기만 (비용·역행폭 표시용)"""
    return "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{abs(x) * 100:.{nd}f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since")
    ap.add_argument("--demo", action="store_true", help="예시 시그널 + 가짜 시세로 동작 확인")
    ap.add_argument("--fake-prices", action="store_true", help="기록된 시그널 + 가짜 시세 (개발용)")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(HERE / "config.yaml", encoding="utf-8"))
    now = datetime.now(timezone.utc)

    if args.demo:
        sigs = demo_signals()
    else:
        sigs = load()
        if not sigs:
            sys.exit("기록된 시그널이 없어요. importer.py 나 listen.py 로 먼저 모아주세요.")
    if args.since:
        t0 = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        sigs = [s for s in sigs if s.ts >= t0]
    trades, warns = pair_trades(sigs)
    unknown = [s for s in sigs if s.kind == "unknown"]
    prices = DemoPrices(trades) if (args.demo or args.fake_prices) else Prices(cfg["price_exchange"])

    rows = []
    for i, t in enumerate(trades, 1):
        rows.append(score_trade(t, prices, cfg, now))
        if i % 20 == 0:
            print(f"  {i}/{len(trades)} 계산 중...")
    ok = [r for r in rows if "error" not in r]
    closed = [r for r in ok if r["status"] == "closed"]
    errs = [r for r in rows if "error" in r]
    if not closed:
        sys.exit("계산할 수 있는 완료된 거래가 없어요.")

    start = cfg["sizing"]["start_equity"]
    sz = cfg["sizing"]
    T = pd.DataFrame(closed)
    days = max(1.0, (T["close_time"].max() - T["open_time"].min()).total_seconds() / 86400)

    # 시그널 그대로(수수료 0, 지연 0)와 우리 따라하기 비교
    sig_rows = [dict(r, sig_net=r["sig_move"]) for r in closed]
    curves = {
        "시그널이 말한 그대로 (지연·수수료 없음)": equity_curve(sig_rows, cfg, "sig_net"),
        "따라하기 (지연·수수료·슬리피지 반영)": equity_curve(closed, cfg, "our_move_net"),
    }
    stop_cols = [(s, f"net_stop_{s:g}") for s in cfg["stop_candidates"] if s is not None]
    stop_res = {s: stats_of(equity_curve(closed, cfg, col), start) for s, col in stop_cols}

    stamp = now.astimezone(timezone(timedelta(hours=9))).strftime("%Y%m%d_%H%M")
    out = ROOT / "reports" / f"signals_{stamp}{'_demo' if args.demo else ''}"
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out / "trades.csv", index=False, encoding="utf-8-sig")

    fig, ax = plt.subplots(figsize=(11, 5))
    for name, pts in curves.items():
        if pts:
            ax.plot([p[0] for p in pts], [p[1] for p in pts], lw=1.4, label="as reported" if "그대로" in name else "copy (realistic)")
    ax.axhline(start, color="gray", lw=0.8, ls="--")
    ax.set_title("Telegram signals: equity" + (" [DEMO]" if args.demo else ""))
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "equity.png", dpi=110)
    plt.close(fig)

    win = (T["our_move_net"] > 0).mean()
    sig_win = (T["sig_move"] > 0).mean()
    gains, losses = T.loc[T.our_move_net > 0, "our_move_net"].sum(), -T.loc[T.our_move_net <= 0, "our_move_net"].sum()
    pf = gains / losses if losses > 0 else float("inf")
    rep_ok = T["reported_return"].notna()
    rep_diff = (T.loc[rep_ok, "reported_return"] / 100 - T.loc[rep_ok, "sig_move"] * T.loc[rep_ok, "sig_leverage"]).abs()
    bad_px = T[~T["sig_price_ok"]]
    liq_sig = int(T["liq_at_sig_lev"].sum())
    liq_our = int(T["liq_at_our_lev"].sum())
    st_sig = stats_of(curves["시그널이 말한 그대로 (지연·수수료 없음)"], start)
    st_our = stats_of(curves["따라하기 (지연·수수료·슬리피지 반영)"], start)

    L = [f"# 텔레그램 시그널 채점 ({stamp} KST)\n"]
    if args.demo:
        L.append("> ⚠️ **가짜 시세로 만든 데모**예요. 숫자에는 의미가 없어요.\n")
    L.append(f"- 기간: {T['open_time'].min():%Y-%m-%d} ~ {T['close_time'].max():%Y-%m-%d} ({days:.0f}일), 완료 거래 {len(T)}건 (하루 평균 {len(T) / days:.1f}건)")
    L.append(f"- 따라하기 조건: 메시지 후 {cfg['delay_sec']}초 + 다음 1분봉 시가 체결, 수수료 {cfg['costs']['taker_fee']:.2%}×2, "
             f"슬리피지 BTC·ETH {cfg['costs']['slippage_major']:.2%} / 알트 {cfg['costs']['slippage_alt']:.2%}")
    L.append(f"- 자금: 시작 {start:,} USDT, 건당 증거금 {sz['margin_per_trade']:.0%}, 레버리지 = 시그널 값 (최대 {sz['max_leverage']}배로 제한)")
    if len(open_ := [r for r in ok if r["status"] == "open"]):
        L.append(f"- 아직 열려 있는 포지션 {len(open_)}건은 제외")
    L.append("")

    L.append("## 요약\n")
    L.append("| | 시그널이 말한 그대로 | 실제로 따라 했다면 |")
    L.append("|---|---:|---:|")
    L.append(f"| 승률 | {sig_win:.0%} | {win:.0%} |")
    L.append(f"| 거래당 평균 (가격 변동, 레버리지 전) | {pct(T['sig_move'].mean(), 2)} | {pct(T['our_move_net'].mean(), 2)} |")
    L.append(f"| 최종 자산 | {st_sig['final']:,.0f} | {st_our['final']:,.0f} |")
    L.append(f"| 총수익률 | {pct(st_sig['total'])} | {pct(st_our['total'])} |")
    L.append(f"| 최대낙폭 | {pct(st_sig['mdd'])} | {pct(st_our['mdd'])} |")
    L.append(f"| 손익비 (PF) | - | {pf:.2f} |")
    L.append("")
    gap = T['sig_move'].mean() - T['our_move_net'].mean()
    L.append(f"- 거래당 비용: 수수료 평균 {amt(T['fees'].mean(), 3)}, 지연 때문에 불리해진 진입가 평균 {pct(T['delay_cost'].mean(), 3)}"
             f" → 시그널 대비 거래당 약 **{amt(gap, 2)} {'손해' if gap > 0 else '이득'}** (하루 {len(T) / days:.1f}건이면 하루 약 {amt(gap * len(T) / days, 2)})")
    L.append(f"- 보유 시간: 중간값 {T['hold_min'].median():.0f}분")
    L.append("")

    L.append("## 신뢰도 점검\n")
    L.append(f"- 시그널 진입가가 그 시각 거래소({cfg['price_exchange']}) 가격 범위 밖: **{len(bad_px)}건 / {len(T)}건**"
             + (" ← 많으면 다른 거래소 가격이거나 사후에 고친 시그널일 수 있음" if len(bad_px) else ""))
    if rep_ok.any():
        L.append(f"- 시그널이 말한 수익률 = 가격 변동 × 레버리지 인지 확인: 최대 오차 {rep_diff.max() * 100:.2f}%p (수수료 미포함 수치)")
    L.append(f"- 보유 중 최대 역행폭(진입 후 가장 불리했던 가격 변동): 중간값 {amt(T['mae'].median(), 2)}, 상위 10% {amt(T['mae'].quantile(0.9), 2)}, 최대 {amt(T['mae'].max(), 2)}")
    L.append(f"- 시그널 레버리지 그대로였다면 **강제청산 {liq_sig}건**, 우리 제한 레버리지(최대 {sz['max_leverage']}배)로는 {liq_our}건")
    L.append(f"- 짝이 안 맞는 메시지 경고 {len(warns)}건, 형식을 못 읽은 메시지 {len(unknown)}건, 시세를 못 구한 거래 {len(errs)}건")
    L.append("")

    L.append("## 봇 자체 보호 손절 후보\n")
    L.append("진입가 대비 이만큼 불리하게 움직이면 시그널 종료를 기다리지 않고 손절했을 때:\n")
    L.append("| 손절폭 | 걸린 거래 | 총수익률 | 최대낙폭 |")
    L.append("|---|---:|---:|---:|")
    L.append(f"| 없음 | 0 | {pct(st_our['total'])} | {pct(st_our['mdd'])} |")
    for s, col in stop_cols:
        hit = int((T["mae"] >= s).sum())
        L.append(f"| {s:.0%} | {hit} | {pct(stop_res[s]['total'])} | {pct(stop_res[s]['mdd'])} |")
    L.append("\n수익률이 거의 안 줄면서 최대낙폭을 줄이는 값이 좋은 보호 손절이에요.\n")

    L.append("## 종목별\n")
    g = T.groupby("symbol").agg(n=("our_move_net", "size"), win=("our_move_net", lambda x: (x > 0).mean()),
                                avg=("our_move_net", "mean"), sum_=("our_move_net", "sum"), mae=("mae", "max"))
    L.append("| 종목 | 거래 | 승률 | 거래당 평균 | 합계 | 최대 역행 |")
    L.append("|---|---:|---:|---:|---:|---:|")
    for sym, r in g.sort_values("sum_", ascending=False).iterrows():
        L.append(f"| {sym} | {int(r.n)} | {r.win:.0%} | {pct(r.avg, 2)} | {pct(r.sum_, 1)} | {amt(r.mae, 1)} |")
    L.append("\n## 롱/숏별\n")
    g2 = T.groupby("side").agg(n=("our_move_net", "size"), win=("our_move_net", lambda x: (x > 0).mean()), avg=("our_move_net", "mean"))
    L.append("| 방향 | 거래 | 승률 | 거래당 평균 |")
    L.append("|---|---:|---:|---:|")
    for side, r in g2.iterrows():
        L.append(f"| {'롱' if side == 'long' else '숏'} | {int(r.n)} | {r.win:.0%} | {pct(r.avg, 2)} |")
    L.append("\n![equity](equity.png)\n")
    if warns:
        L.append("## 경고 메시지\n")
        L += [f"- {w}" for w in warns[:30]]
    if len(T) < 60:
        L.append(f"\n> 완료 거래가 {len(T)}건뿐이라 결과가 우연일 가능성이 커요. 최소 60~100건은 모아서 판단하세요.")
    (out / "report.md").write_text("\n".join(L), encoding="utf-8")

    print(f"\n완료 거래 {len(T)}건 ({days:.0f}일)")
    print(f"  시그널 그대로: 승률 {sig_win:.0%}, 총수익 {pct(st_sig['total'])}, 낙폭 {pct(st_sig['mdd'])}")
    print(f"  실제 따라하기: 승률 {win:.0%}, 총수익 {pct(st_our['total'])}, 낙폭 {pct(st_our['mdd'])}, PF {pf:.2f}")
    print(f"  시그널 가격이 거래소 범위 밖: {len(bad_px)}건, 시그널 레버리지였으면 강제청산: {liq_sig}건")
    print(f"\n리포트: {out / 'report.md'}")


def demo_signals():
    """동작 확인용 가짜 시그널 60건."""
    rng = np.random.default_rng(3)
    base = {"BTC": 80000, "ETH": 3000, "ADA": 0.21, "NEAR": 4.8, "SOL": 150}
    t = datetime(2026, 8, 1, tzinfo=timezone.utc)
    out = []
    for i in range(60):
        sym = rng.choice(list(base))
        side = "long" if rng.random() < 0.7 else "short"
        lev = float(rng.choice([3, 5, 10]))
        p0 = base[sym] * (1 + rng.normal(0, 0.05))
        move = rng.normal(0.003, 0.012)
        p1 = p0 * (1 + (move if side == "long" else -move))
        t += timedelta(hours=float(rng.uniform(3, 10)))
        t2 = t + timedelta(minutes=float(rng.uniform(20, 120)))
        pair = f"{sym}/USDT:USDT"
        out.append(Signal(ts=t, kind="open", side=side, symbol=sym, pair=pair, price=p0, leverage=lev, entry_time=t - timedelta(seconds=20)))
        ret = (p1 / p0 - 1) * (1 if side == "long" else -1) * lev * 100
        out.append(Signal(ts=t2, kind="close", side=side, symbol=sym, pair=pair, price=p1, leverage=lev, reported_return=round(ret, 2)))
    return out


if __name__ == "__main__":
    main()
