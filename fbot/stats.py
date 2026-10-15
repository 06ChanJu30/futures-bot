"""'운이 아닌가' 판정 통계 (Bailey & López de Prado).

- PSR (확률적 샤프): 관측된 샤프가 기준값보다 진짜로 높을 확률. 수익 분포의 치우침/뚱뚱한 꼬리 반영
- DSR (보정 샤프): 여러 후보를 시험하고 그중 제일 좋은 걸 고르면, 운만으로도 높은 샤프가 나와요.
  시험한 후보 수(N)만큼 기준을 높여서 PSR을 다시 계산한 값. 0.95 이상이면 '운일 가능성 5% 미만'.
"""
from statistics import NormalDist

import numpy as np

_N = NormalDist()
EULER = 0.5772156649


def _moments(r):
    r = np.asarray(r, float)
    r = r[np.isfinite(r)]
    if len(r) < 3 or r.std() == 0:
        return None
    mu, sd = r.mean(), r.std(ddof=1)
    z = (r - mu) / sd
    return mu / sd, float((z ** 3).mean()), float((z ** 4).mean()), len(r)


def psr(returns, sr_benchmark=0.0):
    m = _moments(returns)
    if m is None:
        return 0.0
    sr, skew, kurt, t = m
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    if denom <= 0:
        return 0.0
    return _N.cdf((sr - sr_benchmark) * np.sqrt(t - 1) / np.sqrt(denom))


def expected_max_sr(sr_variance, n_trials):
    """후보 n개가 모두 실력 0일 때, 운으로 나오는 최고 샤프의 기댓값 (일간 단위)."""
    if n_trials < 2 or sr_variance <= 0:
        return 0.0
    return np.sqrt(sr_variance) * (
        (1 - EULER) * _N.inv_cdf(1 - 1 / n_trials) + EULER * _N.inv_cdf(1 - 1 / (n_trials * np.e))
    )


def daily_returns(index, equity):
    import pandas as pd

    s = pd.Series(np.asarray(equity, float), index=index).resample("1D").last().dropna()
    return s.pct_change().dropna().to_numpy()


def deflated(all_daily_returns: dict):
    """후보별 일간 수익률 dict → 후보별 (일간 샤프, PSR, DSR)."""
    srs = {}
    for k, r in all_daily_returns.items():
        m = _moments(r)
        srs[k] = m[0] if m else 0.0
    var = float(np.var(list(srs.values()), ddof=1)) if len(srs) > 1 else 0.0
    sr0 = expected_max_sr(var, len(srs))
    return {k: (srs[k], psr(r, 0.0), psr(r, sr0)) for k, r in all_daily_returns.items()}, sr0
