"""Yuri 쪽에 복사해서 쓰는 파일 (이 파일 하나만 있으면 됨, 표준 라이브러리만 사용).

Yuri 프로젝트에 이 파일을 복사한 뒤:

    from yuri_client import FuturesBridge
    fb = FuturesBridge()                      # http://127.0.0.1:8765

    # 1) 대화 중 도구로 쓰기 (Ollama 도구 호출 형식 그대로)
    tools = fb.tools()                        # ollama.chat(..., tools=tools)
    text  = fb.call_tool(name, arguments)     # 모델이 도구를 부르면 결과 문장을 돌려줌
    rules = fb.rules()                        # 시스템 프롬프트에 한 줄 덧붙이기

    # 2) 기상 루프에서 알림 가져오기
    new = fb.new_events()                     # 지난번 이후 새로 생긴 알림만
    for e in new: e["text"], e["urgent"]      # urgent=True는 실거래 비상 상황

선물봇(연결 다리)이 꺼져 있어도 예외를 던지지 않고 "꺼져 있어요" 문장을 돌려줍니다.
→ Yuri가 게임 모드든 뭐든 이 파일 때문에 멈추는 일은 없습니다.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

OFF = {"ok": False, "say_hint": "선물봇 연결 다리가 꺼져 있어요. (선물봇 PC 쪽에서 bridge.py가 실행 중인지 확인)"}


class FuturesBridge:
    def __init__(self, url: str = "http://127.0.0.1:8765", state_file: str | Path = "futures_bridge_seen.json",
                 timeout: float = 4.0):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.state_file = Path(state_file)

    def _get(self, path: str, **params) -> dict:
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        try:
            with urllib.request.urlopen(self.url + path + q, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            return dict(OFF)

    # ---- 바로 쓰는 함수
    def alive(self) -> bool:
        return bool(self._get("/health").get("ok"))

    def status(self) -> dict:
        return self._get("/status")

    def trades(self, limit: int = 5) -> dict:
        return self._get("/trades", limit=limit)

    def signal_report(self, kind: str = "levia") -> dict:
        return self._get("/signals", kind=kind)

    # ---- 대화 도구
    def tools(self) -> list:
        return self._get("/tools").get("tools", [])

    def rules(self) -> str:
        return self._get("/tools").get("rules", "")

    def call_tool(self, name: str, arguments: dict | None = None) -> str:
        a = arguments or {}
        if name == "futures_status":
            r = self.status()
        elif name == "futures_recent_trades":
            r = self.trades(int(a.get("limit", 5)))
        elif name == "futures_signal_report":
            r = self.signal_report(a.get("kind", "levia"))
            if r.get("found"):
                return r["say_hint"] + "\n" + r["excerpt"]
        else:
            return f"모르는 도구예요: {name}"
        return r.get("say_hint", json.dumps(r, ensure_ascii=False))

    # ---- 기상 루프용 알림
    def new_events(self) -> list:
        seen = 0
        try:
            seen = json.loads(self.state_file.read_text(encoding="utf-8")).get("last_id", 0)
        except Exception:
            pass
        r = self._get("/events", since=seen)
        if not r.get("ok"):
            return []
        last = r.get("last_id", seen)
        if last < seen:          # 연결 다리가 다시 켜져서 번호가 처음부터 시작된 경우
            r = self._get("/events", since=0)
            last = r.get("last_id", 0)
        evs = r.get("events", [])
        self.state_file.write_text(json.dumps({"last_id": last}), encoding="utf-8")
        return evs


if __name__ == "__main__":
    fb = FuturesBridge()
    print("연결:", "OK" if fb.alive() else "꺼져 있음")
    print(fb.call_tool("futures_status"))
