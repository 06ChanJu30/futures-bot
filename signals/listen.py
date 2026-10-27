"""텔레그램 방의 시그널을 실시간으로 기록 (매매는 하지 않음).

준비 (처음 한 번)
  1) https://my.telegram.org → 로그인 → 'API development tools' → 앱 만들기 → api_id, api_hash 확인
  2) signals/telegram.env.example 을 복사해 signals/telegram.env 로 저장하고 값 입력
     (이 파일과 로그인 세션 파일은 깃허브에 올라가지 않아요. 다른 사람에게 보여주지 마세요)
  3) pip install telethon

사용
  python signals/listen.py --list              # 내 대화방 목록 → 시그널 방의 ID 확인 (여러 방은 쉼표로)
  python signals/listen.py --history 90        # 지난 90일 메시지를 한 번에 가져와 저장 (내보내기 대신)
  python signals/listen.py                     # 실시간 기록 시작 (Ctrl+C로 종료)

처음 실행하면 전화번호와 텔레그램 로그인 코드를 물어봐요 (한 번만).
실시간 기록을 켤 때마다, 꺼져 있던 동안 놓친 메시지도 자동으로 채워 넣어요.
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from importer import OUT, load, save  # noqa: E402
from sigparse import parse  # noqa: E402

ENV = HERE / "telegram.env"
SESSION = HERE / "data" / "telegram"


def read_env():
    if not ENV.exists():
        sys.exit(f"{ENV} 파일이 없어요. telegram.env.example 을 복사해서 만들어 주세요.")
    vals = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    for k in ("API_ID", "API_HASH"):
        if not vals.get(k):
            sys.exit(f"telegram.env 에 {k} 값을 넣어주세요.")
    return vals


def chat_refs(v):
    """CHAT=방ID1,방ID2,... (여러 방 동시 기록)"""
    items = [x.strip() for x in (v or "").split(",") if x.strip()]
    if not items:
        sys.exit("telegram.env 의 CHAT 에 방 ID를 넣어주세요. (python signals/listen.py --list 로 확인)")
    return [int(x) if x.lstrip("-").isdigit() else x for x in items]


def log(msg):
    print(f"[{datetime.now():%m-%d %H:%M:%S}] {msg}", flush=True)


async def run(args):
    from telethon import TelegramClient, events

    env = read_env()
    SESSION.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(SESSION), int(env["API_ID"]), env["API_HASH"])
    await client.start()

    if args.list:
        print("내 대화방 목록 (시그널 방의 ID를 telegram.env 의 CHAT= 에 넣으세요)\n")
        async for d in client.iter_dialogs():
            print(f"{d.id:>16}  {d.name}")
        return

    chats = [await client.get_entity(c) for c in chat_refs(env.get("CHAT"))]
    from telethon.utils import get_peer_id
    names = {get_peer_id(c): getattr(c, "title", None) or getattr(c, "first_name", "") for c in chats}
    log("대상 방: " + ", ".join(names.values()))

    def handle(msg, quiet=False):
        src = names.get(msg.chat_id)
        s = parse(msg.message or "", msg.date, msg.id, src)
        if s is None:
            return None
        save([s], append=True)
        if not quiet:
            if s.kind == "unknown":
                log(f"⚠️ 형식을 못 읽은 메시지 (id {msg.id}) — 기록만 해둠")
            elif s.kind == "alert":
                log(f"🚨 LEVIA {s.symbol} {'L' if s.side == 'long' else 'S'} 기준가 {s.price} 중단선 {s.levels}")
            else:
                extra = f" 수익률 {s.reported_return}%" if s.reported_return is not None else ""
                log(f"{'🟢' if s.kind == 'open' else '🎯'} {s.symbol} {s.side} {s.kind} {s.price} x{s.leverage}{extra}")
        return s

    # 과거 / 놓친 메시지 채우기
    if args.history:
        since = datetime.now(timezone.utc) - timedelta(days=args.history)
    else:
        prev = load()
        since = max((s.ts for s in prev), default=datetime.now(timezone.utc) - timedelta(days=1))
    n = 0
    for chat in chats:
        async for msg in client.iter_messages(chat, reverse=True, offset_date=since):
            if handle(msg, quiet=True):
                n += 1
    log(f"{since:%Y-%m-%d %H:%M} UTC 이후 메시지 {n}개 저장 → {OUT}")
    if args.history:
        return

    @client.on(events.NewMessage(chats=chats))
    async def on_new(event):
        handle(event.message)

    log("실시간 기록 중... (Ctrl+C로 종료)")
    await client.run_until_disconnected()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="대화방 목록 보기")
    ap.add_argument("--history", type=int, metavar="DAYS", help="지난 N일 메시지를 가져와 저장하고 종료")
    args = ap.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        log("종료")


if __name__ == "__main__":
    main()
