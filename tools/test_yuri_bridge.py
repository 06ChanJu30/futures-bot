"""Yuri 연결 다리 검사 (가짜 Freqtrade 서버로, 거래소·Docker 없이).

  python tools/test_yuri_bridge.py

확인하는 것: 상태 문장, 최근 거래, 시그널 보고서, 알림(진입·청산·비상정지·봇 꺼짐/켜짐),
도구 호출, 연결 다리가 꺼져 있을 때 Yuri 쪽이 멈추지 않는지, 외부 조작 기능이 없는지.
"""
import base64
import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "yuri_bridge"))
import bridge  # noqa: E402
from yuri_client import FuturesBridge  # noqa: E402

USER, PW = "freqtrader", "pw123"
FAKE = {"open": [], "closed": [], "alive": True}


class FakeFT(BaseHTTPRequestHandler):
    def do_GET(self):
        if not FAKE["alive"]:
            self.close_connection = True
            return self.send_error(503)
        want = "Basic " + base64.b64encode(f"{USER}:{PW}".encode()).decode()
        if self.headers.get("Authorization") != want:
            return self.send_error(401)
        p = self.path.split("/api/v1/")[1]
        if p.startswith("show_config"):
            b = {"dry_run": True, "exchange": "binance", "state": "running", "strategy": "TsmomEnsemble",
                 "stake_currency": "USDT"}
        elif p.startswith("status"):
            b = FAKE["open"]
        elif p.startswith("profit"):
            b = {"profit_all_coin": 12.5, "closed_trade_count": len(FAKE["closed"]), "winning_trades": 1,
                 "losing_trades": 0}
        elif p.startswith("daily"):
            b = {"data": [{"date": "2026-11-05", "abs_profit": 3.1, "trade_count": 1}]}
        elif p.startswith("trades"):
            b = {"trades": FAKE["closed"] + [dict(t, is_open=True) for t in FAKE["open"]]}
        else:
            return self.send_error(404)
        d = json.dumps(b).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(d)))
        self.end_headers()
        self.wfile.write(d)

    def log_message(self, *a):
        pass


OK = 0


def check(name, cond):
    global OK
    print(("  ✅ " if cond else "  ❌ ") + name)
    if not cond:
        raise SystemExit(1)
    OK += 1


def main():
    tmp = Path(tempfile.mkdtemp())
    sec = tmp / "config.private.json"
    sec.write_text(json.dumps({"api_server": {"username": USER, "password": PW}}))
    ft_srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeFT)
    threading.Thread(target=ft_srv.serve_forever, daemon=True).start()
    ft = bridge.Freqtrade(f"http://127.0.0.1:{ft_srv.server_port}", sec)

    # 비상정지 파일은 임시 폴더로 (진짜 user_data 건드리지 않음)
    ks_dir = tmp / "freqtrade" / "user_data"
    ks_dir.mkdir(parents=True)
    bridge.ROOT = tmp
    (tmp / "reports" / "levia_20261101_demo").mkdir(parents=True)
    (tmp / "reports" / "levia_20261101_demo" / "report.md").write_text("# LEVIA 결과\n거래 12건, PF 1.1\n", encoding="utf-8")

    w = bridge.Watcher(ft, every=3600, fail_limit=2)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), bridge.make_handler(ft, w))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    fb = FuturesBridge(f"http://127.0.0.1:{srv.server_port}", state_file=tmp / "seen.json")

    print("1) 상태·거래·보고서")
    check("연결 확인", fb.alive())
    s = fb.status()
    check("상태 문장: 모의매매·포지션 0개·누적 손익", "모의매매 중(binance)" in s["say_hint"]
          and "열린 포지션 0개" in s["say_hint"] and "+12.50" in s["say_hint"])
    check("거래 없음 문장", fb.trades()["say_hint"] == "아직 정리된 거래가 없어요.")
    r = fb.call_tool("futures_signal_report", {"kind": "levia"})
    check("시그널 보고서 + 데모 표시", "가짜 데이터 데모" in r and "PF 1.1" in r)
    check("보고서 없는 종류 안내", "아직 채점 보고서가 없어요" in fb.call_tool("futures_signal_report", {"kind": "levybot"}))
    check("도구 3개 + 규칙", len(fb.tools()) == 3 and "지어내지" in fb.rules())

    print("2) 알림")
    w.check()  # 기준 잡기 (알림 없음)
    check("첫 확인은 알림 없음", fb.new_events() == [])
    t = {"trade_id": 7, "pair": "BTC/USDT:USDT", "is_short": False, "leverage": 2, "open_date": "2026-11-05 08:00:00",
         "profit_ratio": 0.012, "profit_abs": 1.5}
    FAKE["open"] = [t]
    w.check()
    ev = fb.new_events()
    check("진입 알림", len(ev) == 1 and ev[0]["kind"] == "trade_opened" and "BTC 롱 2배" in ev[0]["text"]
          and "11/05 17:00" in ev[0]["text"])
    check("이미 본 알림은 다시 안 나옴", fb.new_events() == [])
    FAKE["open"] = []
    FAKE["closed"] = [dict(t, is_open=False, close_date="2026-11-05 10:00:00", close_timestamp=1, profit_ratio=0.03,
                           profit_abs=4.2, exit_reason="trailing_stop_loss")]
    w.check()
    ev = fb.new_events()
    check("청산 알림 (수익·이유)", len(ev) == 1 and ev[0]["kind"] == "trade_closed" and "+3.0%" in ev[0]["text"]
          and "trailing_stop_loss" in ev[0]["text"])
    check("최근 거래 문장", "BTC 롱 2배" in fb.trades()["say_hint"])
    (ks_dir / "kill_switch_state.binance.dryrun.json").write_text(json.dumps({"killed": True}))
    w.check()
    ev = fb.new_events()
    check("비상정지 알림 (모의라 urgent 아님)", len(ev) == 1 and ev[0]["kind"] == "kill_switch" and ev[0]["urgent"] is False)
    check("상태에 비상정지 표시", "비상정지" in fb.status()["say_hint"])

    FAKE["alive"] = False
    w.check(); w.check()
    ev = fb.new_events()
    check("봇 꺼짐 알림 (연속 실패 후 1번만)", [e["kind"] for e in ev] == ["bot_down"])
    check("봇 꺼졌을 때 상태 문장", "꺼져 있거나" in fb.status()["say_hint"])
    FAKE["alive"] = True
    w.check()
    check("봇 다시 켜짐 알림", [e["kind"] for e in fb.new_events()] == ["bot_up"])

    print("3) 안전")
    for path in ("/stop", "/forceexit", "/forceenter", "/start"):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}{path}", timeout=3)
            got = 200
        except urllib.error.HTTPError as e:
            got = e.code
        check(f"조작 주소 없음 {path}", got == 404)
    srv.shutdown()
    dead = FuturesBridge("http://127.0.0.1:1", state_file=tmp / "x.json", timeout=1)
    check("다리 꺼져도 Yuri 쪽 예외 없음", not dead.alive() and "꺼져 있어요" in dead.call_tool("futures_status")
          and dead.new_events() == [])
    print(f"\n전부 통과: {OK}개")


if __name__ == "__main__":
    main()
