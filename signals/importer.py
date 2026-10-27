"""과거 대화 가져오기 → signals/data/signals.csv

두 가지 형식을 지원해요.
1) 텔레그램 데스크톱 '대화 내보내기' 결과 (result.json)  ← 권장 (초 단위 시각)
   텔레그램 데스크톱 → 방 열기 → 오른쪽 위 ⋮ → 'Export chat history'
   → 사진·파일 체크 해제, Format: 'Machine-readable JSON' → 내보내기
2) 대화를 복사해 붙여넣은 텍스트 파일 (.txt)  ← 분 단위 시각
   [2026-10-19 오전 5:16] 보낸사람: 로 시작하는 형식

  python signals/importer.py 경로/result.json
  python signals/importer.py 경로/대화.txt
"""
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sigparse import KST, Signal, parse  # noqa: E402

OUT = Path(__file__).resolve().parent / "data" / "signals.csv"
FIELDS = ["ts", "kind", "side", "symbol", "pair", "price", "leverage", "reported_return", "entry_time",
          "levels", "calc_time", "source", "msg_id", "raw"]


def _text(t):
    if isinstance(t, str):
        return t
    return "".join(x if isinstance(x, str) else x.get("text", "") for x in t)


def from_json(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    for m in d.get("messages", []):
        if m.get("type") != "message":
            continue
        ts = datetime.fromtimestamp(int(m["date_unixtime"]), timezone.utc) if "date_unixtime" in m \
            else datetime.fromisoformat(m["date"]).replace(tzinfo=KST)
        s = parse(_text(m.get("text", "")), ts, m.get("id"), m.get("from"))
        if s:
            yield s


RE_LINE = re.compile(r"^\[(\d{4})-(\d{2})-(\d{2})\s+(오전|오후|AM|PM)?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\]\s*([^:]*):\s*(.*)$")


def from_txt(path):
    cur, buf, sender = None, [], None

    def flush():
        if cur:
            s = parse("\n".join(buf), cur, source=sender)
            if s:
                yield s

    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = RE_LINE.match(line.strip())
        if m:
            yield from flush()
            y, mo, d, ap, h, mi, se = m.group(1, 2, 3, 4, 5, 6, 7)
            h = int(h)
            if ap in ("오후", "PM") and h != 12:
                h += 12
            if ap in ("오전", "AM") and h == 12:
                h = 0
            cur = datetime(int(y), int(mo), int(d), h, int(mi), int(se or 0), tzinfo=KST)
            buf = [m.group(9)]
            sender = m.group(8).strip()
        else:
            buf.append(line)
    yield from flush()


def _key(r):
    """중복 판단 기준: 같은 분 + 종목 + 오픈/종료 + 방향 (+ 해석 못 한 메시지는 내용)"""
    minute = datetime.fromisoformat(r["ts"]).strftime("%Y-%m-%dT%H:%M")
    extra = r["raw"][:80] if r["kind"] == "unknown" else (str(r["price"]) if r["kind"] == "alert" else "")
    return (minute, r["pair"], r["kind"], r["side"], extra)


def save(signals: list[Signal], path=OUT, append=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    old = {}
    if append and path.exists():
        with path.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                old[_key(r)] = r
    for s in signals:
        d = s.to_dict()
        k = _key(d)
        # 같은 메시지가 텍스트(분 단위)와 JSON(초 단위)으로 두 번 들어오면 초 단위 쪽을 남김
        if k in old and datetime.fromisoformat(old[k]["ts"]).second and not s.ts.second:
            continue
        old[k] = d
    rows = sorted(old.values(), key=lambda r: r["ts"])
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in FIELDS})
    return len(rows)


def _dt(v):
    return datetime.fromisoformat(v) if v not in (None, "", "None") else None


def load(path=OUT) -> list[Signal]:
    out = []
    if not path.exists():
        return out
    with path.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            num = lambda k: float(r[k]) if r.get(k) not in (None, "", "None") else None  # noqa: E731
            out.append(Signal(
                ts=datetime.fromisoformat(r["ts"]), kind=r["kind"], side=r["side"] or None,
                symbol=r["symbol"] or None, pair=r["pair"] or None, price=num("price"),
                leverage=num("leverage"), reported_return=num("reported_return"),
                entry_time=_dt(r.get("entry_time")), calc_time=_dt(r.get("calc_time")),
                levels=[float(x) for x in r["levels"].split(";")] if r.get("levels") not in (None, "", "None") else None,
                source=r.get("source") or None, msg_id=r.get("msg_id"), raw=r.get("raw", "")))
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    p = Path(sys.argv[1])
    sigs = list(from_json(p) if p.suffix.lower() == ".json" else from_txt(p))
    n = save(sigs, append=True)
    kinds = {}
    for s in sigs:
        kinds[s.kind] = kinds.get(s.kind, 0) + 1
    print(f"읽은 시그널: {len(sigs)}개 {kinds}  →  {OUT} (누적 {n}개)")
    unk = [s for s in sigs if s.kind == "unknown"]
    if unk:
        print(f"\n⚠️ 형식을 못 읽은 메시지 {len(unk)}개 (예시 1개):\n{unk[0].raw[:300]}")


if __name__ == "__main__":
    main()
