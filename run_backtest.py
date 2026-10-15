"""백테스트 실행.

  python run_backtest.py                    # 받아둔 실제 데이터로 실행
  python run_backtest.py --synthetic        # 가짜 데이터로 프로그램 동작만 확인
  python run_backtest.py --only tsmom_ensemble,trend_ema --tf 1d
"""
import argparse
import time

import yaml

from fbot import data, report, stats, walkforward as wfm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--synthetic", action="store_true", help="가짜 데이터로 동작 테스트")
    ap.add_argument("--only", help="특정 전략만 (쉼표로 구분)")
    ap.add_argument("--tf", help="특정 봉 주기만 (예: 4h,1d)")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    t0 = time.time()
    base = data.load(cfg, synthetic=args.synthetic)
    print(f"원본 데이터 {len(base):,}개 봉 ({base.index[0]:%Y-%m-%d} ~ {base.index[-1]:%Y-%m-%d})")

    names = args.only.split(",") if args.only else list(cfg["strategies"])
    tfs = args.tf.split(",") if args.tf else cfg["timeframes"]
    by_tf, bh, daily = {}, {}, {}
    for tf in tfs:
        df = data.resample(base, tf)
        bpd = data.bars_per_day(df)
        m = wfm.market_arrays(df, bpd)
        print(f"\n[{tf}] {len(df):,}개 봉")
        by_tf[tf] = []
        for n in names:
            print(f"  {n}")
            r = wfm.run_strategy(n, df, m, bpd, cfg, log=print)
            r["tf"] = tf
            by_tf[tf].append(r)
            daily[(tf, n)] = stats.daily_returns(r["index"], r["equity"])
        first = df.index.searchsorted(by_tf[tf][0]["index"][0])
        eq, st = wfm.buy_and_hold(df, first, first + len(by_tf[tf][0]["index"]), cfg)
        bh[tf] = (by_tf[tf][0]["index"], eq, st)

    dsr, sr0 = stats.deflated(daily)
    for tf, rs in by_tf.items():
        for r in rs:
            r["dsr"] = dsr[(tf, r["name"])][2]
            r["verdict"] = report.verdict(r, cfg)

    out = report.write(by_tf, bh, cfg, sr0, synthetic=args.synthetic)
    print("\n=== 검증 구간 성적 ('운 아닐 확률' 순) ===")
    allr = sorted((r for rs in by_tf.values() for r in rs), key=lambda r: r["dsr"], reverse=True)
    for r in allr:
        s = r["stats"]
        print(f"{r['tf']:>3} {r['name']:<18} 수익 {s['total_return']:+8.1%}  낙폭 {s['max_drawdown']:7.1%}  "
              f"샤프 {s['sharpe']:5.2f}  운아닐확률 {r['dsr']:4.0%}  거래 {s['trades']:4d}  {r['verdict'][0]}")
    for tf, (_, _, b) in bh.items():
        print(f"{tf:>3} {'단순 보유':<16} 수익 {b['total_return']:+8.1%}  낙폭 {b['max_drawdown']:7.1%}  샤프 {b['sharpe']:5.2f}")
    print(f"\n리포트: {out / 'report.md'}  ({time.time() - t0:.0f}초)")


if __name__ == "__main__":
    main()
