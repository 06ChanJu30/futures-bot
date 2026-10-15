"""시세 데이터 수집/로드. 공개 데이터만 사용하므로 API 키가 필요 없습니다."""
import time
from pathlib import Path

import numpy as np
import pandas as pd

TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def _paths(cfg):
    d = cfg["data"]
    safe = d["symbol"].replace("/", "").replace(":", "_")
    base = Path(d["data_dir"])
    return (
        base / f"{d['exchange']}_{safe}_{d['timeframe']}.csv",
        base / f"{d['exchange']}_{safe}_funding.csv",
    )


def _exchange(cfg):
    import ccxt

    ex = getattr(ccxt, cfg["data"]["exchange"])({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    ex.load_markets()
    return ex


def _fetch_range(ex, symbol, tf, since, until, log):
    tf_ms = TF_MS[tf]
    rows = []
    while since < until:
        batch = ex.fetch_ohlcv(symbol, tf, since=since, limit=1000)
        batch = [b for b in batch if b[0] < until]
        if not batch:
            break
        rows += batch
        since = batch[-1][0] + tf_ms
        log(f"  OHLCV {pd.to_datetime(batch[-1][0], unit='ms'):%Y-%m-%d %H:%M} 까지 {len(rows):,}개")
        time.sleep(ex.rateLimit / 1000)
    return rows


def fetch(cfg, log=print):
    """과거 OHLCV와 펀딩비 내역을 받아 CSV로 저장.
    이미 받은 파일이 있으면 모자란 앞부분(더 과거)과 뒷부분(최신)만 받아서 합칩니다."""
    d = cfg["data"]
    ohlcv_path, fund_path = _paths(cfg)
    ohlcv_path.parent.mkdir(parents=True, exist_ok=True)
    ex = _exchange(cfg)
    tf_ms = TF_MS[d["timeframe"]]
    now = ex.milliseconds()
    want_since = now - int(d["years"] * 365 * 86_400_000)

    old = pd.read_csv(ohlcv_path) if ohlcv_path.exists() else None
    rows = []
    if old is None or old.empty:
        rows += _fetch_range(ex, d["symbol"], d["timeframe"], want_since, now, log)
    else:
        first, last = int(old["ts"].iloc[0]), int(old["ts"].iloc[-1])
        if want_since < first - tf_ms:
            log("더 과거 데이터를 받는 중...")
            rows += _fetch_range(ex, d["symbol"], d["timeframe"], want_since, first, log)
        log("최신 데이터를 받는 중...")
        rows += _fetch_range(ex, d["symbol"], d["timeframe"], last + tf_ms, now, log)
    new = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = pd.concat([old, new]) if old is not None else new
    df = df.drop_duplicates("ts").sort_values("ts")
    df = df[df["ts"] <= now - tf_ms]  # 아직 안 끝난 봉 제외
    df.to_csv(ohlcv_path, index=False)
    log(f"저장: {ohlcv_path} ({len(df):,}개 봉, {pd.to_datetime(df['ts'].iloc[0], unit='ms'):%Y-%m-%d} 부터)")

    if ex.has.get("fetchFundingRateHistory"):
        fsince = int(df["ts"].iloc[0])
        frows = []
        while fsince < now:
            try:
                batch = ex.fetch_funding_rate_history(d["symbol"], since=fsince, limit=1000)
            except Exception as e:  # 거래소마다 제약이 달라서 실패해도 기본값으로 진행
                log(f"  펀딩비 내역 수집 중단: {e}")
                break
            if not batch:
                break
            frows += [(b["timestamp"], b["fundingRate"]) for b in batch]
            if batch[-1]["timestamp"] + 1 <= fsince:
                break
            fsince = batch[-1]["timestamp"] + 1
            time.sleep(ex.rateLimit / 1000)
        if frows:
            f = pd.DataFrame(frows, columns=["ts", "rate"]).drop_duplicates("ts").sort_values("ts")
            f.to_csv(fund_path, index=False)
            log(f"저장: {fund_path} ({len(f):,}건)")
    else:
        log("이 거래소는 펀딩비 내역을 제공하지 않아 기본 펀딩비를 사용합니다.")


def load(cfg, synthetic=False):
    """백테스트용 DataFrame (index=UTC 시각, open/high/low/close/volume/funding)."""
    if synthetic:
        df, fund = make_synthetic()
    else:
        ohlcv_path, fund_path = _paths(cfg)
        if not ohlcv_path.exists():
            raise SystemExit(f"데이터 파일이 없어요: {ohlcv_path}\n먼저 'python fetch_data.py'를 실행하세요.")
        df = pd.read_csv(ohlcv_path)
        df.index = pd.DatetimeIndex(pd.to_datetime(df["ts"], unit="ms", utc=True))
        df = df.drop(columns="ts")
        fund = None
        if fund_path.exists():
            f = pd.read_csv(fund_path)
            fidx = pd.DatetimeIndex(pd.to_datetime(f["ts"], unit="ms", utc=True)).floor("h")
            fund = pd.Series(f["rate"].to_numpy(), index=fidx)
            fund = fund[~fund.index.duplicated()]

    c = cfg["costs"]
    is_fund_hour = df.index.hour.isin(c["funding_hours_utc"]) & (df.index.minute == 0)
    default = pd.Series(np.where(is_fund_hour, c["default_funding_rate"], 0.0), index=df.index)
    if fund is not None and len(fund):
        known = fund.reindex(df.index)
        covered = (df.index >= fund.index[0]) & (df.index <= fund.index[-1])
        # 실제 데이터 기간은 실제 펀딩 시각/값, 그 밖은 기본 펀딩 시각에 기본값
        df["funding"] = np.where(covered, known.fillna(0.0), default)
    else:
        df["funding"] = default.to_numpy()
    return df


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """1시간봉을 4시간봉/일봉으로 묶음. 펀딩비는 그 봉 안의 합계를 봉 시작에 정산(근사)."""
    rule = {"1h": "1h", "4h": "4h", "1d": "1D"}[tf]
    base_step = pd.Series(df.index).diff().median()
    if pd.Timedelta(rule) == base_step:
        return df
    g = df.resample(rule, label="left", closed="left")
    out = g.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "funding": "sum"})
    count = g["close"].count()
    full = int(pd.Timedelta(rule) / base_step)
    out = out[count >= full].dropna(subset=["open", "close"])  # 덜 찬 봉(데이터 빠짐/진행 중) 제외
    return out


def bars_per_day(df: pd.DataFrame) -> float:
    return pd.Timedelta(days=1) / pd.Series(df.index).diff().median()


def make_synthetic(years=6, seed=7):
    """테스트용 가짜 시세 (추세 구간과 횡보 구간이 번갈아 나오는 랜덤워크)."""
    rng = np.random.default_rng(seed)
    n = int(years * 365 * 24)
    idx = pd.date_range("2020-01-01", periods=n, freq="h", tz="UTC")
    drift = np.repeat(rng.normal(0, 0.0003, n // 500 + 1), 500)[:n]
    vol = np.repeat(rng.uniform(0.004, 0.012, n // 300 + 1), 300)[:n]
    r = drift + vol * rng.standard_t(4, n) / np.sqrt(2)
    close = 30000 * np.exp(np.cumsum(r))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + np.abs(rng.normal(0, vol * 0.6, n)) * close
    low = np.minimum(open_, close) - np.abs(rng.normal(0, vol * 0.6, n)) * close
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1.0}, index=idx)
    return df, None
