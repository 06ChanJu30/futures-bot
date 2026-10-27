# futures-bot — Claude용 프로젝트 맥락

## 사용자
- 비개발자. **한국어**로, 쉬운 말로 설명. 명령어는 Windows 명령 프롬프트(cmd) 기준으로 그대로 복사해 쓸 수 있게
- 소액으로 실험하는 암호화폐 무기한 선물 자동매매. 목표: 모의매매 → 소액 실거래
- 수익 과장 금지. 결과가 나쁘면 나쁘다고 말하기. 법률·세무·투자 자문 아님을 필요 시 언급
- API 키·비밀번호를 대화에 붙여넣지 않도록 안내. 로그에 키가 보이면 지우라고 말하기

## 현재 상태 → `HANDOFF.md` (작업지시서), 변경 이력 → `CHANGELOG.md`
- 채택 전략: **일봉 모멘텀 앙상블** `tsmom_ensemble(lookbacks=[10,30,60], short_mode="none", atr_mult=5.0)`
- 다음 할 일: 노트북에 Freqtrade 설치 → 실제 데이터 교차 확인 → 모의매매 2~3개월
- 별도 트랙: 텔레그램 시그널 — `signals/` 1단계(기록·채점)만 있음, 매매 연결은 아직 없음
  - ① LevyBot: 방장 포지션 오픈·종료 → `score.py` ② LEVIA 코어: L/S·기준가·중단선(종료 없음) → `levia_sim.py`가 서포터 가이드북 규칙으로 시뮬레이션
  - 가이드북 규칙을 바꾸면 `tools/test_levia_sim.py`도 같이 고치고 통과 확인
  - 시그널 보고 수익률 = 가격 변동 × 레버리지 (수수료 미포함). 따라하기 성적은 반드시 지연·수수료 반영해서 판단
  - 채점 통과(60~100건, 실제 따라하기 PF 1.3+, 가격 검증 OK) 전에는 실거래 연결을 만들지 말 것

## 구조
| 경로 | 내용 |
|---|---|
| `fbot/` | 자체 백테스트 엔진 (engine: 봉 단위 시뮬, strategies, walkforward, stats: DSR, report) |
| `run_backtest.py` | 전략 후보 전체 워크포워드 검증 (4h·1d) |
| `robustness.py` | 채택 전략 견고성 검사 (다종목·주변설정·비용) |
| `check_lookahead.py` | 미래 참조 검사 |
| `config.yaml` | fbot 설정 (거래소·비용·리스크·전략 후보) |
| `freqtrade/` | 실운영: `TsmomEnsemble.py` 전략, `config.json`, `exchanges/*.json`, Docker, `GUIDE.md` |
| `tools/ft_offline_test.py` | 거래소 접속 없이 5개 거래소 설정으로 Freqtrade 백테스트 (가짜 데이터) |
| `signals/` | 텔레그램 시그널: `sigparse.py` 해석, `importer.py` 가져오기, `listen.py` 실시간 기록(Telethon), `score.py` 채점, `config.yaml` |
| `RESEARCH.md` | 조사 내용·출처 |

## 지켜야 할 것
- **채택 전략의 설정을 바꾸지 말 것.** 바꾸려면 사용자에게 과최적화 위험을 설명하고 fbot에서 워크포워드+견고성 검사부터 다시
- `fbot/strategies.py`의 `tsmom_ensemble`과 `freqtrade/.../TsmomEnsemble.py`는 **같은 규칙**이어야 함. 한쪽을 고치면 다른 쪽도 고치고 `tools/ft_offline_test.py`로 확인
- 신호는 봉 마감 후 계산 → 다음 봉 시가 체결 (미래 참조 금지). 수정 후 `check_lookahead.py` 실행
- 비밀정보 파일(`config.private.json`, `config.live.json`, `.env`, `signals/telegram.env`, 텔레그램 세션)과 `user_data/`·`signals/data/` 기록은 커밋 금지 (.gitignore 있음)
- 사용자가 공유한 시그널 방 메시지 원문은 저장소에 커밋하지 말 것 (테스트는 가짜 데이터로)
- 커밋 메시지는 한국어, 변경 시 `CHANGELOG.md`와 필요하면 `HANDOFF.md` 갱신

## 검증 방법
```
python run_backtest.py --synthetic        # fbot 동작 확인 (가짜 데이터)
python robustness.py --synthetic
python check_lookahead.py --synthetic
pip install freqtrade && python tools/ft_offline_test.py   # Freqtrade 전략 + 5개 거래소 설정
python signals/score.py --demo            # 시그널 채점 (가짜 시그널·시세)
python tools/test_levia_sim.py            # 가이드북 규칙 검증 15개
python signals/levia_sim.py --demo        # LEVIA 시뮬레이션 데모
```
실제 데이터는 `python fetch_data.py`(바이낸스 공개 API, 키 불필요)로 받음.

## Freqtrade 메모 (2026.9 기준)
- 거래소 선택: `freqtrade/.env`의 `EXCHANGE` → compose가 `exchanges/${EXCHANGE}.json`을 설정에 겹침
- 설정 순서: `config.json` → `exchanges/<거래소>.json` → `config.private.json` → (실거래) `config.live.json`
- 비상정지 상태: `user_data/kill_switch_state.<거래소>.<dryrun|live>.json`
- Windows+Docker는 실거래 비권장(공식 문서) → 실거래 시 리눅스 VPS 제안
