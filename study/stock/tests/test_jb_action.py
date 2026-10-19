"""JB차트 종합 신호 — 오늘 할 일(core/jb_rules.action) 다섯 가지."""
import numpy as np

from core import jb_rules

D = [f"2026-01-{i:02d}" for i in range(1, 11)]
O = np.full(10, 100.0)
C = np.full(10, 100.0)


def test_buy_when_signal_today():
    s = np.full(10, 0.5); s[-1] = 0.97
    a = jb_rules.action(D, O, C, [], 9, {}, s, "중형주")
    assert a["kind"] == "buy" and "중형주" in a["text"] and "다음 거래일" in a["text"]


def test_sell_today_and_hold_and_wait():
    s = np.full(10, 0.5)
    tr = [(2, 3, 9, 110.0, 0.097, "AI 점수(5봉 평균) 20점 — 30점 아래")]
    a = jb_rules.action(D, O, C, tr, None, {}, s, "소형주")
    assert a["kind"] == "sell" and "+9.7%" in a["text"]
    tr = [(2, 3, None, None, None, "")]
    c = C.copy(); c[-1] = 105
    a = jb_rules.action(D, O, c, tr, None, {"adds": {}, "add_pending": []}, s, "대형주")
    assert a["kind"] == "hold" and "+5.0%" in a["text"]
    a = jb_rules.action(D, O, c, tr, None, {"adds": {}, "add_pending": [3]}, s, "대형주")
    assert a["kind"] == "add"
    a = jb_rules.action(D, O, C, [], None, {}, s, None)
    assert a["kind"] == "wait" and "AI 50점" in a["text"]
