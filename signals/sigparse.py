"""텔레그램 시그널 메시지 해석.

지원 형식 (LevyBot 스타일):
    🟢 롱포지션 오픈 감지 🟢 / 🔴 숏포지션 오픈 감지 🔴
    💸 심볼 : XXXUSDT.P
    🪙 진입가격 : 1.000 USDT
    ⚡️레버리지 : 5배
    ⏰ 진입시각 : 12:34:56 UTC+9

    🎯 롱포지션 종료 감지 🎯
    💸 심볼 : XXXUSDT.P
    🪙 전체 평균 종료가격 : 1.010 USDT
    ⚡️레버리지 : 5배
    📈 실현 수익률 : 5.00%

형식이 조금 달라도(이모지·띄어쓰기·'롱/숏/Long/Short') 최대한 읽도록 느슨하게 맞춥니다.
처음 보는 형식은 kind='unknown'으로 남겨서 나중에 확인할 수 있게 해요.
"""
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

_NUM = r"([-+]?\d[\d,]*\.?\d*)"
RE_HEAD = re.compile(r"(롱|숏|long|short)\s*포?지?션?\s*(?:position\s*)?(오픈|진입|open|종료|청산|close)", re.I)
RE_SYMBOL = re.compile(r"심볼\s*[:：]\s*([A-Z0-9]+?)(?:USDT|USDC)?(?:\.P|PERP)?\b", re.I)
RE_SYMBOL_RAW = re.compile(r"심볼\s*[:：]\s*([A-Z0-9.]+)", re.I)
RE_ENTRY = re.compile(r"진입\s*가격\s*[:：]\s*" + _NUM)
RE_EXIT = re.compile(r"종료\s*가격\s*[:：]\s*" + _NUM)
RE_LEV = re.compile(r"레버리지\s*[:：]\s*" + _NUM + r"\s*배")
RE_RET = re.compile(r"수익률\s*[:：]\s*" + _NUM + r"\s*%")
RE_TIME = re.compile(r"진입\s*시각\s*[:：]\s*(\d{1,2}):(\d{2}):(\d{2})\s*(?:UTC\s*([+-]\d+))?")


def _f(m):
    return float(m.group(1).replace(",", "")) if m else None


@dataclass
class Signal:
    ts: datetime                 # 메시지가 올라온 시각 (UTC)
    kind: str                    # open | close | unknown
    side: str | None = None      # long | short
    symbol: str | None = None    # 기준 코인 (예: ADA)
    pair: str | None = None      # ccxt 선물 표기 (예: ADA/USDT:USDT)
    price: float | None = None   # 진입가 또는 평균 종료가 (시그널에 적힌 값)
    leverage: float | None = None
    reported_return: float | None = None  # 시그널이 말한 실현 수익률 (레버리지 반영, %)
    entry_time: datetime | None = None    # 시그널에 적힌 진입시각 (UTC)
    msg_id: int | str | None = None
    raw: str = field(default="", repr=False)

    def to_dict(self):
        d = asdict(self)
        for k in ("ts", "entry_time"):
            if d[k] is not None:
                d[k] = d[k].isoformat()
        return d


def parse(text: str, ts: datetime, msg_id=None) -> Signal | None:
    """메시지 하나를 해석. 시그널과 무관한 메시지면 None."""
    if not text or "심볼" not in text:
        return None
    ts = ts.astimezone(timezone.utc)
    head = RE_HEAD.search(text)
    sym = RE_SYMBOL.search(text)
    if not head or not sym:
        return Signal(ts=ts, kind="unknown", msg_id=msg_id, raw=text)
    side = "long" if head.group(1).lower() in ("롱", "long") else "short"
    kind = "open" if head.group(2).lower() in ("오픈", "진입", "open") else "close"
    base = sym.group(1).upper()
    s = Signal(ts=ts, kind=kind, side=side, symbol=base, pair=f"{base}/USDT:USDT",
               leverage=_f(RE_LEV.search(text)), msg_id=msg_id, raw=text)
    if kind == "open":
        s.price = _f(RE_ENTRY.search(text))
        t = RE_TIME.search(text)
        if t:
            tz = timezone(timedelta(hours=int(t.group(4)))) if t.group(4) else KST
            local = ts.astimezone(tz)
            et = local.replace(hour=int(t.group(1)), minute=int(t.group(2)), second=int(t.group(3)), microsecond=0)
            if et > local + timedelta(minutes=5):  # 자정 넘김 (메시지가 0시 직후, 진입은 전날 23시대)
                et -= timedelta(days=1)
            s.entry_time = et.astimezone(timezone.utc)
    else:
        s.price = _f(RE_EXIT.search(text))
        s.reported_return = _f(RE_RET.search(text))
    if s.price is None:
        s.kind = "unknown"
    return s


@dataclass
class Trade:
    pair: str
    side: str
    open: Signal
    close: Signal | None = None

    @property
    def leverage(self):
        return self.open.leverage or (self.close.leverage if self.close else None)


def pair_trades(signals: list[Signal]):
    """오픈→종료 짝짓기. 같은 종목·방향에 오픈이 겹치면(추가 진입) 첫 오픈 기준으로 묶고 기록.
    반환: (trades, 경고 목록)"""
    open_pos: dict[tuple, Trade] = {}
    trades, warnings = [], []
    for s in sorted((x for x in signals if x.kind in ("open", "close")), key=lambda x: x.ts):
        key = (s.pair, s.side)
        if s.kind == "open":
            if key in open_pos:
                warnings.append(f"{s.ts:%Y-%m-%d %H:%M} {s.pair} {s.side}: 종료 없이 다시 오픈 (추가 진입으로 보고 첫 진입 유지)")
                continue
            opp = (s.pair, "short" if s.side == "long" else "long")
            if opp in open_pos:
                warnings.append(f"{s.ts:%Y-%m-%d %H:%M} {s.pair}: 반대 포지션이 열린 상태에서 {s.side} 오픈")
            open_pos[key] = Trade(pair=s.pair, side=s.side, open=s)
        else:
            t = open_pos.pop(key, None)
            if t is None:
                warnings.append(f"{s.ts:%Y-%m-%d %H:%M} {s.pair} {s.side}: 오픈 기록 없이 종료 (기록 시작 전 진입으로 보고 제외)")
                continue
            t.close = s
            trades.append(t)
    for t in open_pos.values():
        trades.append(t)  # 아직 열려 있는 포지션
    trades.sort(key=lambda t: t.open.ts)
    return trades, warnings
