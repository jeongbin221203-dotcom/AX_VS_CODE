"""등급 판정과 점수 추정.

토익 환산표는 시험마다 달라 공개되지 않는다. 여기서는 정답률(난이도 가중)을 흔히 알려진
환산 구간에 맞춘 구간별 직선으로 바꾼 '추정 점수'를 쓴다. 실제 점수와는 차이가 있다.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass


@dataclass(frozen=True)
class Grade:
    level: int
    name: str
    ko: str
    low: int
    high: int
    color: str

    @property
    def range_text(self) -> str:
        return f"{self.low}~{self.high}점"


GRADES = [
    Grade(1, "Orange", "입문", 10, 215, "#E07B24"),
    Grade(2, "Brown", "기초", 220, 465, "#8A5A3B"),
    Grade(3, "Green", "중급", 470, 725, "#2E8B57"),
    Grade(4, "Blue", "중상급", 730, 855, "#2F6DB5"),
    Grade(5, "Gold", "고급", 860, 990, "#B8912F"),
]
GRADE_BY_LEVEL = {g.level: g for g in GRADES}


def grade_for(score: int | None) -> Grade | None:
    if score is None:
        return None
    for g in reversed(GRADES):
        if score >= g.low:
            return g
    return GRADES[0]


# 정답률(0~1) → 섹션 점수(5~495). 구간 사이는 직선 보간.
_LC_TABLE = [(0.0, 5), (0.2, 60), (0.4, 165), (0.6, 285), (0.75, 375), (0.85, 430), (0.93, 475), (0.97, 495), (1.0, 495)]
_RC_TABLE = [(0.0, 5), (0.2, 45), (0.4, 140), (0.6, 255), (0.75, 345), (0.85, 405), (0.93, 455), (0.98, 495), (1.0, 495)]


def _interp(table: list[tuple[float, int]], ratio: float) -> int:
    ratio = min(max(ratio, 0.0), 1.0)
    xs = [x for x, _ in table]
    i = max(1, min(bisect_right(xs, ratio), len(table) - 1))
    (x0, y0), (x1, y1) = table[i - 1], table[i]
    y = y0 if x1 == x0 else y0 + (y1 - y0) * (ratio - x0) / (x1 - x0)
    return int(round(y / 5.0) * 5)


def section_score(section: str, ratio: float) -> int:
    return _interp(_LC_TABLE if section == "LC" else _RC_TABLE, ratio)


# 모의고사 등급 구성과 같은 난이도 분포를 기준(1.0)으로 난이도 가중 정답률을 보정한다.
# 어려운 문제를 맞히면 더 많이, 쉬운 문제를 틀리면 더 많이 반영된다.
LEVEL_WEIGHT = {1: 0.6, 2: 0.8, 3: 1.0, 4: 1.25, 5: 1.5}


def weighted_ratio(results: list[tuple[int, bool]]) -> float:
    """results: [(level, correct)]. 난이도 가중 정답률."""
    if not results:
        return 0.0
    total = sum(LEVEL_WEIGHT[lv] for lv, _ in results)
    got = sum(LEVEL_WEIGHT[lv] for lv, ok in results if ok)
    return got / total


def estimate(lc: list[tuple[int, bool]], rc: list[tuple[int, bool]]) -> dict:
    """섹션별 (level, correct) 목록으로 추정 점수를 계산한다. 한 섹션이 비면 None."""
    lc_est = section_score("LC", weighted_ratio(lc)) if lc else None
    rc_est = section_score("RC", weighted_ratio(rc)) if rc else None
    total = (lc_est + rc_est) if lc_est is not None and rc_est is not None else None
    return {"lc_est": lc_est, "rc_est": rc_est, "total_est": total}


# ---- 모의고사 구성 -------------------------------------------------------------
# 모의고사 문제의 등급 분포 (실제 시험의 쉬운/어려운 문제 비율을 흉내 냄)
MOCK_LEVEL_MIX = {1: 0.10, 2: 0.20, 3: 0.35, 4: 0.25, 5: 0.10}

MOCK_FORMS = {
    "mini": {"name": "미니 모의고사", "desc": "LC 약 20문항 + RC 약 20문항 · 약 30분",
             "p1": 2, "p2": 6, "p3": 6, "p4": 6, "p5": 10, "p6": 4, "p7_single": 6, "p7_double": 0, "p7_triple": 0,
             "rc_minutes": 18},
    "half": {"name": "하프 모의고사", "desc": "LC 약 50문항 + RC 약 50문항 · 약 65분",
             "p1": 3, "p2": 12, "p3": 18, "p4": 15, "p5": 15, "p6": 8, "p7_single": 14, "p7_double": 1, "p7_triple": 1,
             "rc_minutes": 38},
    # 실제 시험과 같은 구성·진행: LC 는 음성 흐름대로 자동 진행(다시 듣기·되돌아가기 없음), RC 75분
    "full": {"name": "실전 모의고사", "desc": "실제 토익과 같은 200문항 · LC 약 45분 자동 진행 + RC 75분",
             "p1": 6, "p2": 25, "p3": 39, "p4": 30, "p5": 30, "p6": 16, "p7_single": 29, "p7_double": 2, "p7_triple": 3,
             "p3_graphic": 3, "p4_graphic": 2, "rc_minutes": 75, "real": True},
}

# ---- 실전 모의고사: 원점수(맞힌 개수, 100문항 기준) → 환산 점수 ------------------------------
# 흔히 쓰이는 비공식 환산 구간. 시험마다 실제 환산표는 다르므로 ±30점 정도 오차가 있다.
_LC_RAW = [(0, 5), (5, 10), (10, 25), (15, 45), (20, 65), (25, 85), (30, 105), (35, 125), (40, 150), (45, 175),
           (50, 200), (55, 230), (60, 260), (65, 290), (70, 320), (75, 350), (80, 380), (85, 410), (90, 445),
           (95, 475), (97, 495), (100, 495)]
_RC_RAW = [(0, 5), (5, 5), (10, 20), (15, 35), (20, 55), (25, 75), (30, 95), (35, 115), (40, 140), (45, 165),
           (50, 190), (55, 220), (60, 250), (65, 280), (70, 310), (75, 340), (80, 370), (85, 400), (90, 435),
           (95, 465), (98, 495), (100, 495)]


def raw_section_score(section: str, correct: int, total: int) -> int:
    """맞힌 개수를 100문항 기준으로 맞춘 뒤 환산 구간으로 바꾼다."""
    raw = 100 * correct / total if total else 0
    table = [(x / 100, y) for x, y in (_LC_RAW if section == "LC" else _RC_RAW)]
    return _interp(table, raw / 100)


def estimate_raw(lc_correct: int, lc_total: int, rc_correct: int, rc_total: int) -> dict:
    lc = raw_section_score("LC", lc_correct, lc_total) if lc_total else None
    rc = raw_section_score("RC", rc_correct, rc_total) if rc_total else None
    return {"lc_est": lc, "rc_est": rc, "total_est": (lc + rc) if lc is not None and rc is not None else None}


# 진단 테스트: 등급을 고르게 섞은 짧은 세트 (약 20분). 듣기 28문항 — 이보다 적으면 듣기 점수 오차가 ±70점을 넘는다.
DIAGNOSTIC_FORM = {
    1: {1: 1, 2: 1, 3: 1},                    # part: {level: 문항 수}
    2: {1: 2, 2: 2, 3: 2, 4: 2, 5: 2},
    3: {2: 3, 4: 3, 5: 3},
    4: {3: 3, 5: 3},
    5: {1: 2, 2: 3, 3: 3, 4: 3, 5: 2},
    7: {2: 2, 4: 2},
}
