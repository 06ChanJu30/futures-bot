"""Yuri 연결 다리 (읽기 전용).

선물봇(Freqtrade)과 시그널 채점 결과를 Yuri가 쉽게 물어볼 수 있게
내 PC 안에서만(127.0.0.1) 열리는 작은 서버를 띄웁니다.

  python yuri_bridge/bridge.py            # 기본: http://127.0.0.1:8765
  python yuri_bridge/bridge.py --port 8765 --ft-url http://127.0.0.1:8080

지키는 것
- 주문·청산·정지 같은 '조작' 기능은 아예 없습니다. 보기만 합니다.
- 내 PC 밖에서는 접속할 수 없습니다(127.0.0.1에만 열림).
- 표준 라이브러리만 사용 (추가 설치 없음).
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))
VERSION = "1.0"
log = logging.getLogger("yuri_bridge")


def kst(ts) -> str:
    """Freqtrade 시각(문자열·밀리초) → '11/05 17:30' (한국 시간)."""
    if ts is None or ts == "":
        return ""
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(KST).strftime("%m/%d %H:%M")
    except Exception:
        return str(ts)


def sign(x: float) -> str:
    return f"+{x:.2f}" if x >= 0 else f"{x:.2f}"


# ------------------------------------------------------------------ Freqtrade 읽기
class Freqtrade:
    def __init__(self, url: str, private_cfg: Path):
        self.url = url.rstrip("/")
        a = json.loads(private_cfg.read_text(encoding="utf-8"))["api_server"]
        tok = base64.b64encode(f"{a['username']}:{a['password']}".encode()).decode()
        self.auth = {"Authorization": f"Basic {tok}"}

    def get(self, path: str):
        req = urllib.request.Request(self.url + "/api/v1/" + path, headers=self.auth)
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))


def kill_state(exchange: str, dry_run: bool) -> dict:
    mode = "dryrun" if dry_run else "live"
    p = ROOT / "freqtrade" / "user_data" / f"kill_switch_state.{exchange}.{mode}.json"
    if not p.exists():
        return {"killed": False, "halted_today": False}
    s = json.loads(p.read_text(encoding="utf-8"))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {"killed": bool(s.get("killed")), "halted_today": s.get("halted_day") == today}


def trade_line(t: dict, open_: bool) -> str:
    coin = t.get("pair", "?").split("/")[0]
    side = "숏" if t.get("is_short") else "롱"
    lev = t.get("leverage") or 1
    if open_:
        pct = (t.get("profit_ratio") or 0) * 100
        return f"{coin} {side} {lev:g}배, {kst(t.get('open_date'))} 진입, 지금 {pct:+.1f}% ({sign(t.get('profit_abs') or 0)} USDT)"
    pct = (t.get("profit_ratio") or t.get("close_profit") or 0) * 100
    return (f"{coin} {side} {lev:g}배, {kst(t.get('close_date'))} 정리 "
            f"{pct:+.1f}% ({sign(t.get('profit_abs') or t.get('close_profit_abs') or 0)} USDT), 이유: {t.get('exit_reason') or '-'}")


def build_status(ft: Freqtrade) -> dict:
    cfg = ft.get("show_config")
    open_trades = ft.get("status")
    profit = ft.get("profit")
    daily = ft.get("daily?timescale=1").get("data") or [{}]
    today = daily[0]
    dry = bool(cfg.get("dry_run", True))
    ex = cfg.get("exchange", "?")
    ks = kill_state(ex, dry)
    st = {
        "ok": True,
        "mode": "모의매매" if dry else "실거래",
        "dry_run": dry,
        "exchange": ex,
        "bot_state": cfg.get("state", "?"),
        "strategy": cfg.get("strategy", "?"),
        "currency": cfg.get("stake_currency", "USDT"),
        "kill_switch": ks["killed"],
        "halted_today": ks["halted_today"],
        "open_trades": [trade_line(t, True) for t in open_trades],
        "today_profit": round(float(today.get("abs_profit") or 0), 2),
        "today_trades": int(today.get("trade_count") or 0),
        "total_profit": round(float(profit.get("profit_all_coin") or 0), 2),
        "closed_trades": int(profit.get("closed_trade_count") or 0),
        "win": int(profit.get("winning_trades") or 0),
        "loss": int(profit.get("losing_trades") or 0),
        "checked_at": datetime.now(KST).strftime("%m/%d %H:%M"),
    }
    parts = [f"{st['mode']} 중({ex})"]
    if st["bot_state"] != "running":
        parts.append(f"봇 상태: {st['bot_state']}")
    if ks["killed"]:
        parts.append("⛔ 비상정지 상태")
    elif ks["halted_today"]:
        parts.append("오늘은 일일 손실 한도로 매매 중지")
    parts.append(f"열린 포지션 {len(open_trades)}개" + (": " + "; ".join(st["open_trades"]) if open_trades else ""))
    parts.append(f"오늘 {sign(st['today_profit'])} {st['currency']}, 누적 {sign(st['total_profit'])} {st['currency']}"
                 f" (정리된 거래 {st['closed_trades']}건, {st['win']}승 {st['loss']}패)")
    st["say_hint"] = ". ".join(parts) + "."
    return st


def build_trades(ft: Freqtrade, limit: int) -> dict:
    data = ft.get(f"trades?limit={max(1, min(limit, 50))}")
    closed = [t for t in data.get("trades", []) if not t.get("is_open")]
    closed.sort(key=lambda t: t.get("close_timestamp") or 0, reverse=True)
    lines = [trade_line(t, False) for t in closed[:limit]]
    return {"ok": True, "trades": lines,
            "say_hint": ("최근 정리된 거래: " + "; ".join(lines)) if lines else "아직 정리된 거래가 없어요."}


def build_signal_report(kind: str) -> dict:
    """signals/score.py · levia_sim.py 결과 중 가장 최근 보고서 요약."""
    prefix = {"levybot": "signals_", "levia": "levia_"}.get(kind)
    if not prefix:
        return {"ok": False, "say_hint": "kind는 levybot 또는 levia 중 하나여야 해요."}
    dirs = sorted((ROOT / "reports").glob(prefix + "*"), key=lambda p: p.stat().st_mtime, reverse=True)
    real = [d for d in dirs if not d.name.endswith("_demo")]
    pick = (real or dirs or [None])[0]
    if pick is None or not (pick / "report.md").exists():
        return {"ok": True, "found": False,
                "say_hint": "아직 채점 보고서가 없어요. 텔레그램 기록을 가져와서 채점을 먼저 돌려야 해요."}
    text = (pick / "report.md").read_text(encoding="utf-8")
    lines = [l for l in text.splitlines() if l.strip()][:40]
    return {"ok": True, "found": True, "report": pick.name, "demo": pick.name.endswith("_demo"),
            "made_at": datetime.fromtimestamp(pick.stat().st_mtime, KST).strftime("%m/%d %H:%M"),
            "excerpt": "\n".join(lines),
            "say_hint": f"가장 최근 보고서는 {pick.name}"
                        + (" (가짜 데이터 데모)" if pick.name.endswith("_demo") else "")
                        + ". 아래 발췌를 읽고 요약해서 말해 주세요."}


# ------------------------------------------------------------------ 알림 감시
class Watcher(threading.Thread):
    """일정 간격으로 봇을 살펴서 '말해 줄 만한 일'을 알림으로 쌓아 둡니다.
    Yuri의 기상 루프가 /events?since=번호 로 가져가면 됩니다."""

    def __init__(self, ft: Freqtrade | None, every: int, fail_limit: int = 3):
        super().__init__(daemon=True)
        self.ft, self.every, self.fail_limit = ft, every, fail_limit
        self.events: deque = deque(maxlen=200)
        self.next_id = 1
        self.lock = threading.Lock()
        self.prev: dict | None = None  # 직전 상태 (첫 확인 때는 알림 없이 기준만 잡음)
        self.fails = 0
        self.down = False
        self.last_live = False

    def push(self, kind: str, text: str, urgent: bool = False):
        with self.lock:
            self.events.append({"id": self.next_id, "kind": kind, "text": text, "urgent": urgent,
                                "live": self.last_live, "at": datetime.now(KST).strftime("%m/%d %H:%M")})
            self.next_id += 1
        log.info(f"알림: {kind} {text}")

    def since(self, n: int) -> list:
        with self.lock:
            return [e for e in self.events if e["id"] > n]

    def snapshot(self) -> dict:
        cfg = self.ft.get("show_config")
        dry = bool(cfg.get("dry_run", True))
        self.last_live = not dry
        trades = {t["trade_id"]: t for t in self.ft.get("status")}
        return {"dry": dry, "state": cfg.get("state"), "trades": trades,
                "ks": kill_state(cfg.get("exchange", "?"), dry)}

    def check(self):
        try:
            cur = self.snapshot()
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            self.fails += 1
            log.debug(f"봇 확인 실패 {self.fails}회: {e}")
            if self.fails >= self.fail_limit and not self.down:
                self.down = True
                self.push("bot_down", "선물봇 응답이 없어요. PC나 Docker가 꺼졌는지 확인이 필요해요.",
                          urgent=self.last_live)
            return
        self.fails = 0
        if self.down:
            self.down = False
            self.push("bot_up", "선물봇이 다시 응답해요.")
        p, self.prev = self.prev, cur
        if p is None:
            return
        mode = "모의" if cur["dry"] else "실거래"
        for tid, t in cur["trades"].items():
            if tid not in p["trades"]:
                self.push("trade_opened", f"[{mode}] 새 포지션: {trade_line(t, True)}")
        closed_ids = [tid for tid in p["trades"] if tid not in cur["trades"]]
        if closed_ids:
            done = {t["trade_id"]: t for t in self.ft.get("trades?limit=50").get("trades", [])}
            for tid in closed_ids:
                t = done.get(tid, p["trades"][tid])
                self.push("trade_closed", f"[{mode}] 포지션 정리: {trade_line(t, False)}")
        if cur["ks"]["killed"] and not p["ks"]["killed"]:
            self.push("kill_switch", f"[{mode}] ⛔ 비상정지가 걸렸어요. 고점 대비 손실 한도에 닿아 모든 포지션을 정리하고 멈췄어요.",
                      urgent=not cur["dry"])
        if cur["ks"]["halted_today"] and not p["ks"]["halted_today"]:
            self.push("daily_limit", f"[{mode}] 오늘 손실 한도에 닿아 오늘은 매매를 멈췄어요.", urgent=not cur["dry"])
        if cur["state"] != p["state"]:
            self.push("bot_state", f"[{mode}] 봇 상태가 '{p['state']}' → '{cur['state']}'로 바뀌었어요.")

    def run(self):
        while True:
            if self.ft is not None:
                self.check()
            time.sleep(self.every)


# ------------------------------------------------------------------ Yuri용 도구 설명
TOOLS = [
    {"type": "function", "function": {
        "name": "futures_status",
        "description": "찬주님의 선물 자동매매 봇 현재 상태를 확인한다. 모의/실거래 여부, 열린 포지션, 오늘·누적 손익, 비상정지 여부. "
                       "선물봇·코인 매매·수익을 물어보면 사용.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "futures_recent_trades",
        "description": "선물봇에서 최근에 정리(청산)된 거래 목록을 가져온다.",
        "parameters": {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "몇 건까지 (1~20, 기본 5)"}}}}},
    {"type": "function", "function": {
        "name": "futures_signal_report",
        "description": "텔레그램 시그널 방을 따라 했을 때의 채점 보고서(가장 최근) 요약을 가져온다. "
                       "levybot = 방장 포지션 오픈·종료 시그널, levia = LEVIA 코어 알림 + 가이드북 규칙.",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["levybot", "levia"]}}, "required": ["kind"]}}},
]

RULES = ("선물봇 관련 규칙: 숫자는 도구 결과 그대로만 말하고 지어내지 않는다. "
         "매수·매도·레버리지 같은 투자 권유는 하지 않는다. 주문·정지는 할 수 없으니, 원하면 찬주님이 직접 하도록 안내한다. "
         "모의매매 결과는 '연습 결과'라고 분명히 말한다.")


# ------------------------------------------------------------------ HTTP 서버
def make_handler(ft: Freqtrade | None, watcher: Watcher):
    def guard(fn):
        if ft is None:
            return {"ok": False, "say_hint": "선물봇 비밀번호 파일(freqtrade/config.private.json)이 없어서 봇을 볼 수 없어요."}
        try:
            return fn()
        except (urllib.error.URLError, OSError) as e:
            return {"ok": False, "say_hint": "선물봇이 꺼져 있거나 응답이 없어요.", "error": str(e)}

    routes = {
        "/health": lambda q: {"ok": True, "version": VERSION, "bot_reachable": not watcher.down and ft is not None},
        "/status": lambda q: guard(lambda: build_status(ft)),
        "/trades": lambda q: guard(lambda: build_trades(ft, int(q.get("limit", ["5"])[0]))),
        "/signals": lambda q: build_signal_report(q.get("kind", ["levia"])[0]),
        "/events": lambda q: {"ok": True, "events": watcher.since(int(q.get("since", ["0"])[0])),
                              "last_id": watcher.next_id - 1},
        "/tools": lambda q: {"ok": True, "tools": TOOLS, "rules": RULES},
    }

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self.send_error(403)
            u = urlparse(self.path)
            fn = routes.get(u.path)
            if fn is None:
                return self.send_error(404)
            try:
                body = fn(parse_qs(u.query))
                code = 200
            except ValueError:
                body, code = {"ok": False, "say_hint": "요청 값이 잘못됐어요."}, 400
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass
    return H


def main():
    ap = argparse.ArgumentParser(description="Yuri ↔ 선물봇 연결 다리 (읽기 전용)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--ft-url", default="http://127.0.0.1:8080")
    ap.add_argument("--ft-secrets", default=str(ROOT / "freqtrade" / "config.private.json"))
    ap.add_argument("--every", type=int, default=30, help="봇 확인 간격(초)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    sec = Path(args.ft_secrets)
    ft = Freqtrade(args.ft_url, sec) if sec.exists() else None
    if ft is None:
        log.warning(f"{sec} 이 없어요. freqtrade 폴더에서 python setup_secrets.py 를 먼저 실행하세요. (시그널 보고서만 볼 수 있음)")
    watcher = Watcher(ft, args.every)
    watcher.start()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(ft, watcher))
    log.info(f"Yuri 연결 다리 켜짐: http://127.0.0.1:{args.port}/health  (끄려면 Ctrl+C)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
