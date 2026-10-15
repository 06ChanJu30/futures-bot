"""백테스트 실행.

  python run_backtest.py              # 받아둔 실제 데이터로 실행
  python run_backtest.py --synthetic  # 가짜 데이터로 프로그램 동작만 확인
"""
import argparse
import time

import yaml

from fbot import data, report, walkforward as wfm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--synthetic", action="store_true", help="가짜 데이터로 동작 테스트")
    ap.add_argument("--only", help="특정 전략만 (쉼표로 구분)")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    t0 = time.time()
    df = data.load(cfg, synthetic=args.synthetic)
    print(f"데이터 {len(df):,}개 봉 ({df.index[0]:%Y-%m-%d} ~ {df.index[-1]:%Y-%m-%d})")
    m = wfm.market_arrays(df)

    names = args.only.split(",") if args.only else list(cfg["strategies"])
    results = [wfm.run_strategy(n, df, m, cfg) for n in names]
    first = df.index.searchsorted(results[0]["index"][0])
    bh_eq, bh_stats = wfm.buy_and_hold(df, first, first + len(results[0]["index"]), cfg)

    out = report.write(results, bh_eq, bh_stats, cfg, synthetic=args.synthetic)
    print("\n=== 검증 구간 성적 ===")
    for r in results:
        s = r["stats"]
        print(f"{r['name']:<18} 수익 {s['total_return']:+8.1%}  최대낙폭 {s['max_drawdown']:7.1%}  샤프 {s['sharpe']:5.2f}  거래 {s['trades']}")
    print(f"{'단순 보유':<18} 수익 {bh_stats['total_return']:+8.1%}  최대낙폭 {bh_stats['max_drawdown']:7.1%}")
    print(f"\n리포트: {out / 'report.md'}  ({time.time() - t0:.0f}초)")


if __name__ == "__main__":
    main()
