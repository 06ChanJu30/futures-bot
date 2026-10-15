"""머신러닝 전략 (Freqtrade의 FreqAI 방식을 단순화해서 참고).

- 특징값: 여러 기간 수익률, 변동성, RSI, 이동평균과의 거리, 펀딩비 (모두 과거 데이터만)
- 정답(라벨): 앞으로 H일 뒤 가격이 올랐는가 (1/0)
- 모델: 그래디언트 부스팅 분류기 (scikit-learn HistGradientBoosting)
- 워크포워드로 매 구간 다시 학습. 학습 구간 끝의 H일은 정답이 검증 구간과 겹치므로 잘라냄(purge)
- 설정 고르기: 학습 구간을 75:25로 나눠 앞에서 학습, 뒤에서 성적 확인 → 고른 설정으로 전체 재학습

연구상 가격 방향 예측 모델은 비용을 빼면 우위가 사라지는 경우가 많아요.
이 전략은 '규칙 기반보다 나은가'를 같은 잣대로 확인하기 위한 실험용입니다.
"""
import numpy as np
import pandas as pd

from .indicators import atr, ema, rsi


def _n(days, bpd):
    return max(1, int(round(days * bpd)))


def features(df: pd.DataFrame, bpd: float) -> pd.DataFrame:
    c = df["close"]
    lr = np.log(c)
    one = lr.diff()
    a = atr(df)
    X = {}
    for d in (1, 3, 7, 14, 30, 60):
        X[f"ret_{d}d"] = lr - lr.shift(_n(d, bpd))
    for d in (7, 30):
        X[f"vol_{d}d"] = one.rolling(_n(d, bpd)).std()
    X["vol_ratio"] = X["vol_7d"] / X["vol_30d"]
    X["rsi14"] = rsi(c, 14)
    X["rsi2"] = rsi(c, 2)
    for d in (20, 50, 100):
        X[f"ema_dist_{d}d"] = (c - ema(c, _n(d, bpd))) / a
    X["range"] = (df["high"] - df["low"]) / c
    f3 = df["funding"].rolling(_n(3, bpd)).sum()
    X["funding_3d"] = f3
    X["funding_z"] = (f3 - f3.rolling(_n(90, bpd)).mean()) / f3.rolling(_n(90, bpd)).std()
    return pd.DataFrame(X, index=df.index).replace([np.inf, -np.inf], np.nan)


def labels(df: pd.DataFrame, bpd: float, horizon_days: float) -> pd.Series:
    n = _n(horizon_days, bpd)
    fwd = df["close"].shift(-n) / df["close"] - 1
    return (fwd > 0).astype(float).where(fwd.notna())


def _model():
    from sklearn.ensemble import HistGradientBoostingClassifier

    return HistGradientBoostingClassifier(
        max_depth=3, learning_rate=0.05, max_iter=200, l2_regularization=1.0,
        min_samples_leaf=50, random_state=0,
    )


def fit_predict(X, y, bpd, horizon_days, fit_lo, fit_hi, pred_lo, pred_hi):
    """[fit_lo, fit_hi - H) 로 학습 → [pred_lo, pred_hi) 상승 확률 예측."""
    purge = _n(horizon_days, bpd)
    hi = fit_hi - purge
    Xt, yt = X.iloc[fit_lo:hi], y.iloc[fit_lo:hi]
    ok = yt.notna().to_numpy()
    Xt, yt = Xt[ok], yt[ok]
    if len(yt) < 100 or yt.nunique() < 2:
        return None
    cols = [c for c in Xt.columns if Xt[c].notna().sum() > 50 and Xt[c].nunique() > 1]  # 비어있거나 상수인 특징 제외
    m = _model().fit(Xt[cols].to_numpy(), yt.to_numpy().astype(int))
    return m.predict_proba(X[cols].iloc[pred_lo:pred_hi].to_numpy())[:, 1]


def to_target(proba, threshold, allow_short):
    t = np.where(proba > 0.5 + threshold, 1, 0)
    if allow_short:
        t = np.where(proba < 0.5 - threshold, -1, t)
    return t
