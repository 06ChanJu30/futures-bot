# Freqtrade 모의매매 → 실거래 안내

견고성 검사를 통과한 **일봉 모멘텀 앙상블(TsmomEnsemble)** 을 Freqtrade로 운영합니다.
모의매매(dry-run)와 실거래는 **같은 전략 파일, 같은 설정**을 쓰고 `dry_run` 값만 달라요.

| 항목 | 값 |
|---|---|
| 종목 | BTC/USDT, ETH/USDT 무기한 선물 (바이낸스, 격리 마진) |
| 판단 시각 | 매일 1번, 일봉 마감 직후 (UTC 0시 = 한국 오전 9시) |
| 진입 | 10·30·60일 수익률 방향 평균이 플러스면 롱 (숏 없음) |
| 크기 | 페어별 자본 × 40% ÷ 최근 30일 변동성 × 신호 강도, 레버리지 1~2배 |
| 손절 | ATR×5 추적 손절, **거래소에 손절 주문을 걸어둠** (봇이 꺼져도 작동) |
| 안전장치 | 하루 손실 5% → 그날 정지 / 고점 대비 30% → 영구 정지 |

> 이 저장소 개발 환경에서 Freqtrade 2026.9로 백테스트를 돌려 확인했어요.
> 같은 데이터에서 기존 백테스트 엔진과 진입일 75/79건 일치, 체결가 동일, 비상정지 동작, 미래참조 검사 통과.

---

## 1단계. 설치 (처음 한 번)

1. **Docker Desktop 설치**: https://www.docker.com/products/docker-desktop/ → 설치 후 **반드시 재부팅**
2. 명령 프롬프트에서:

```
cd futures-bot
git pull
cd freqtrade
python setup_secrets.py
docker compose pull
docker compose run --rm freqtrade create-userdir --userdir user_data
```

`setup_secrets.py`가 출력하는 **웹 화면 아이디/비밀번호를 메모**해 두세요 (`config.private.json`에 저장, 깃허브에 안 올라감).

## 2단계. 실제 데이터로 교차 확인 (권장, 10분)

Freqtrade 자체 백테스트로 우리 결과와 비슷한지 확인해요.

```
docker compose run --rm freqtrade download-data --config /freqtrade/config.json --timerange 20200101- -t 1d
docker compose run --rm freqtrade backtesting --config /freqtrade/config.json --strategy TsmomEnsemble --timerange 20210301-
docker compose run --rm freqtrade lookahead-analysis --config /freqtrade/config.json --strategy TsmomEnsemble --timerange 20210301-
```

- 백테스트는 BTC+ETH를 자본 반씩 나눠 운용한 결과라 `robustness.py`(종목별 전체 자본)와 숫자가 똑같진 않아요. **방향이 비슷하면(플러스, 낙폭 20~25% 안팎) 정상**이에요.
- `lookahead-analysis`에서 `has_bias`가 `No`면 정상.
- 결과 화면을 저(Claude)에게 보여주면 같이 확인해 드려요.

## 3단계. 모의매매 시작

```
docker compose up -d
```

- 웹 화면: http://localhost:8080 → 아이디/비밀번호 입력 (+ 버튼으로 봇 추가 시 주소 `http://127.0.0.1:8080`)
- 로그 보기: `docker compose logs -f` (Ctrl+C로 빠져나옴, 봇은 계속 돌아감)
- 멈추기: `docker compose down` / 다시 켜기: `docker compose up -d`
- **PC 절전 모드 끄기** (설정 → 시스템 → 전원 → 절전 '안 함'). 모의매매는 PC가 켜져 있을 때만 진행돼요.
- 일봉 전략이라 **며칠 동안 거래가 없어도 정상**이에요. 신호가 켜져 있으면 다음 날 오전 9시쯤 진입해요.

### 모의매매 평가 기준 (최소 2~3개월)

- [ ] 오류 없이 매일 판단이 돌아감 (로그에 에러가 반복되지 않음)
- [ ] 진입/청산이 규칙대로 일어남 (점수 > 0 진입, ≤ 0 청산, 손절 주문이 걸림)
- [ ] 최대낙폭이 백테스트 범위(약 -25%) 안
- [ ] 기간 중 BTC/ETH 흐름 대비 결과가 백테스트 성격(상승장 일부 추종, 하락장 회피)과 맞음

**중간에 설정을 바꾸지 마세요.** 결과를 보고 고치면 과최적화로 돌아가요.

---

## 4단계. 실거래 준비 (모의매매 통과 후)

### 4-1. 바이낸스 계정 설정
- 선물 계정 활성화, USDT를 선물 지갑으로 이체 (**잃어도 괜찮은 금액만**)
- 선물 설정: **포지션 모드 = One-way(단방향)**, **자산 모드 = Single-Asset(단일 자산)**, 마진 = 격리(Isolated)
  (Freqtrade가 시작할 때 확인하고 틀리면 에러로 알려줘요)
- 최소 주문 금액 때문에 **BTC+ETH 운영은 대략 1,000 USDT 이상**이 필요해요. 이보다 적으면 진입이 자주 생략돼요 (로그에 표시). 금액이 작으면 `pair_whitelist`를 BTC 하나로 줄이세요.

### 4-2. API 키 만들기 (보안 중요)
- 권한: **선물(Futures) 거래만 허용**, **출금(Withdraw) 절대 허용 안 함**
- **IP 제한 설정** (봇이 도는 PC/서버의 IP만 허용)
- 키는 누구에게도(저 포함) 보여주지 마세요. 대화창에 붙여넣지 마세요.

### 4-3. 텔레그램 알림 (권장)
- 텔레그램에서 @BotFather → `/newbot` → 토큰 받기
- 만든 봇에게 아무 메시지 보낸 뒤 @userinfobot 으로 내 chat_id 확인

### 4-4. 실거래 설정 파일
`config.live.example.json`을 복사해서 `config.live.json`으로 이름을 바꾸고 키·토큰을 채워요 (이 파일은 깃허브에 안 올라감).

### 4-5. 실거래 시작
```
docker compose down
docker compose -f docker-compose.yml -f docker-compose.live.yml up -d
```
- 모의매매 기록과 실거래 기록은 다른 DB 파일에 따로 저장돼요.
- 처음 며칠은 매일 텔레그램 알림과 바이낸스 앱에서 포지션·손절 주문을 직접 확인하세요.

### 4-6. 운영 환경
Freqtrade 공식 문서는 **Windows+Docker를 실거래용으로 권장하지 않아요** (시계 어긋남, 재부팅 문제).
실거래는 월 5~10달러 정도의 **리눅스 VPS**로 옮기는 걸 권장해요. 옮길 때가 되면 같이 설정해 드릴게요.
(손절 주문은 거래소에 걸려 있어서 PC가 꺼져도 손절은 작동하지만, 신호 청산·신규 진입은 멈춰요.)

---

## 비상 상황

| 상황 | 할 일 |
|---|---|
| 봇을 당장 멈추고 싶다 | `docker compose down` — **포지션은 그대로 남아요** |
| 포지션까지 전부 정리하고 싶다 | 웹 화면에서 Force exit(전체 청산) 후 멈추기, 또는 바이낸스 앱에서 직접 청산 |
| 비상정지(30%)가 발동했다 | 자동으로 전부 청산되고 신규 진입 중지. 원인 확인 전까지 해제하지 마세요. 해제: `user_data/kill_switch_state.json` 삭제 후 재시작 |
| `Timestamp ... outside of the recvWindow` 에러 | Windows 시계 문제. Docker Desktop 종료 → 명령 프롬프트에서 `wsl --shutdown` → Docker Desktop 다시 실행 |
| 로그에 에러가 계속 난다 | `docker compose logs --tail 100` 결과를 저(Claude)에게 보여주세요 |

## 백테스트 엔진(fbot)과 다른 점

- **일일 손실 한도**: 백테스트는 하루 마감 때만 확인, 실제 운영은 실시간 확인 (더 보수적)
- **주문 수량**: 거래소 최소 단위로 반올림되어 백테스트와 수 % 차이
- 손절은 거래소 주문으로 걸리므로 급변동 시 체결가가 손절가보다 불리할 수 있음

## 참고
- 바이낸스는 한국 거주자 대상 서비스에 규제 이슈가 있을 수 있어요. 이용 가능 여부와 세금(해외 거래소 수익 신고 등)은 직접 확인하세요. 저는 법률·재무 자문가가 아니에요.
- Freqtrade 문서: https://www.freqtrade.io/en/stable/
