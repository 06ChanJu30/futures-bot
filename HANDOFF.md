# 작업지시서 — 노트북에서 이어서 하기

마지막 업데이트: 2026-10-27 (v0.8). 바뀐 내용은 `CHANGELOG.md` 참고.

## 지금까지 한 일 (한 줄 요약)
과거 데이터로 전략 10여 개를 검증 → **일봉 모멘텀 앙상블(10·30·60일)** 하나가 견고성 검사 통과 →
Freqtrade로 옮겨서 **모의매매 직전 상태**. 거래소 5곳(binance·bybit·okx·bitget·gate) 중 골라서 쓸 수 있음.
**별도 트랙**: 텔레그램 시그널 — ① LevyBot(방장 포지션 오픈·종료) 따라하기 채점 ② LEVIA 코어 알림(L/S·기준가·중단선) × 서포터 가이드북 규칙 시뮬레이션. 도구 완성, 실제 데이터 넣어서 채점할 차례.

거래소 조사 결론(10/16): 모의매매는 binance(기본값), 실거래는 binance 1순위·OKX 예비.
실거래 전 확인: ① 계정에서 USDT 무기한 선물 주문 가능 ② 해외 거래소로 실제 송금 가능 (국내 거래소 출금 제한 강화).

## 진행 상황

| 단계 | 상태 |
|---|---|
| 1. 백테스트 엔진 만들기·조사·전략 검증 | ✅ 완료 |
| 2. 견고성 검사 | ✅ 3/4 통과 (주변 설정 민감도 약점) |
| 3. Freqtrade 이식·다중 거래소 | ✅ 완료 (개발 환경에서 오프라인 검증) |
| 4. **노트북 설치 + 실제 데이터 교차 확인** | ⬜ **← 여기부터** |
| 5. 모의매매 2~3개월 | ⬜ |
| 6. 실거래 소액 시작 (가능하면 리눅스 VPS) | ⬜ |

**텔레그램 시그널 트랙** (`signals/README.md`)

| 단계 | 상태 |
|---|---|
| S1. 기록·채점 도구 (LevyBot `score.py`, LEVIA `levia_sim.py`) | ✅ 완료 (예시 메시지·가짜 시세·규칙 테스트 15개로 검증) |
| S2. **과거 대화 가져와서 채점** | ⬜ **← 여기부터** (아래 C-6) |
| S3. 실시간 기록 3~4주 (60~100건) | ⬜ |
| S4. 성적 통과 시 Freqtrade 연결 → 모의매매 | ⬜ (아직 안 만듦) |

---

## A. 노트북 준비 (처음 한 번, 30분)

1. **Git 설치**: https://git-scm.com/download/win (설치 옵션은 기본값 그대로)
2. **Python 설치**: https://www.python.org/downloads/ — 설치 첫 화면에서 **"Add python.exe to PATH" 체크**
3. **Docker Desktop 설치**: https://www.docker.com/products/docker-desktop/ → **재부팅**
4. 명령 프롬프트(cmd)에서:

```
cd %USERPROFILE%
git clone https://github.com/06ChanJu30/futures-bot
cd futures-bot
pip install -r requirements.txt
```

> 저장소를 Private으로 만들었다면 clone할 때 GitHub 로그인 창이 떠요. 로그인하면 돼요.

5. 노트북 전원 설정: **절전·최대 절전 끄기**, **덮개를 닫아도 '아무 것도 안 함'**, 전원 어댑터 연결 유지

## B. 이전 PC에서 가져올 것 (선택)

깃허브에 안 올라가는 파일이라 필요하면 USB 등으로 직접 옮겨요. 없으면 새로 만들면 돼요.

| 파일 | 없으면? |
|---|---|
| `data/` 폴더 (백테스트용 시세) | `python fetch_data.py` 로 다시 받기 (몇 분) |
| `freqtrade/config.private.json` | `python setup_secrets.py` 로 새로 만들기 |
| `freqtrade/user_data/` | 아직 모의매매 전이라 옮길 것 없음 |
| `signals/telegram.env`, `signals/data/` | 텔레그램 로그인 정보·수집한 시그널. 없으면 다시 작성하고 `--history`로 다시 가져오기 |

## C. 이어서 할 작업 (순서대로)

### C-1. 백테스트 엔진 동작 확인 (5분)
```
cd %USERPROFILE%\futures-bot
python fetch_data.py
python robustness.py
```
→ 판정 3/4, BTC +177% 근처가 나오면 정상 (데이터가 하루씩 늘어서 숫자는 조금 달라도 OK)

### C-2. 거래소 고르기 + Freqtrade 설치
```
cd freqtrade
python setup_secrets.py
python select_exchange.py bybit
docker compose pull
docker compose run --rm freqtrade create-userdir --userdir user_data
```
- `bybit` 자리에 쓸 거래소. 출력되는 **준비사항** 확인
- 웹 화면 비밀번호 메모

### C-3. 실제 데이터 교차 확인 → **결과를 Claude에게 보여주기**
`freqtrade/GUIDE.md` **2단계** 명령 3줄 실행 (download-data → backtesting → lookahead-analysis)

### C-4. 모의매매 시작
```
docker compose up -d
```
웹 화면 http://localhost:8080 에서 확인. 이후 **2~3개월 설정 변경 없이** 운영.

### C-5. 모의매매 점검 (2주마다 권장)
웹 화면에서 거래 내역·수익 곡선 캡처, 또는 `docker compose logs --tail 200` → Claude에게 보여주고 평가 기준(GUIDE.md 3단계) 체크

### C-6. 텔레그램 시그널 채점 (시그널 트랙)
```
python signals/importer.py result.json경로     (방마다: LevyBot 방, Levia Core BTC 방, AltCoin 방)
python signals/score.py                        (LevyBot)
python signals/levia_sim.py                    (LEVIA 알림 × 가이드북 규칙)
```
- `result.json`: 텔레그램 데스크톱 → 방 → ⋮ → Export chat history (JSON, 사진 제외)
- 또는 실시간 기록: `signals/telegram.env` 작성 후 `python signals/listen.py --list` → `--history 90` → `python signals/listen.py`
- `reports/signals_날짜/report.md`, `reports/levia_날짜/report.md` 를 Claude에게 보여주고 판단 기준(signals/README.md) 체크

---

## D. 노트북에서 Claude와 이어서 작업하는 법

- **Claude Code / Claude 앱에서 `futures-bot` 폴더를 열면** `CLAUDE.md`가 자동으로 읽혀서 프로젝트 맥락을 바로 알아요
- 첫 메시지 예시: **"HANDOFF.md 읽고 C-3부터 이어서 하자"**
- 결과 공유: 명령 프롬프트 출력 전체를 복사해서 붙여넣거나 파일로 첨부
- ⚠️ API 키·비밀번호·`config.live.json` 내용은 절대 붙여넣지 마세요

## E. 정해둔 원칙 (바꾸지 않기)
1. 전략 설정(10·30·60일, ATR×5, 목표 변동성 40%, 최대 2배)은 **고정**. 결과 보고 고치면 과최적화
2. 모의매매 최소 2~3개월 → 평가 기준 통과해야 실거래
3. 실거래는 잃어도 되는 금액만, API 출금 권한 금지, IP 제한
4. 숏은 쓰지 않음 (견고성 검사에서 숏 없는 설정이 통과)
5. BNB·XRP 같은 '횡보 후 급등형' 코인에는 안 맞음 → 종목은 BTC·ETH (SOL은 선택)

## F. 남은 과제 (나중에)
- [ ] 실거래 시 리눅스 VPS로 이전 (Freqtrade는 Windows+Docker 실거래 비권장)
- [ ] 텔레그램 알림 연결
- [ ] 시그널 트랙 S4: listen.py → Freqtrade REST API(forceenter/forceexit) 연결, 레버리지 상한·보호 손절(채점 결과로 결정)·일일 한도, 모멘텀 전략과 자금 분리·반대 포지션 충돌 규칙
- [ ] 모의매매 결과 vs 백테스트 비교 도구 (같은 기간 신호가 일치하는지 자동 확인)
- [ ] 주변 설정 민감도 약점 원인 분석 (`reports/*_robustness/report.md` 민감도 표 확인)
