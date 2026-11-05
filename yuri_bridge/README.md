# Yuri ↔ 선물봇 연결 다리 (읽기 전용)

Yuri가 선물봇에 **물어보고**, 봇에 생긴 일을 **알림으로 받아** 찬주님께 전해 주는 연결입니다.
**주문·청산·정지 기능은 없습니다.** 보기만 합니다.

```
[선물봇: Freqtrade (Docker)] ──127.0.0.1:8080──▶ [bridge.py] ──127.0.0.1:8765──▶ [Yuri: yuri_client.py]
[시그널 채점 보고서: reports/] ─────────────────▶     (읽기 전용)
```

- 둘 다 **같은 개인 PC**에서 돌아간다는 전제입니다 (127.0.0.1 = 내 PC 안에서만 열림).
  나중에 실거래를 Lightsail 같은 서버로 옮기면 이 연결은 그대로는 안 닿습니다 → 그때 사설 연결(Tailscale 등)로 다시 설계.
- 추가 설치 없음 (파이썬 기본 기능만 사용).
- GPU를 쓰지 않아서 **Yuri 게임 모드와 상관없이** 계속 켜 둬도 됩니다.

---

## 1. 선물봇 쪽 (이 저장소)

처음 한 번: Freqtrade 웹 화면 비밀번호 파일이 있어야 합니다 (이미 했다면 생략).
```
cd %USERPROFILE%\futures-bot\freqtrade
python setup_secrets.py
```

연결 다리 켜기 (선물봇 Docker가 켜진 상태에서):
```
cd %USERPROFILE%\futures-bot
python yuri_bridge\bridge.py
```
`Yuri 연결 다리 켜짐: http://127.0.0.1:8765/health` 가 보이면 성공. 브라우저로 그 주소를 열어 `"ok": true` 가 나오는지 확인하세요.

동작 검사 (봇 없이 가짜 데이터로):
```
python tools\test_yuri_bridge.py
```

윈도우 켤 때 자동 실행하려면: 작업 스케줄러 → 기본 작업 만들기 → "로그온할 때" →
프로그램 `pythonw.exe`, 인수 `yuri_bridge\bridge.py`, 시작 위치 `C:\Users\(내 사용자 이름)\futures-bot`

---

## 2. Yuri 쪽 — 붙이는 방법 (Yuri의 Claude Code에게 이 부분을 보여 주세요)

1. `yuri_bridge/yuri_client.py` 파일 **하나만** Yuri 프로젝트(예: `C:\Yuri\core\`)에 복사
2. 대화 처리에 도구로 연결
   ```python
   from yuri_client import FuturesBridge
   fb = FuturesBridge()

   system_prompt += "\n" + fb.rules()          # 숫자 지어내지 않기·투자 권유 금지 규칙
   resp = ollama.chat(model=..., messages=..., tools=fb.tools())
   for call in resp.message.tool_calls or []:
       result = fb.call_tool(call.function.name, call.function.arguments)
       messages.append({"role": "tool", "content": result})   # 다시 chat 호출해서 Yuri 말투로 답하기
   ```
   - 선택한 대화 모델이 **도구 호출을 잘 못하면**: "선물/코인/봇/수익" 같은 단어가 나왔을 때 `fb.call_tool("futures_status")` 결과를 그냥 대화 맥락에 넣어 줘도 됩니다.
3. 기상 루프에 알림 연결
   ```python
   for e in fb.new_events():        # 지난번 이후 새 알림만 (본 것은 futures_bridge_seen.json에 기록)
       # e["kind"]: trade_opened / trade_closed / kill_switch / daily_limit / bot_down / bot_up / bot_state
       # e["text"]: 그대로 말해도 되는 한국어 문장, e["urgent"]: 실거래 비상 상황이면 True
       문지기에게 넘기기(e)            # 조용한 시간·게임 중이면 메모지로
   ```
4. 연결 다리가 꺼져 있어도 `yuri_client.py`는 **예외를 던지지 않고** "꺼져 있어요" 문장을 돌려줍니다. Yuri가 멈출 일은 없습니다.

### 문지기 규칙 제안 (Yuri 쪽에서 정함)

| 알림 | 평소 | 조용한 시간(23시~) · 게임 중 |
|---|---|---|
| 진입·청산 (모의매매) | 하루 묶어서 저녁에 한 번 전해도 충분 | 메모지 |
| 진입·청산 (실거래) | 바로 선톡 | 메모지 |
| 비상정지·일일 한도 (실거래, `urgent`) | 바로 선톡 | **찬주님이 정할 것**: 깨워도 되는지, 메모지로 둘지 |
| 봇 꺼짐 | 바로 선톡 | 메모지 |

---

## 3. 주소 목록 (모두 GET, 127.0.0.1에서만)

| 주소 | 내용 |
|---|---|
| `/health` | 연결 다리·봇 응답 여부 |
| `/status` | 모의/실거래, 열린 포지션, 오늘·누적 손익, 비상정지 + `say_hint`(바로 말할 수 있는 문장) |
| `/trades?limit=5` | 최근 정리된 거래 |
| `/signals?kind=levia` · `kind=levybot` | 가장 최근 시그널 채점 보고서 발췌 (데모인지 표시) |
| `/events?since=번호` | 새 알림 (연결 다리가 30초마다 봇 확인) |
| `/tools` | Yuri용 도구 설명(Ollama 형식) + 말하기 규칙 |

시각은 모두 한국 시간. 알림 기록은 연결 다리를 껐다 켜면 처음부터 다시 쌓입니다(그동안의 일은 `/trades`로 확인).
