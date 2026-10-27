"""levia_sim.py 규칙 검증 (손으로 계산한 값과 비교). 개발용.

  python tools/test_levia_sim.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "signals"))
from levia_sim import Alert, simulate  # noqa: E402

C = {"leverage": 10, "full_position_margin": 1.0, "first_entry": 0.5, "add_when_better": True,
     "order_max_hours": 24, "max_hold_hours": 72, "consecutive_stop_limit": 2, "withdraw_ratio": 0.5,
     "maker_fee": 0.0002, "taker_fee": 0.0005, "slippage": 0.0, "maintenance_margin": 0.005, "start_equity": 1000}
P = {"stop_seed": 0.05, "tp_ratio": 1.0, "on_opposite": "cancel_pending"}
T0 = pd.Timestamp("2026-10-01 00:00", tz="UTC")  # KST 09:00


def bars(rows, start=T0):
    idx = pd.date_range(start, periods=len(rows), freq="1min")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def al(minute, side, price, cancel):
    return Alert(T0 + pd.Timedelta(minutes=minute), side, price, cancel)


fails = 0


def check(name, cond, info=""):
    global fails
    print(("✅" if cond else "❌") + f" {name} {info}")
    fails += not cond


# 1) 지정가 체결 → 익절. 자본 1000, 1차 50% → 증거금 500 × 10배 = 5000 → 100에서 50개
#    손절 금액 = 5% × 1000 = 50 → 손절가 99, 익절가 101
b = bars([(101, 101.2, 100.8, 101), (100.5, 100.6, 99.9, 100.2), (100.2, 100.8, 100.1, 100.7), (100.7, 101.3, 100.6, 101.1)])
R = simulate(b, [al(0, 1, 100, 102)], P, C)
t = R.trades[0] if R.trades else {}
exp_pnl = 50 * 1 - 5000 * 0.0002 - 50 * 101 * 0.0002
check("지정가 체결 후 익절", t.get("reason") == "take_profit" and abs(t["avg"] - 100) < 1e-9 and abs(t["exit"] - 101) < 1e-9,
      f"{t.get('reason')} avg={t.get('avg')} exit={t.get('exit')}")
check("익절 손익·50% 출금", abs(t.get("pnl", 0) - exp_pnl) < 1e-6 and abs(R.withdrawn - exp_pnl / 2) < 1e-6
      and abs(R.equity - (1000 + exp_pnl / 2)) < 1e-6, f"pnl={t.get('pnl'):.3f} 출금={R.withdrawn:.3f} 자본={R.equity:.3f}")

# 2) 체결 전 마지막 중단선 도달 → 취소
b = bars([(101, 101.5, 100.9, 101.4), (101.4, 102.1, 101.3, 102), (102, 102, 99, 99.5)])
R = simulate(b, [al(0, 1, 100, 102)], P, C)
check("체결 전 마지막 중단선 → 진입 포기", len(R.trades) == 0 and R.n["cancel_line"] == 1, str(R.n))

# 3) 같은 봉에서 기준가·중단선 둘 다 → 보수적으로 취소
b = bars([(101, 101.2, 100.8, 101), (101, 102.2, 99.8, 101)])
R = simulate(b, [al(0, 1, 100, 102)], P, C)
check("같은 봉에서 체결·취소선 둘 다 → 취소", len(R.trades) == 0 and R.n["cancel_line"] == 1)

# 4) 알림 도착 시 이미 마지막 중단선을 넘음 → 즉시 취소
b = bars([(102.5, 102.6, 99, 100)])
R = simulate(b, [al(0, 1, 100, 102)], P, C)
check("도착 시 이미 중단선 넘음 → 진입 안 함", len(R.trades) == 0 and R.n["cancel_line"] == 1)

# 5) 도착 시 가격이 이미 기준가보다 유리 → 시가로 즉시 체결
b = bars([(99.5, 99.6, 98.9, 99), (99, 99.1, 98.4, 98.45)])
R = simulate(b, [al(0, 1, 100, 102)], P, C)
t = R.trades[0] if R.trades else {}
check("이미 유리한 가격 → 즉시 체결(시가)", abs(t.get("avg", 0) - 99.5) < 1e-9, f"avg={t.get('avg')}")
check("손절가 = 평단 - 50/수량", t.get("reason") == "stop" and abs(t["exit"] - (99.5 - 50 / t["qty"])) < 1e-6, f"exit={t.get('exit')}")

# 6) 새 같은 방향 알림 → 미체결 주문 교체
b = bars([(101, 101.2, 100.6, 101), (101, 101.1, 100.4, 100.5), (100.5, 100.6, 100.25, 100.3)])
R = simulate(b, [al(0, 1, 100, 103), al(1, 1, 100.4, 103)], P, C)
check("새 알림이 미체결 주문을 교체", R.n["replaced"] == 1 and R.n["fills"] == 1, str(R.n))

# 7) 2차 진입: 100에서 50개, 새 L 알림 99 체결 → 평단 99.5 근처, 손절가는 평단 - 50/총수량
b = bars([(100.5, 100.6, 99.9, 100.2), (100.2, 100.3, 99.6, 99.7), (99.7, 99.8, 98.95, 99.1), (99.1, 99.2, 98.0, 98.1)])
R = simulate(b, [al(0, 1, 100, 102), al(1, 1, 99, 101)], P, C)
t = R.trades[0] if R.trades else {}
q1, q2 = 5000 / 100, 5000 / 99
avg = (100 * q1 + 99 * q2) / (q1 + q2)
check("2차 진입(평단보다 유리한 새 기준가)", t.get("adds") == 1 and abs(t.get("avg", 0) - avg) < 1e-6, f"avg={t.get('avg')} 기대={avg:.4f}")
check("2차 진입 후 손절가 재계산", t.get("reason") == "stop" and abs(t["exit"] - (avg - 50 / (q1 + q2))) < 1e-6)

# 8) 2연속 손절 → 그날(KST) 남은 알림 무시
rows = [(100.5, 100.6, 99.9, 100), (100, 100, 98.5, 98.6),       # 1차 손절
        (98.6, 98.7, 98.0, 98.1), (98.1, 98.2, 96.5, 96.6),       # 2차 손절
        (96.6, 96.7, 95.0, 95.1), (95.1, 99, 95, 98)]
b = bars(rows)
R = simulate(b, [al(0, 1, 100, 103), al(2, 1, 98.1, 101), al(4, 1, 95.5, 99)], P, C)
check("2연속 손절 → 당일 매매 종료", sum(t["reason"] == "stop" for t in R.trades) == 2 and R.n["halted_days"] == 1
      and R.n["skipped_halt"] == 1, str(R.n))

# 9) 다음 날(KST)에는 다시 매매
b2 = pd.concat([b, bars([(98, 98.1, 97.5, 97.6), (97.6, 97.7, 96.9, 97), (97, 98.6, 96.95, 98.5)], start=T0 + pd.Timedelta(hours=15, minutes=1))])
R = simulate(b2, [al(0, 1, 100, 103), al(2, 1, 98.1, 101), al(15 * 60 + 1, 1, 97, 99)], P, C)
check("다음 날 KST 0시 이후 다시 진입", len(R.trades) == 3, f"거래 {len(R.trades)}")

# 10) 반대 알림: 'close'면 청산 후 반대 방향 주문
b = bars([(100.5, 100.6, 99.9, 100.2), (100.2, 100.4, 100.1, 100.3), (100.3, 100.9, 100.2, 100.8)])
R = simulate(b, [al(0, 1, 100, 102), al(1, -1, 100.8, 99)], {**P, "on_opposite": "close"}, C)
check("반대 알림 'close' → 청산", any(t["reason"] == "opposite" for t in R.trades))
R2 = simulate(b, [al(0, 1, 100, 102), al(1, -1, 100.8, 99)], {**P, "on_opposite": "ignore"}, C)
check("반대 알림 'ignore' → 포지션 유지", not any(t["reason"] == "opposite" for t in R2.trades))

# 11) 숏: 기준가 위에서 체결, 아래 익절
b = bars([(99.5, 99.8, 99.4, 99.7), (99.7, 100.1, 99.6, 99.9), (99.9, 100.0, 98.9, 99.0)])
R = simulate(b, [al(0, -1, 100, 97)], P, C)
t = R.trades[0] if R.trades else {}
check("숏 체결 → 익절", t.get("side") == "short" and t.get("reason") == "take_profit" and abs(t["exit"] - 99) < 1e-9, f"{t}")

print("\n전부 통과" if not fails else f"\n실패 {fails}건")
sys.exit(1 if fails else 0)
