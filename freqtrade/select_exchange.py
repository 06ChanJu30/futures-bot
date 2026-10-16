"""사용할 거래소를 고르고, 그 거래소에 필요한 준비사항을 보여줍니다.

  python select_exchange.py            # 지원 거래소 목록과 현재 선택
  python select_exchange.py bybit      # bybit로 변경 (.env 파일에 저장)

바꾼 뒤에는 봇을 다시 켜야 적용돼요:  docker compose down  →  docker compose up -d
모의매매/실거래 기록과 비상정지 상태는 거래소별로 따로 저장돼요.
"""
import sys
from pathlib import Path

HERE = Path(__file__).parent

INFO = {
    "binance": [
        "선물 설정: 포지션 모드 'One-way(단방향)', 자산 모드 'Single-Asset(단일 자산)' (Freqtrade가 확인만 하고 바꾸진 않음)",
        "API 권한: 선물(Futures) 거래 허용, 출금 금지, IP 제한",
        "BTC 선물 최소 주문 금액이 큰 편이라 BTC+ETH는 약 1,000 USDT 이상 권장",
    ],
    "bybit": [
        "봇 전용 '서브계정'을 만들어서 사용 (같은 계정에서 수동 거래·다른 봇 금지)",
        "Freqtrade가 시작할 때 서브계정을 One-way 모드로 자동 설정",
        "API 권한: Read-Write, Contract-Orders, Contract-Positions / 출금 금지, IP 제한 강력 권장",
        "Bybit는 펀딩비 내역을 주지 않아서, 실거래 펀딩비도 추정치로 계산됨 (실제 손익은 거래소 기준)",
    ],
    "okx": [
        "API 만들 때 정한 passphrase를 config.live.json의 'password'에 입력",
        "my.okx.com(유럽 OKX EEA) 계정이면 exchanges/okx.json의 name을 'myokx'로 변경",
        "포지션 모드는 Buy/Sell(단방향) 권장, 운영 중에 바꾸지 말 것",
        "OKX는 과거 마크가격 데이터가 3개월치뿐이라 Freqtrade 백테스트의 펀딩비가 부정확할 수 있음",
    ],
    "bitget": [
        "API 만들 때 정한 passphrase를 config.live.json의 'password'에 입력",
        "Freqtrade가 시작할 때 계정을 One-way 모드로 자동 설정 → 봇 전용 서브계정 권장",
        "API 권한: 선물 거래 허용, 출금 금지, IP 제한",
    ],
    "gate": [
        "API 권한: Perpetual Futures(읽기·쓰기), Wallet(읽기), Account(읽기) — 없으면 시작 시 권한 오류",
        "거래소 손절 주문은 지정가(limit)만 지원 → exchanges/gate.json에 이미 설정됨",
        "수수료를 POINT로 내면 자동 계산이 안 됨 → POINT 결제 끄기 권장",
    ],
}


def current():
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("EXCHANGE="):
                return line.split("=", 1)[1].strip()
    return "binance (기본값)"


def main():
    if len(sys.argv) < 2:
        print(f"현재 거래소: {current()}\n\n지원: {', '.join(INFO)}\n예: python select_exchange.py bybit")
        return
    ex = sys.argv[1].strip().lower()
    if ex not in INFO:
        print(f"지원하지 않는 거래소예요: {ex}\n지원: {', '.join(INFO)}")
        sys.exit(1)
    if not (HERE / "exchanges" / f"{ex}.json").exists():
        print(f"exchanges/{ex}.json 파일이 없어요. git pull 을 먼저 해주세요.")
        sys.exit(1)
    (HERE / ".env").write_text(f"EXCHANGE={ex}\n", encoding="utf-8")
    print(f"거래소를 '{ex}'(으)로 설정했어요 (.env 저장)\n\n[{ex} 준비사항]")
    for line in INFO[ex]:
        print(f"  - {line}")
    print("\n적용: docker compose down  →  docker compose up -d")
    print(f"백테스트 데이터도 거래소별로 따로 받아야 해요:\n"
          f"  docker compose run --rm freqtrade download-data --config /freqtrade/config.json "
          f"--config /freqtrade/exchanges/{ex}.json --timerange 20200101- -t 1d")


if __name__ == "__main__":
    main()
