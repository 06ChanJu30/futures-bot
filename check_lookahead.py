"""미래 참조(lookahead) 검사 — Freqtrade의 lookahead-analysis를 참고.

각 전략의 신호를 '전체 데이터'와 '중간에서 자른 데이터'로 각각 계산해서
자른 지점 이전 값이 하나라도 다르면 미래 데이터를 엿보고 있다는 뜻이에요.

  python check_lookahead.py             # 실제 데이터
  python check_lookahead.py --synthetic # 가짜 데이터
"""
import argparse

import numpy as np
import yaml

from fbot import data, ml
from fbot.strategies import STRATEGIES
from fbot.walkforward import param_grid


def same(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    return np.allclose(np.nan_to_num(a, nan=-9e9), np.nan_to_num(b, nan=-9e9), rtol=1e-9, atol=1e-12)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    base = data.load(cfg, synthetic=args.synthetic)
    bad = 0
    for tf in cfg["timeframes"]:
        full = data.resample(base, tf)
        bpd = data.bars_per_day(full)
        cuts = [int(len(full) * f) for f in (0.35, 0.6, 0.85)]
        for name in cfg["strategies"]:
            ok = True
            for cut in cuts:
                part = full.iloc[:cut]
                if name == "ml_gbm":
                    a, b = ml.features(full, bpd).iloc[: cut], ml.features(part, bpd)
                    ok &= all(same(a[c], b[c]) for c in a.columns)
                    continue
                for prm in param_grid(cfg["strategies"][name]):
                    s1, s2 = STRATEGIES[name](full, bpd, **prm), STRATEGIES[name](part, bpd, **prm)
                    if s1 is None:
                        continue
                    for col in s1.columns:
                        if not same(s1[col].iloc[:cut], s2[col]):
                            ok = False
            print(f"[{tf}] {name:<18} {'문제 없음' if ok else '⚠️ 미래 참조 의심'}")
            bad += not ok
    print("\n결과:", "모든 전략 통과" if not bad else f"{bad}건 의심 — 해당 전략 코드를 확인하세요")


if __name__ == "__main__":
    main()
