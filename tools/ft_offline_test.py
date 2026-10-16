"""Freqtrade 전략 오프라인 검증 도구 (개발용).

거래소 접속 없이, 가짜 시세와 가짜 시장 정보로 Freqtrade 백테스트를 돌려
전략·설정 파일이 각 거래소에서 오류 없이 동작하는지 확인합니다.

  pip install freqtrade        (또는 Docker 이미지 안에서 실행)
  python tools/ft_offline_test.py                  # 5개 거래소 전부
  python tools/ft_offline_test.py --exchange bybit # 하나만
  python tools/ft_offline_test.py --keep           # 작업 폴더를 지우지 않음

결과: 거래소별 거래 수·수익률·청산 사유를 출력하고, 오류가 있으면 실패로 표시.
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FT = ROOT / "freqtrade"
EXCHANGES = ["binance", "bybit", "okx", "bitget", "gate"]

RUNNER = r'''
import sys
from unittest import mock
from freqtrade.exchange.exchange import Exchange

def mk(sym, base):
    return {"id": base + "USDT", "symbol": sym, "base": base, "quote": "USDT", "settle": "USDT",
            "baseId": base, "quoteId": "USDT", "settleId": "USDT", "type": "swap", "spot": False,
            "margin": False, "swap": True, "future": False, "option": False, "linear": True,
            "inverse": False, "contract": True, "contractSize": 1.0, "active": True,
            "taker": 0.0005, "maker": 0.0002, "percentage": True, "tierBased": False,
            "precision": {"amount": 0.001, "price": 0.01},
            "limits": {"amount": {"min": 0.001, "max": 1e6}, "cost": {"min": 5, "max": None},
                       "leverage": {"min": 1, "max": 100}, "price": {"min": 0.01, "max": None}},
            "info": {}, "created": None}

MARKETS = [mk("BTC/USDT:USDT", "BTC"), mk("ETH/USDT:USDT", "ETH")]
TIERS = {m["symbol"]: [{"minNotional": 0, "maxNotional": 1e9, "maintenanceMarginRate": 0.005,
                        "maxLeverage": 100, "maintAmt": 0}] for m in MARKETS}

def fake_load(self, reload=False):
    self._api_async.set_markets(MARKETS)

def fake_tiers(self):
    return {k: [self.parse_leverage_tier(t) if hasattr(self, "parse_leverage_tier") else t for t in v]
            for k, v in TIERS.items()}

patches = [
    mock.patch.object(Exchange, "_load_async_markets", fake_load),
    mock.patch.object(Exchange, "fetch_trading_fees", lambda self: {}),
    mock.patch.object(Exchange, "load_leverage_tiers", lambda self: {k: [
        {"minNotional": 0.0, "maxNotional": 1e9, "maintenanceMarginRate": 0.005,
         "maxLeverage": 100.0, "maintAmt": 0.0}] for k in TIERS}),
    mock.patch.object(Exchange, "additional_exchange_init", lambda self: None),
]
for p in patches:
    p.start()
from freqtrade.main import main
sys.exit(main(sys.argv[1:]))
'''


def make_data(datadir: Path):
    sys.path.insert(0, str(ROOT))
    import pandas as pd
    from freqtrade.data.history import get_datahandler
    from freqtrade.enums import CandleType

    from fbot.data import make_synthetic

    dh = get_datahandler(datadir, "feather")
    for pair, seed in (("BTC/USDT:USDT", 7), ("ETH/USDT:USDT", 8)):
        h, _ = make_synthetic(seed=seed)
        agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        d = h.resample("1D").agg(agg)
        for tf in ("1h", "8h"):
            m = h.resample(tf).agg(agg)
            dh.ohlcv_store(pair, tf, m.reset_index().rename(columns={"index": "date"}), CandleType.MARK)
            f = pd.DataFrame({"funding_rate": 0.0001 if tf == "8h" else 0.0}, index=m.index)
            if tf == "8h":
                dh.ohlcv_store(pair, tf, f.reset_index().rename(columns={"index": "date"}), CandleType.FUNDING_RATE)
        dh.ohlcv_store(pair, "1d", d.reset_index().rename(columns={"index": "date"}), CandleType.FUTURES)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exchange", choices=EXCHANGES)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="ftoffline_"))
    ud = work / "user_data"
    (ud / "strategies").mkdir(parents=True)
    shutil.copy(FT / "user_data/strategies/TsmomEnsemble.py", ud / "strategies")
    (work / "runner.py").write_text(RUNNER, encoding="utf-8")
    ok_all = True
    for ex in [args.exchange] if args.exchange else EXCHANGES:
        datadir = ud / "data" / ex
        datadir.mkdir(parents=True, exist_ok=True)
        (ud / "bt" / ex).mkdir(parents=True, exist_ok=True)
        make_data(datadir)
        cmd = [sys.executable, str(work / "runner.py"), "backtesting",
               "--config", str(FT / "config.json"), "--config", str(FT / "exchanges" / f"{ex}.json"),
               "--userdir", str(ud), "--datadir", str(datadir), "--strategy", "TsmomEnsemble",
               "--timerange", "20200801-", "--fee", "0.0005", "--export", "trades",
               "--backtest-directory", str(ud / "bt" / ex), "--cache", "none"]
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = p.stdout + p.stderr
        res = sorted((ud / "bt" / ex).glob("*.zip"))
        if p.returncode != 0 or not res:
            ok_all = False
            print(f"❌ {ex}: 실패\n" + "\n".join(l for l in out.splitlines() if "ERROR" in l or "CRITICAL" in l or "Error" in l)[-2000:])
            continue
        import zipfile
        with zipfile.ZipFile(res[-1]) as z:
            n = [x for x in z.namelist() if x.endswith(".json") and "config" not in x and "market" not in x][0]
            st = json.loads(z.read(n))["strategy"]["TsmomEnsemble"]
        reasons = {}
        for t in st["trades"]:
            reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
        print(f"✅ {ex:<8} 거래 {st['total_trades']:3d}  수익 {st['profit_total']:+.1%}  "
              f"낙폭 {st.get('max_drawdown_account', 0):.1%}  청산 {reasons}")
    if args.keep:
        print(f"작업 폴더: {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
