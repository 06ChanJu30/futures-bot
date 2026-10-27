# 텔레그램 시그널 따라하기 — 1단계: 기록·채점

텔레그램 방(LevyBot 형식)의 롱/숏 진입·종료 시그널을 모아서
**"그대로 따라 했으면 실제로 얼마였을까"** 를 거래소 1분봉 시세로 계산해요. **매매는 하지 않아요.**

## 왜 채점부터 하나
- 시그널이 말하는 수익률은 **수수료 빼기 전, 레버리지 곱한 값**이에요 (예: 가격 +1% × 5배 = 5%)
- 따라 하는 쪽은 메시지를 받고 주문하기까지 **지연**이 있어서 진입가가 불리해져요
- 하루 3~4건이면 거래당 0.2~0.3%만 손해 봐도 하루 1% 가까이 빠져요
- 그래서 시그널 성적과 **실제 따라하기 성적**을 같이 비교해 봐야 해요

## 준비
```
cd futures-bot
git pull
pip install -r requirements.txt
```

## 방법 A: 과거 대화를 한 번에 가져오기 (가장 빠름)

**A-1. 텔레그램 데스크톱 내보내기 (권장, 초 단위 시각)**
1. PC용 텔레그램 데스크톱 → 시그널 방 열기 → 오른쪽 위 ⋮ → **Export chat history**
2. 사진·영상·파일 체크 **해제**, Format: **Machine-readable JSON**, 기간: 가능한 길게
3. 저장된 폴더의 `result.json`을 가져오기:
```
python signals/importer.py C:\Users\TOP\Downloads\Telegram Desktop\ChatExport_날짜\result.json
```

**A-2. 대화를 복사해 붙여넣은 텍스트** (`[2026-10-19 오전 5:16] AI비서 LevyBot:` 형식, 분 단위)
```
python signals/importer.py 대화.txt
```

같은 메시지를 두 방법으로 넣어도 중복 저장되지 않아요.

## 방법 B: 실시간 기록 (앞으로 올라오는 시그널)

1. https://my.telegram.org → 로그인 → **API development tools** → 앱 만들기 → `api_id`, `api_hash` 확인
2. `signals/telegram.env.example`을 복사해 `signals/telegram.env`로 저장하고 값 입력
3. 방 ID 찾기 → `telegram.env`의 `CHAT=`에 입력
```
python signals/listen.py --list
```
4. 지난 기록 가져오기 (내보내기 대신 써도 돼요) / 실시간 기록 시작
```
python signals/listen.py --history 90
python signals/listen.py
```
- 처음 실행 시 전화번호와 로그인 코드를 물어봐요 (한 번만)
- 껐다 켜면 꺼져 있던 동안의 메시지도 자동으로 채워요
- ⚠️ `telegram.env`와 `signals/data/telegram.session`은 **내 텔레그램 계정 열쇠**예요. 절대 공유하지 마세요 (깃허브에 안 올라가게 되어 있어요)

## 채점
```
python signals/score.py
python signals/score.py --since 2026-09-01
```
→ `reports/signals_날짜/report.md`, `equity.png`, `trades.csv`

| 리포트 항목 | 의미 |
|---|---|
| 요약 | 시그널 그대로 vs 실제 따라하기 (승률, 총수익, 최대낙폭) |
| 거래당 비용 | 수수료 + 지연 때문에 시그널보다 손해 본 정도 |
| 신뢰도 점검 | 시그널 진입가가 실제 거래소 가격 범위 안이었는지 (밖이면 다른 거래소 가격이거나 사후 수정 의심) |
| 최대 역행폭 | 진입 후 가장 불리했던 가격 변동 → 시그널 레버리지였으면 강제청산 났을 거래 수 |
| 보호 손절 후보 | 봇 자체 손절(2·3·5·8%)을 걸었다면 어땠을지 → 2단계 손절값 결정용 |
| 종목별·롱/숏별 | 특정 코인이나 방향에서만 잘 되는지 |

조건(지연 시간, 수수료, 증거금 비율, 최대 레버리지 3배)은 `signals/config.yaml`에서 바꿔요.
시세 거래소는 기본 바이낸스 — 실제로 쓸 거래소로 맞추는 게 정확해요.

동작 확인만: `python signals/score.py --demo` (가짜 시그널·시세)

## 판단 기준 (2단계 모의매매로 넘어갈지)
- [ ] 완료 거래 **최소 60~100건** (하루 3~4건이면 3~4주)
- [ ] **실제 따라하기** 총수익이 플러스, 손익비(PF) 1.3 이상
- [ ] 시그널 진입가가 거래소 범위 밖인 건이 거의 없음
- [ ] 수익이 한두 번의 대박 거래나 한 종목에만 몰려 있지 않음

## 다음 단계 (예정)
2단계에서는 실시간 기록기가 시그널을 받으면 Freqtrade에 주문을 넣도록 연결해요
(모의매매 → 소액 실거래, 레버리지 상한·보호 손절·일일 한도·비상정지 적용).
