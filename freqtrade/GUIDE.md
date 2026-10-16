# Freqtrade 모의매매 → 실거래 안내

견고성 검사를 통과한 **일봉 모멘텀 앙상블(TsmomEnsemble)** 을 Freqtrade로 운영합니다.
모의매매(dry-run)와 실거래는 **같은 전략 파일, 같은 설정**을 쓰고 `dry_run` 값만 달라요.

| 항목 | 값 |
|---|---|
| 거래소 | **binance · bybit · okx · bitget · gate** 중 선택 (USDT 무기한 선물, 격리 마진) |
| 종목 | BTC/USDT, ETH/USDT (`config.json`의 `pair_whitelist`) |
| 판단 시각 | 매일 1번, 일봉 마감 직후 (UTC 0시 = 한국 오전 9시) |
| 진입 | 10·30·60일 수익률 방향 평균이 플러스면 롱 (숏 없음) |
| 크기 | 페어별 자본 × 40% ÷ 최근 30일 변동성 × 신호 강도, 레버리지 1~2배 |
| 손절 | ATR×5 추적 손절, **거래소에 손절 주문을 걸어둠** (봇이 꺼져도 작동) |
| 안전장치 | 하루 손실 5% → 그날 정지 / 고점 대비 30% → 영구 정지 |

> 검증: Freqtrade 2026.9로 백테스트해 기존 엔진과 진입일 75/79건 일치, 체결가 동일, 비상정지 동작,
> 미래참조 검사 통과. 5개 거래소 설정 모두 오프라인 백테스트 정상 (`tools/ft_offline_test.py`).

## 파일 구성

| 파일 | 역할 | 깃허브 |
|---|---|---|
| `config.json` | 공통 설정 (모의매매, 종목, 자본 1,000 USDT) | 올라감 |
| `exchanges/<거래소>.json` | 거래소별 설정 | 올라감 |
| `.env` | 지금 쓰는 거래소 (`select_exchange.py`가 만듦) | ❌ 안 올라감 |
| `config.private.json` | 웹 화면 비밀번호 (`setup_secrets.py`가 만듦) | ❌ 안 올라감 |
| `config.live.json` | 실거래 API 키 (`config.live.example.json` 복사해서 작성) | ❌ 안 올라감 |
| `user_data/` | 거래 기록 DB, 로그, 데이터, 비상정지 상태 (거래소·모드별로 따로) | ❌ 안 올라감 |

> 노트북·VPS 등 **다른 컴퓨터로 옮길 때 ❌ 파일들은 직접 복사**하거나 다시 만들어야 해요.

---

## 1단계. 설치 (처음 한 번)

1. **Docker Desktop 설치**: https://www.docker.com/products/docker-desktop/ → 설치 후 **반드시 재부팅**
2. 명령 프롬프트에서:

```
cd futures-bot
git pull
cd freqtrade
python setup_secrets.py
python select_exchange.py bybit
docker compose pull
docker compose run --rm freqtrade create-userdir --userdir user_data
```

- `setup_secrets.py`가 출력하는 **웹 화면 아이디/비밀번호를 메모**
- `select_exchange.py` 뒤에 쓸 거래소 이름(binance, bybit, okx, bitget, gate). 실행하면 **그 거래소 준비사항**이 나와요
- 거래소를 바꾸고 싶으면 언제든 `python select_exchange.py 이름` → 봇 재시작

## 2단계. 실제 데이터로 교차 확인 (권장, 10분)

`<거래소>` 자리에 고른 거래소 이름을 넣으세요 (예: bybit).

```
docker compose run --rm freqtrade download-data --config /freqtrade/config.json --config /freqtrade/exchanges/<거래소>.json --timerange 20200101- -t 1d
docker compose run --rm freqtrade backtesting --config /freqtrade/config.json --config /freqtrade/exchanges/<거래소>.json --strategy TsmomEnsemble --timerange 20210301-
docker compose run --rm freqtrade lookahead-analysis --config /freqtrade/config.json --config /freqtrade/exchanges/<거래소>.json --strategy TsmomEnsemble --timerange 20210301-
```

- BTC+ETH를 자본 반씩 나눈 결과라 `robustness.py`(종목별 전체 자본)와 숫자가 똑같진 않아요. **플러스이고 낙폭 20~25% 안팎이면 정상**.
- `lookahead-analysis`의 `has_bias`가 `No`면 정상.
- 거래소마다 데이터 시작 시점이 달라요. OKX는 과거 마크가격이 3개월치뿐이라 펀딩비 계산이 부정확해요 → 교차 확인은 binance나 bybit 데이터로 하는 게 정확해요.

## 3단계. 모의매매 시작

```
docker compose up -d
```

- 웹 화면: http://localhost:8080 → 아이디/비밀번호 입력 (봇 추가 시 주소 `http://127.0.0.1:8080`)
- 로그: `docker compose logs -f` (Ctrl+C로 빠져나옴, 봇은 계속 돌아감)
- 멈추기 `docker compose down` / 다시 켜기 `docker compose up -d`
- **노트북 절전·최대 절전 끄기**, 덮개를 닫아도 꺼지지 않게 설정 (전원 옵션 → 덮개를 닫으면 '아무 것도 안 함'), **전원 연결 상태 유지**
- 일봉 전략이라 **며칠 동안 거래가 없어도 정상**

### 모의매매 평가 기준 (최소 2~3개월)

- [ ] 오류 없이 매일 판단이 돌아감
- [ ] 진입/청산이 규칙대로 (점수 > 0 진입, ≤ 0 청산, 손절 주문이 걸림)
- [ ] 최대낙폭이 백테스트 범위(약 -25%) 안
- [ ] 결과가 백테스트 성격(상승장 일부 추종, 하락장 회피)과 맞음

**중간에 설정을 바꾸지 마세요.** 결과를 보고 고치면 과최적화로 돌아가요.

---

## 4단계. 실거래 준비 (모의매매 통과 후)

### 4-1. 거래소 계정
- `python select_exchange.py <거래소>` 출력의 **준비사항**을 그대로 따르세요 (포지션 모드, 서브계정, API 권한 등)
- USDT를 선물(파생상품) 지갑으로 이체 — **잃어도 괜찮은 금액만**
- 최소 주문 금액 때문에 **BTC+ETH 운영은 대략 1,000 USDT 이상** 권장. 적으면 진입이 자주 생략돼요(로그에 표시). 그럴 땐 `pair_whitelist`를 BTC 하나로.

### 4-2. API 키 (보안 중요)
- **출금(Withdraw) 권한 절대 켜지 않기**, **IP 제한** 설정
- 키는 누구에게도(저 포함) 보여주지 말고, 대화창에 붙여넣지 마세요

### 4-3. 텔레그램 알림 (권장)
- @BotFather → `/newbot` → 토큰 / 만든 봇에게 메시지 보낸 뒤 @userinfobot 으로 chat_id 확인

### 4-4. 실거래 설정 파일
`config.live.example.json` → 복사해서 `config.live.json`으로 저장 → 키·토큰 입력.
`password` 줄은 **OKX·Bitget만** 필요해요. 나머지 거래소는 그 줄을 지우세요.

### 4-5. 실거래 시작
```
docker compose down
docker compose -f docker-compose.yml -f docker-compose.live.yml up -d
```
- 기록 DB·로그·비상정지 상태는 `거래소.모드` 별로 따로 저장돼요 (예: `tradesv3.bybit.live.sqlite`)
- 처음 며칠은 매일 텔레그램 알림과 거래소 앱에서 포지션·손절 주문을 직접 확인하세요

### 4-6. 운영 환경
Freqtrade 공식 문서는 **Windows+Docker를 실거래용으로 권장하지 않아요** (시계 어긋남, 재부팅 문제).
실거래는 월 5~10달러 정도의 **리눅스 VPS** 권장. 옮길 때가 되면 같이 설정해 드릴게요.
(손절 주문은 거래소에 걸려 있어서 컴퓨터가 꺼져도 작동하지만, 신호 청산·신규 진입은 멈춰요.)

---

## 비상 상황

| 상황 | 할 일 |
|---|---|
| 봇을 당장 멈추고 싶다 | `docker compose down` — **포지션은 그대로 남아요** |
| 포지션까지 전부 정리 | 웹 화면 Force exit(전체 청산) 후 멈추기, 또는 거래소 앱에서 직접 청산 |
| 비상정지(30%) 발동 | 자동 전량 청산·신규 진입 중지. 원인 확인 전 해제 금지. 해제: `user_data/kill_switch_state.<거래소>.<dryrun\|live>.json` 삭제 후 재시작 |
| `Timestamp ... outside of the recvWindow` | Windows 시계 문제. Docker Desktop 종료 → `wsl --shutdown` → Docker Desktop 다시 실행 |
| 권한/모드 오류로 시작 안 됨 | `select_exchange.py` 준비사항 재확인 (포지션 모드, API 권한) |
| 에러가 계속 난다 | `docker compose logs --tail 100` 결과를 Claude에게 보여주세요 (**API 키가 찍혀 있으면 지우고**) |

## 백테스트 엔진(fbot)과 다른 점
- 일일 손실 한도: 백테스트는 하루 마감 때만, 실제 운영은 실시간 (더 보수적)
- 주문 수량: 거래소 최소 단위로 반올림되어 수 % 차이
- 손절은 거래소 주문이라 급변동 시 체결가가 손절가보다 불리할 수 있음 (Gate는 지정가 손절이라 급락 시 미체결 가능성)
- Bybit는 펀딩비 내역을 주지 않아 Freqtrade가 추정치로 계산 (실제 손익은 거래소 기준)

## 참고
- 해외 거래소는 한국 거주자 대상 서비스에 규제 이슈가 있을 수 있어요. 이용 가능 여부와 세금(해외 거래소 수익 신고 등)은 직접 확인하세요. 저는 법률·재무 자문가가 아니에요.
- Freqtrade 거래소별 문서: https://www.freqtrade.io/en/stable/exchanges/
