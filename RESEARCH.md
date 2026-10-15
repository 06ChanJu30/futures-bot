# 조사 노트 (2026-10-15)

1차 백테스트(1시간봉, 단순 지표 3종)가 모두 탈락한 뒤, 기존 오픈소스 봇과 연구·사례를 조사해 반영한 내용이에요.

## 1. 기존 오픈소스 시스템

| 이름 | 특징 | 이 프로젝트에 반영한 것 |
|---|---|---|
| Freqtrade | 가장 큰 커뮤니티. 백테스트·하이퍼옵트·모의매매(dry-run)·실거래. 선물(`trading_mode: futures`, 격리/교차 마진), 펀딩비 반영, 청산 버퍼 | 2단계 모의매매/실거래 플랫폼으로 사용 예정. `lookahead-analysis`를 본떠 `check_lookahead.py` 작성 |
| FreqAI (Freqtrade 내장) | 특징값+라벨로 ML 모델 학습, 정해진 주기로 재학습, 이상치 제거 | `ml_gbm` 전략 (워크포워드 재학습, 라벨 겹침 제거) |
| Jesse | 연구 중심 프레임워크, 워크포워드 최적화 내장 | 워크포워드 구조 유지 |
| Hummingbot | 마켓메이킹·차익거래 특화 | 방향성 매매와 성격이 달라 제외 |
| TradingAgents (LLM 멀티에이전트) | 분석가·토론·리스크 에이전트가 협의. 주식 대상, 3개월 시험 | 매 봉마다 LLM 호출은 비용·속도·검증 문제로 제외. 나중에 하루 1회 뉴스/심리 보조 판단 정도로 검토 가능 |

## 2. 연구·사례에서 나온 핵심

- **주기가 길수록 추세 추종에 근거가 있음.** Grayscale(2012~2023): 비트코인 50일 이동평균 추세 신호가 단순 보유보다 위험 대비 성과(샤프 1.9 vs 1.3)가 좋았고, 주로 큰 하락을 피한 덕분. WisdomTree: 1개월+3개월 모멘텀 혼합이 보유보다 샤프·낙폭 개선. 단, 모두 수수료 미반영 백테스트.
  → 1시간봉 대신 4시간봉·일봉 검증, 여러 기간 모멘텀 앙상블(`tsmom_ensemble`) 추가
- **1시간봉 지표 조합은 대부분 운.** 약 1억 개 백테스트 사례: 비용 반영 시 중간값 전략은 손실, 시험 횟수를 감안한 보정 샤프(DSR) 기준을 넘는 건 0.003%뿐.
  → 'DSR(운 아닐 확률)' 계산을 리포트에 추가, 후보 수를 줄임
- **추세 + 단기 눌림목 결합이 횡보장에 강함.** 50일선 위 + RSI(2) 과매도 매수 사례(유료 전략이라 결과는 미검증).
  → `trend_pullback`
- **손절 규칙이 모멘텀 성과를 개선**(암호화폐 147종 연구), **변동성 적응형 추적 손절**(AdaptiveTrend 논문).
  → ATR 추적 손절, 목표 변동성 포지션 크기
- **암호화폐는 상승 편향이 있어 롱/숏 비대칭 배분이 유리했다는 연구**(AdaptiveTrend 70/30).
  → 숏은 모든 기간이 하락에 동의할 때만 (`short_mode: consensus`)
- **펀딩비 극단값 역발상**(BitMEX 리서치): 표본이 작고 수익 대부분이 펀딩비 수취에서 나옴, 회귀분석상 유의하지 않음.
  → `funding_z_max` 필터로만 넣고 기본은 꺼둠
- **ML 가격 방향 예측**: 논문 대부분이 방향 정확도만 보고하고 비용 반영 매매 성과는 없음.
  → 실험용으로만 넣고 같은 잣대(워크포워드+비용+DSR)로 비교

## 3. 정직한 기대치

공개된 기술적 지표 전략으로 꾸준한 초과수익을 내기는 어렵다는 게 조사 전반의 결론이에요.
이 프로그램의 목적은 '돈 되는 전략 찾기'보다 '안 되는 전략에 돈 넣지 않기'에 더 가까워요.

## 출처

- Freqtrade 선물/레버리지: https://www.freqtrade.io/en/stable/leverage/
- Freqtrade lookahead-analysis: https://www.freqtrade.io/en/stable/lookahead-analysis/
- FreqAI: https://www.freqtrade.io/en/stable/freqai/
- Freqtrade vs Jesse vs Hummingbot (2026): https://www.pistack.xyz/posts/2026-04-27-freqtrade-vs-jesse-vs-hummingbot-self-hosted-algorithmic-trading-guide-2026/
- Grayscale, The Trend is Your Friend: https://research.grayscale.com/reports/the-trend-is-your-friend-managing-bitcoins-volatility-with-momentum-signals
- WisdomTree, Enhancing Bitcoin returns using momentum: https://www.wisdomtree.com/dk/insights/blog/enhancing-bitcoin-returns-using-momentum
- Quantpedia, Trend-following and Mean-reversion in Bitcoin: https://quantpedia.com/trend-following-and-mean-reversion-in-bitcoin/
- 1억 개 비트코인 백테스트와 DSR: https://dev.to/rulyfi/we-ran-100-million-bitcoin-backtests-only-3130-beat-the-luck-bar-3ma2
- 추세+눌림목 사례: https://setup4alpha.substack.com/p/this-is-how-an-algorithmic-crypto-realtest
- 손절과 암호화폐 모멘텀: https://sbs.iba.edu.pk/stop-loss-rules-momentum-payoffs-cryptocurrencies.php
- AdaptiveTrend: https://arxiv.org/abs/2602.11708v1
- BitMEX 펀딩비 역발상: https://www.bitmex.com/blog/xbtusd-funding-mean-reversion-strategy
- 무기한 선물 가격결정: https://arxiv.org/abs/2212.06888v2
- TradingAgents: https://www.digitalocean.com/resources/articles/tradingagents-llm-framework
- ML 비트코인 예측 논문 예시: https://www.arxiv.org/pdf/2504.18206
