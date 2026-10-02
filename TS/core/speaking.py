"""말하기 시험(토익스피킹·오픽) — 과제 정의, 문제 은행, 말하기 단계(steps) 만들기, 기록, 점수·등급 추정.

화면은 하나의 엔진(static/js/speaking.js)이 '단계' 목록을 차례로 돌린다.
단계 = 화면에 보일 것 + 들려줄 문장 + 준비 시간 + 답변 시간 + 채점 기준 + 모범 답안.
연습은 문제 하나(단계 1~3개)가 끝날 때마다, 모의고사는 전부 끝난 뒤 한꺼번에 스스로 채점한다.

점수 환산은 주관사 공식 방식이 아니다(공개되지 않음). 스스로 채점한 결과로 대략의 위치를 보여 주는 추정치다.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from . import db
from .content import content_files

# ---- 토익스피킹 ---------------------------------------------------------------------
# 2022년 6월 개편 형식: 11문항 약 20분. 문항 점수 Q1~10 0~3, Q11 0~5 (합 35) → 0~200 환산(추정).
TSP_TASKS = {
    "read_aloud": {"q": "Q1~2", "name": "문장 읽기", "en": "Read a text aloud", "n": 2, "prep": 45, "speak": [45], "max": 3,
                   "desc": "화면의 안내문·광고를 45초 준비 후 45초 동안 소리 내어 읽는다. 발음·억양·강세를 본다."},
    "describe_picture": {"q": "Q3~4", "name": "사진 묘사", "en": "Describe a picture", "n": 2, "prep": 45, "speak": [30], "max": 3,
                         "desc": "사진을 45초 보고 30초 동안 묘사한다. (이 앱은 사진 대신 장면 설명을 보여 줍니다)"},
    "respond_questions": {"q": "Q5~7", "name": "질문에 답하기", "en": "Respond to questions", "n": 1, "prep": 3, "speak": [15, 15, 30], "max": 3,
                          "desc": "전화 설문 같은 상황에서 일상 질문 3개에 답한다 (준비 3초 · 15/15/30초)."},
    "respond_info": {"q": "Q8~10", "name": "표 보고 답하기", "en": "Respond using information provided", "n": 1, "prep": 3,
                     "speak": [15, 15, 30], "max": 3,
                     "desc": "일정표·예약표를 45초 읽은 뒤, 전화로 묻는 질문 3개에 표를 보고 답한다 (10번은 두 번 들려줌)."},
    "opinion": {"q": "Q11", "name": "의견 말하기", "en": "Express an opinion", "n": 1, "prep": 45, "speak": [60], "max": 5,
                "desc": "찬반·선택 질문에 45초 준비 후 60초 동안 이유와 예시를 들어 의견을 말한다."},
}
TSP_ORDER = list(TSP_TASKS)
TSP_RAW_MAX = sum(t["max"] * t["n"] * len(t["speak"]) for t in TSP_TASKS.values())     # 35

# (레벨, 이름, 최저 점수) — 점수는 10점 단위
TSP_LEVELS = [
    (8, "Advanced High", 200), (7, "Advanced Mid", 180), (6, "Advanced Low", 160), (5, "Intermediate High", 140),
    (4, "Intermediate Mid 3", 130), (3, "Intermediate Mid 2", 120), (2, "Intermediate Mid 1", 110),
    (1, "Intermediate Low", 90), (0, "Novice High", 60), (-1, "Novice Mid / Low", 0),
]
TSP_TARGETS = [110, 120, 130, 140, 150, 160, 170, 180]

# 문항 유형별 자기 채점 기준 (점수, 설명) — ETS 공개 채점 기준을 학습용으로 풀어 쓴 것
TSP_RUBRIC = {
    "read_aloud": [(3, "발음·억양·강세가 자연스럽고 막힘이 거의 없다"),
                   (2, "대체로 알아듣기 쉽지만 발음·끊어 읽기 실수가 몇 군데 있다"),
                   (1, "자주 막히거나 틀린 발음이 많아 알아듣기 어려운 곳이 많다"),
                   (0, "읽지 못했거나 거의 알아들을 수 없다")],
    "describe_picture": [(3, "주요 대상과 배경을 알맞은 어휘와 문법으로 묘사했다 (사람·동작·위치)"),
                         (2, "묘사는 했지만 어휘·문법 실수가 있거나 내용이 적다"),
                         (1, "한두 가지만 말했거나 실수가 많아 이해가 어렵다"),
                         (0, "답하지 못함 / 사진과 무관")],
    "respond_questions": [(3, "질문에 맞게 완전한 문장으로 답하고 (30초 문항은) 이유까지 덧붙였다"),
                          (2, "질문에 답했지만 문장이 어색하거나 내용이 부족하다"),
                          (1, "일부만 답했거나 실수가 많아 뜻이 흐리다"),
                          (0, "답하지 못함 / 질문과 무관")],
    "respond_info": [(3, "표의 정보를 정확히 찾아 자연스러운 문장으로 전달했다"),
                     (2, "정보는 대체로 맞지만 일부 빠지거나 문장이 어색하다"),
                     (1, "정보가 틀렸거나 단어만 나열했다"),
                     (0, "답하지 못함 / 표와 무관")],
    "opinion": [(5, "의견이 분명하고 이유·예시가 논리적으로 이어지며 실수가 거의 없다"),
                (4, "의견과 이유가 잘 전달되지만 작은 실수나 머뭇거림이 있다"),
                (3, "의견은 있으나 이유·예시가 부족하거나 실수가 눈에 띈다"),
                (2, "생각을 일부만 전달했고 문장이 단순·부정확하다"),
                (1, "짧은 말 몇 마디로 끝났다"),
                (0, "답하지 못함 / 질문과 무관")],
}

TSP_DIRECTIONS = {
    "read_aloud": "In this part of the test, you will read aloud the text on the screen. You will have 45 seconds to prepare. "
                  "Then you will have 45 seconds to read the text aloud.",
    "describe_picture": "In this part of the test, you will describe the picture on your screen in as much detail as you can. "
                        "You will have 45 seconds to prepare your response. Then you will have 30 seconds to speak about the picture.",
    "respond_questions": "In this part of the test, you will answer three questions. You will have three seconds to prepare after you "
                         "hear each question. You will have 15 seconds to respond to Questions 5 and 6, and 30 seconds to respond to Question 7.",
    "respond_info": "In this part of the test, you will answer three questions based on the information provided. You will have 45 seconds "
                    "to read the information before the questions begin. You will have three seconds to prepare and 15 seconds to respond "
                    "to Questions 8 and 9. You will hear Question 10 two times. You will have three seconds to prepare and 30 seconds to respond.",
    "opinion": "In this part of the test, you will give your opinion about a specific topic. Be sure to say as much as you can in the time "
               "allowed. You will have 45 seconds to prepare. Then you will have 60 seconds to speak.",
}


def tsp_score(raw: float) -> int:
    """문항 점수 합(0~35) → 0~200 (10점 단위, 추정)."""
    return int(round(max(0.0, min(raw, TSP_RAW_MAX)) / TSP_RAW_MAX * 20)) * 10


def tsp_level(score: int | None) -> tuple[int, str] | None:
    if score is None:
        return None
    for lv, name, low in TSP_LEVELS:
        if score >= low:
            return lv, name
    return TSP_LEVELS[-1][:2]


# ---- 오픽 -----------------------------------------------------------------------------
# 주제 (key, 이름, 묶음). survey=True 는 Background Survey 에서 고르는 주제, False 는 돌발 주제.
OPIC_TOPICS = {
    # 거주 (설문 3부 — 누구나 받는 주제)
    "home": ("사는 곳·집", "거주", True),
    # 여가
    "movie": ("영화 보기", "여가", True), "concert": ("공연·콘서트 보기", "여가", True),
    "park": ("공원 가기", "여가", True), "beach": ("해변 가기", "여가", True),
    "cafe": ("카페 가기", "여가", True), "shopping": ("쇼핑하기", "여가", True),
    "tv": ("TV·리얼리티 쇼 보기", "여가", True), "games": ("게임하기", "여가", True),
    # 취미
    "music": ("음악 감상", "취미", True), "cooking": ("요리하기", "취미", True), "pets": ("반려동물 기르기", "취미", True),
    # 운동
    "jogging": ("조깅", "운동", True), "walking": ("걷기", "운동", True),
    "gym": ("헬스", "운동", True), "bike": ("자전거 타기", "운동", True),
    "swimming": ("수영", "운동", True), "hiking": ("하이킹·등산", "운동", True),
    # 휴가·출장
    "travel_dom": ("국내 여행", "휴가", True), "travel_abroad": ("해외 여행", "휴가", True),
    "staycation": ("집에서 보내는 휴가", "휴가", True),
    # 돌발 주제
    "recycling": ("재활용", "돌발", False), "bank": ("은행", "돌발", False), "hotel": ("호텔", "돌발", False),
    "phone": ("휴대폰", "돌발", False), "tech": ("인터넷·기술", "돌발", False), "transport": ("교통", "돌발", False),
    "furniture": ("가구·가전", "돌발", False), "weather": ("날씨·계절", "돌발", False),
    "holiday": ("명절·기념일", "돌발", False), "health": ("건강", "돌발", False),
    "food": ("음식·외식", "돌발", False), "friends": ("가족·친구", "돌발", False),
    "appointment": ("약속", "돌발", False), "fashion": ("패션", "돌발", False),
    "housework": ("집안일", "돌발", False), "neighborhood": ("동네·이웃", "돌발", False),
    "library": ("도서관", "돌발", False), "geography": ("지형·나라", "돌발", False), "industry": ("산업·회사", "돌발", False),
    # 자기소개
    "intro": ("자기소개", "자기소개", False),
}
SURVEY_GROUPS = {"여가": 2, "취미": 1, "운동": 1, "휴가": 1}       # 묶음별 최소 선택 수 (실제 설문은 합계 12개)

OPIC_KINDS = {
    "intro": "자기소개", "describe": "묘사", "routine": "습관·루틴", "past": "경험", "compare": "비교·변화", "issue": "사회 이슈",
    "ask": "롤플레이: 질문하기", "solve": "롤플레이: 문제 해결", "exp": "롤플레이: 관련 경험",
}

# 등급 (점수 1~5 자기 채점 → 등급). IM 은 답변 길이로 IM1~3 을 나눈다(추정).
OPIC_GRADES = ["NL", "NM", "NH", "IL", "IM1", "IM2", "IM3", "IH", "AL"]
OPIC_GRADE_NAME = {"AL": "Advanced Low", "IH": "Intermediate High", "IM3": "Intermediate Mid 3",
                   "IM2": "Intermediate Mid 2", "IM1": "Intermediate Mid 1", "IL": "Intermediate Low",
                   "NH": "Novice High", "NM": "Novice Mid", "NL": "Novice Low"}
OPIC_RUBRIC = [
    (5, "AL 수준: 일어난 일을 시간 순서대로 이야기하고, 문단으로 길게 말하며 시제·연결어가 정확하다"),
    (4, "IH 수준: 문단으로 말하고 현재·과거·미래를 오가며 설명·비교한다. 가끔 실수가 있다"),
    (3, "IM 수준: 문장을 여러 개 이어 질문에 답한다. 기본 시제는 대체로 맞고 내용이 이어진다"),
    (2, "IL 수준: 짧고 단순한 문장 몇 개, 자주 멈추고 같은 말을 반복한다"),
    (1, "NH 이하: 단어·외운 표현 위주, 문장을 거의 만들지 못했다"),
]
OPIC_LEVELS = {1: "1 · 아주 쉬움", 2: "2 · 쉬움", 3: "3 · 보통", 4: "4 · 보통+", 5: "5 · 어려움", 6: "6 · 아주 어려움"}
OPIC_TARGETS = ["IM1", "IM2", "IM3", "IH", "AL"]
OPIC_MINUTES = 40


def opic_grade(avg_points: float | None, avg_words: float | None) -> str | None:
    """자기 채점 평균(1~5)과 답변 평균 단어 수 → 등급 (추정)."""
    if avg_points is None:
        return None
    w = avg_words or 0
    if avg_points >= 4.5:
        return "AL"
    if avg_points >= 3.6:
        return "IH"
    if avg_points >= 2.6:
        return "IM3" if w >= 110 else "IM2" if w >= 70 else "IM1"
    if avg_points >= 1.8:
        return "IL"
    return "NH" if avg_points >= 1.3 else "NM"


# ---- 문제 은행 -----------------------------------------------------------------------

class SpeakingBank:
    """content/speaking/toeic/*.json, content/speaking/opic/*.json"""

    def __init__(self, content_dir: Path):
        self.dir = Path(content_dir)
        self._sig = None
        self.tsp: dict[str, list[dict]] = {}
        self.opic_q: list[dict] = []
        self.opic_rp: list[dict] = []
        self.by_id: dict[tuple[str, str], dict] = {}
        self.reload()

    def _signature(self):
        return tuple((p.as_posix(), p.stat().st_mtime_ns) for p in sorted(self.dir.glob("*/*.json")))

    @staticmethod
    def _load(folder: Path, stem: str) -> list[dict]:
        out = []
        for path in content_files(folder, stem):
            try:
                out += json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue                          # 작성 중인 파일은 건너뜀
        return out

    def reload(self) -> None:
        self._sig = self._signature()
        tsp = {t: self._load(self.dir / "toeic", t) for t in TSP_TASKS}
        opic_q = [q for q in self._load(self.dir / "opic", "questions") if q.get("topic") in OPIC_TOPICS]
        opic_rp = self._load(self.dir / "opic", "roleplay")
        by_id = {}
        for t, items in tsp.items():
            for it in items:
                by_id[(t, it["id"])] = it
        for q in opic_q:
            by_id[("opic_q", q["id"])] = q
        for r in opic_rp:
            by_id[("opic_rp", r["id"])] = r
        self.tsp, self.opic_q, self.opic_rp, self.by_id = tsp, opic_q, opic_rp, by_id

    def refresh_if_changed(self) -> None:
        if self._signature() != self._sig:
            self.reload()

    def topic_counts(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for q in self.opic_q:
            out.setdefault(q["topic"], {}).setdefault(q["kind"], 0)
            out[q["topic"]][q["kind"]] += 1
        for r in self.opic_rp:
            out.setdefault(r["topic"], {}).setdefault("roleplay", 0)
            out[r["topic"]]["roleplay"] += 1
        return out


def _fresh_first(pool: list[dict], task: str, rng: random.Random) -> list[dict]:
    """안 푼 문제 → 오래전에 푼 문제 순 (같은 조건이면 무작위)."""
    pool = list(pool)
    rng.shuffle(pool)
    seen = last_seen(task)
    pool.sort(key=lambda it: seen.get(it["id"], ""))
    return pool


# ---- 단계(steps) 만들기 ---------------------------------------------------------------
# 단계 공통 키:
#   ref {task, item_id, qidx} · label · title · directions · show {kind, ...} · intro(말할 소개, 첫 단계만)
#   say [{text, gender}] 들려줄 질문 · repeat 질문을 몇 번 들려줄지 · prep 준비 초 · speak 답변 초(0 = 제한 없음, 최대 180초)
#   replay 다시 듣기 허용 횟수 · rubric 채점 기준 키 · max 만점 · samples [{label, text, ko}] · tips · target(읽기 정답 문장)

def _samples(it: dict, i: int | None = None) -> list[dict]:
    """모범 답안 두 단계. i 가 있으면 samples[i] 처럼 질문별 배열에서 꺼낸다."""
    def get(key):
        if i is None:
            return it.get(key)
        arr = it.get("samples" + key[len("sample"):]) or []        # sample_ko → samples_ko
        return arr[i] if i < len(arr) else None
    pairs = [("기본 답변 (IM 목표)", get("sample"), get("sample_ko")),
             ("고득점 답변 (IH~AL 목표)", get("sample_adv"), get("sample_adv_ko"))]
    return [{"label": a, "text": b, "ko": c or ""} for a, b, c in pairs if b]


def tsp_steps(task: str, it: dict, qno: int | None = None) -> list[dict]:
    """토익스피킹 문제 하나 → 단계 목록. qno = 모의고사 문항 번호(1~11)."""
    info = TSP_TASKS[task]
    ref = {"task": task, "item_id": it["id"]}
    base = {"title": info["en"], "directions": TSP_DIRECTIONS[task], "rubric": task, "max": info["max"],
            "tips": it.get("tips", [])}
    lab = lambda k: f"Question {qno + k} of 11" if qno else (f"질문 {k + 1} / 3" if len(info["speak"]) > 1 else info["name"])
    if task == "read_aloud":
        return [{**base, "ref": {**ref, "qidx": 0}, "label": lab(0), "show": {"kind": "text", "text": it["text"]},
                 "say": [], "prep": 45, "speak": 45, "target": it["text"],
                 "samples": [{"label": "끊어 읽기·강세 표시 ( / 쉼, *강세* )", "text": it.get("marked", ""), "ko": it.get("translation", "")}],
                 "notes": it.get("tricky", [])}]
    if task == "describe_picture":
        return [{**base, "ref": {**ref, "qidx": 0}, "label": lab(0),
                 "show": {"kind": "scene", "setting": it.get("setting", ""), "scene_ko": it["scene_ko"],
                          "elements": [{"where": e["where"], "ko": e["ko"]} for e in it["elements"]]},
                 "say": [], "prep": 45, "speak": 30, "samples": _samples(it),
                 "notes": [f"{e['where']} · {e['en']}" for e in it["elements"]]}]
    if task == "respond_questions":
        out = []
        for i, q in enumerate(it["questions"]):
            out.append({**base, "ref": {**ref, "qidx": i}, "label": lab(i),
                        "show": {"kind": "question", "intro": it["intro"], "question": q},
                        "intro": [{"text": it["intro"], "gender": "female"}] if i == 0 else [],
                        "say": [{"text": q, "gender": "female"}], "prep": 3, "speak": info["speak"][i],
                        "samples": _samples(it, i), "question_ko": (it.get("questions_ko") or [""] * 3)[i]})
        return out
    if task == "respond_info":
        out = []
        for i, q in enumerate(it["questions"]):
            out.append({**base, "ref": {**ref, "qidx": i}, "label": lab(i),
                        "show": {"kind": "table", "info": it["info"]},
                        "read_first": 45 if i == 0 else 0,
                        "intro": [{"text": it["intro"], "gender": it.get("voice", "male")}] if i == 0 else [],
                        "say": [{"text": q, "gender": it.get("voice", "male")}], "repeat": 2 if i == 2 else 1,
                        "prep": 3, "speak": info["speak"][i], "hide_question": True,
                        "samples": _samples(it, i), "question_ko": (it.get("questions_ko") or [""] * 3)[i],
                        "question_text": q})
        return out
    if task == "opinion":
        return [{**base, "ref": {**ref, "qidx": 0}, "label": lab(0),
                 "show": {"kind": "question", "question": it["question"]},
                 "say": [{"text": it["question"], "gender": "female"}], "prep": 45, "speak": 60,
                 "samples": _samples(it), "question_ko": it.get("question_ko", ""),
                 "notes": it.get("outline", [])}]
    raise KeyError(task)


def tsp_practice(bank: SpeakingBank, task: str, n: int, rng: random.Random | None = None, weak: bool = False) -> list[dict]:
    """연습: 문제 n개 → [{task, item_id, steps}]. weak=True 면 마지막 점수가 낮았던 문제만 (낮은 것부터)."""
    rng = rng or random.Random()
    pool = bank.tsp.get(task, [])
    if weak:
        ids = weak_items("tsp", f"tsp:{task}")
        pool = sorted((it for it in pool if it["id"] in ids), key=lambda it: ids[it["id"]])
        return [{"task": task, "item_id": it["id"], "steps": tsp_steps(task, it)} for it in pool[:n]]
    items = _fresh_first(pool, f"tsp:{task}", rng)[:n]
    return [{"task": task, "item_id": it["id"], "steps": tsp_steps(task, it)} for it in items]


def tsp_mock_plan(bank: SpeakingBank, rng: random.Random | None = None) -> list[dict]:
    """모의고사 11문항: 읽기 2 · 사진 2 · 질문 1세트 · 표 1세트 · 의견 1."""
    rng = rng or random.Random()
    units, qno = [], 1
    for task in TSP_ORDER:
        info = TSP_TASKS[task]
        for it in _fresh_first(bank.tsp.get(task, []), f"tsp:{task}", rng)[:info["n"]]:
            steps = tsp_steps(task, it, qno)
            qno += len(steps)
            units.append({"task": task, "item_id": it["id"], "steps": steps})
    return units


def opic_q_step(q: dict, label: str, show_text: bool) -> dict:
    return {"ref": {"task": "opic_q", "item_id": q["id"], "qidx": 0}, "label": label,
            "title": f"{OPIC_TOPICS[q['topic']][0]} · {OPIC_KINDS[q['kind']]}",
            "show": {"kind": "opic", "question": q["question"] if show_text else ""},
            "say": [{"text": q["question"], "gender": "female"}], "prep": 0, "speak": 0, "replay": 1,
            "rubric": "opic", "max": 5, "samples": _samples(q), "question_text": q["question"],
            "question_ko": q.get("question_ko", ""), "tips": q.get("tips", [])}


def opic_rp_steps(r: dict, start_no: int | None, show_text: bool) -> list[dict]:
    out = []
    for i, s in enumerate(r["steps"]):
        label = f"Question {start_no + i}" if start_no else f"롤플레이 {i + 1} / {len(r['steps'])}"
        out.append({"ref": {"task": "opic_rp", "item_id": r["id"], "qidx": i}, "label": label,
                    "title": f"{OPIC_TOPICS[r['topic']][0]} · {OPIC_KINDS[s['kind']]}",
                    "show": {"kind": "opic", "question": s["question"] if show_text else "",
                             "situation_ko": r.get("situation_ko", "") if i == 0 else ""},
                    "say": [{"text": s["question"], "gender": "female"}], "prep": 0, "speak": 0, "replay": 1,
                    "rubric": "opic", "max": 5, "samples": _samples(s), "question_text": s["question"],
                    "question_ko": s.get("question_ko", ""), "tips": s.get("tips", [])})
    return out


def _combo(bank: SpeakingBank, topic: str, level: int, rng: random.Random, used: set) -> list[dict]:
    """한 주제 3문항 콤보: 묘사 → 습관/비교 → 경험 (난이도가 높으면 비교·이슈가 섞인다)."""
    # 경험(level 3)은 어느 난이도에서나 나온다. 비교·이슈(level 5)는 난이도 5 이상에서만 콤보에 섞인다.
    qs = [q for q in bank.opic_q if q["topic"] == topic and q["id"] not in used and q.get("level", 1) <= max(level, 3)]
    seen = last_seen("opic_q")

    def take(kinds):
        pool = [q for q in qs if q["kind"] in kinds and q["id"] not in used]
        rng.shuffle(pool)
        pool.sort(key=lambda q: seen.get(q["id"], ""))
        if pool:
            used.add(pool[0]["id"])
            return pool[0]
        return None

    second = ["compare", "routine"] if level >= 5 and rng.random() < 0.5 else ["routine", "compare"]
    picks = [take(["describe"]), take(second) or take(["describe", "routine"]), take(["past"]) or take(["routine", "describe"])]
    return [p for p in picks if p]


def opic_mock_plan(bank: SpeakingBank, survey: list[str], level: int, rng: random.Random | None = None) -> list[dict]:
    """실제 시험처럼 15문항: 자기소개 1 · 설문 콤보 2(3+3) · 돌발 콤보 1(3) · 롤플레이 1(3) · 마무리 2(비교·이슈).
    난이도 1~2 는 마무리 2문항 없이 13문항."""
    rng = rng or random.Random()
    used: set = set()
    units: list[dict] = []

    def add(qs: list[dict]):
        for q in qs:
            no = sum(len(u["steps"]) for u in units) + 1
            units.append({"task": "opic_q", "item_id": q["id"], "steps": [opic_q_step(q, f"Question {no}", False)]})

    intro = [q for q in bank.opic_q if q["kind"] == "intro"]
    if intro:
        add([rng.choice(intro)])
    has_q = {q["topic"] for q in bank.opic_q}                 # 문항이 있는 주제만 (없는 주제를 뽑으면 시험이 짧아짐)
    topics = ([t for t in survey if t in OPIC_TOPICS and OPIC_TOPICS[t][2] and t in has_q]
              or [t for t, v in OPIC_TOPICS.items() if v[2] and t in has_q])
    if "home" in topics and rng.random() < 0.6:
        first = ["home"] + rng.sample([t for t in topics if t != "home"] or ["home"], 1)
    else:
        first = rng.sample(topics, min(2, len(topics)))
    for t in first:
        add(_combo(bank, t, level, rng, used))
    unexpected = [t for t, v in OPIC_TOPICS.items() if not v[2] and t != "intro"]
    rng.shuffle(unexpected)
    for t in unexpected:
        combo = _combo(bank, t, level, rng, used)
        if len(combo) >= 2:
            add(combo)
            break
    rps = [r for r in bank.opic_rp if r.get("level", 3) <= max(level, 3)] or bank.opic_rp
    if rps:
        seen = last_seen("opic_rp")
        rps = list(rps)
        rng.shuffle(rps)
        rps.sort(key=lambda r: seen.get(r["id"], ""))
        r = rps[0]
        no = sum(len(u["steps"]) for u in units) + 1
        units.append({"task": "opic_rp", "item_id": r["id"], "steps": opic_rp_steps(r, no, False)})
    # 마무리 2문항: 설문 주제의 비교·사회 이슈 (난이도 3 이상)
    if level >= 3:
        pool = [q for q in bank.opic_q if q["topic"] in topics and q["kind"] in ("compare", "issue") and q["id"] not in used]
        rng.shuffle(pool)
        add(pool[:2])
    return units


def opic_practice(bank: SpeakingBank, topic: str | None, kind: str | None, n: int, show_text: bool,
                  rng: random.Random | None = None, weak: bool = False) -> list[dict]:
    rng = rng or random.Random()
    if weak:                                     # 마지막 점수가 낮았던 문항 (점수 낮은 것부터)
        ids = weak_items("opic", "opic_q")
        pool = sorted((q for q in bank.opic_q if q["id"] in ids and (not topic or q["topic"] == topic)),
                      key=lambda q: ids[q["id"]])[:n]
        return [{"task": "opic_q", "item_id": q["id"], "steps": [opic_q_step(q, f"질문 {i + 1} / {len(pool)}", show_text)]}
                for i, q in enumerate(pool)]
    if kind == "roleplay":
        pool = [r for r in bank.opic_rp if not topic or r["topic"] == topic]
        pool = _fresh_first(pool, "opic_rp", rng)[:max(1, min(n, 3))]
        return [{"task": "opic_rp", "item_id": r["id"], "steps": opic_rp_steps(r, None, show_text)} for r in pool]
    if topic and not kind:                       # 주제 하나 → 콤보처럼 묘사·습관·경험 순서
        pool = [q for q in bank.opic_q if q["topic"] == topic]
        order = {k: i for i, k in enumerate(["intro", "describe", "routine", "past", "compare", "issue"])}
        pool = _fresh_first(pool, "opic_q", rng)[:n]
        pool.sort(key=lambda q: order.get(q["kind"], 9))
    else:
        pool = [q for q in bank.opic_q if (not topic or q["topic"] == topic) and (not kind or q["kind"] == kind)]
        pool = _fresh_first(pool, "opic_q", rng)[:n]
    return [{"task": "opic_q", "item_id": q["id"], "steps": [opic_q_step(q, f"질문 {i + 1} / {len(pool)}", show_text)]}
            for i, q in enumerate(pool)]


# ---- 기록 -----------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS speaking_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    exam        TEXT NOT NULL,             -- tsp | opic
    task        TEXT NOT NULL,             -- tsp:read_aloud … | opic_q | opic_rp
    item_id     TEXT NOT NULL,
    qidx        INTEGER NOT NULL DEFAULT 0,
    points      REAL NOT NULL,             -- 자기 채점 점수 (토익스피킹 0~3/0~5, 오픽 1~5)
    max_points  REAL NOT NULL,
    words       INTEGER,                   -- 음성 인식으로 센 단어 수
    seconds     REAL,                      -- 실제로 말한 시간
    accuracy    REAL,                      -- 문장 읽기: 원문과 일치한 비율
    mock_id     INTEGER,
    response    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_spk_task ON speaking_attempts(exam, task, created_at);

CREATE TABLE IF NOT EXISTS speaking_mocks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    exam        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    finished_at TEXT,
    plan        TEXT NOT NULL,
    settings    TEXT NOT NULL DEFAULT '{}',
    result      TEXT,
    score       TEXT                       -- 토익스피킹 '150', 오픽 'IH'
);
"""


def ensure_schema() -> None:
    with db.connect() as con:
        con.executescript(SCHEMA)


def task_key(exam: str, task: str) -> str:
    return f"tsp:{task}" if exam == "tsp" else task


def _valid(bank: SpeakingBank, exam: str, task: str, item_id: str) -> dict | None:
    if exam == "tsp" and task in TSP_TASKS:
        return bank.by_id.get((task, item_id))
    if exam == "opic" and task in ("opic_q", "opic_rp"):
        return bank.by_id.get((task, item_id))
    return None


def _num(v, lo=None, hi=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:                                   # NaN
        return None
    if lo is not None:
        x = max(lo, x)
    if hi is not None:
        x = min(hi, x)
    return x


def record(bank: SpeakingBank, exam: str, rows: list, mock_id: int | None = None) -> list[dict]:
    """rows = [{task, item_id, qidx, points, words, seconds, accuracy, response}] → 저장한 행(검증된 것만)."""
    saved = []
    ts = db.now()
    with db.connect() as con:
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict):
                continue
            task, iid = str(r.get("task", "")), str(r.get("item_id", ""))
            if not _valid(bank, exam, task, iid):
                continue
            mx = TSP_TASKS[task]["max"] if exam == "tsp" else 5
            pts = _num(r.get("points"), 0 if exam == "tsp" else 1, mx)
            if pts is None:
                continue
            row = {"task": task, "item_id": iid, "qidx": int(_num(r.get("qidx"), 0, 20) or 0), "points": pts, "max": mx,
                   "words": None if _num(r.get("words")) is None else int(_num(r.get("words"), 0, 2000)),
                   "seconds": _num(r.get("seconds"), 0, 600), "accuracy": _num(r.get("accuracy"), 0, 1),
                   "response": str(r.get("response", ""))[:4000]}
            con.execute("INSERT INTO speaking_attempts(created_at, exam, task, item_id, qidx, points, max_points, words, "
                        "seconds, accuracy, mock_id, response) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (ts, exam, task_key(exam, task), iid, row["qidx"], pts, mx, row["words"], row["seconds"],
                         row["accuracy"], mock_id, row["response"]))
            saved.append(row)
    return saved


def weak_items(exam: str, task: str) -> dict[str, float]:
    """마지막으로 답한 회차의 점수 비율(세트는 평균)이 낮은 문제 → {item_id: 비율}.
    토익스피킹은 만점의 60% 미만, 오픽은 3점(IM) 이하."""
    with db.connect() as con:
        rows = con.execute(
            "SELECT a.item_id, AVG(a.points / a.max_points) r FROM speaking_attempts a "
            "JOIN (SELECT item_id, MAX(created_at) at FROM speaking_attempts WHERE exam = ? AND task = ? GROUP BY item_id) m "
            "ON a.item_id = m.item_id AND a.created_at = m.at WHERE a.exam = ? AND a.task = ? GROUP BY a.item_id",
            (exam, task, exam, task)).fetchall()
    cut = (lambda r: r <= 0.6) if exam == "opic" else (lambda r: r < 0.6)
    return {r[0]: r[1] for r in rows if cut(r[1])}


def last_seen(task: str) -> dict[str, str]:
    with db.connect() as con:
        return {r[0]: r[1] for r in con.execute(
            "SELECT item_id, MAX(created_at) FROM speaking_attempts WHERE task = ? GROUP BY item_id", (task,))}


def tsp_task_stats(last_n: int = 20) -> dict[str, dict]:
    """과제별 최근 last_n 답변 평균 (점수 비율·단어 수)."""
    out = {}
    with db.connect() as con:
        for t in TSP_TASKS:
            rows = con.execute("SELECT points, max_points, words FROM speaking_attempts WHERE task = ? "
                               "ORDER BY id DESC LIMIT ?", (f"tsp:{t}", last_n)).fetchall()
            total = con.execute("SELECT COUNT(*) FROM speaking_attempts WHERE task = ?", (f"tsp:{t}",)).fetchone()[0]
            if rows:
                w = [r["words"] for r in rows if r["words"] is not None]
                out[t] = {"n": total, "ratio": sum(r["points"] / r["max_points"] for r in rows) / len(rows),
                          "words": sum(w) / len(w) if w else None}
    return out


def tsp_estimate(stats: dict[str, dict]) -> int | None:
    """다섯 유형을 모두 연습했으면 유형별 평균 비율로 11문항 점수를 채워 환산."""
    if any(t not in stats for t in TSP_TASKS):
        return None
    raw = sum(stats[t]["ratio"] * TSP_TASKS[t]["max"] * TSP_TASKS[t]["n"] * len(TSP_TASKS[t]["speak"]) for t in TSP_TASKS)
    return tsp_score(raw)


def opic_stats(last_n: int = 30) -> dict:
    with db.connect() as con:
        rows = con.execute("SELECT points, words FROM speaking_attempts WHERE exam = 'opic' ORDER BY id DESC LIMIT ?",
                           (last_n,)).fetchall()
        by_kind = {}
        for r in con.execute("SELECT task, item_id, qidx, points FROM speaking_attempts WHERE exam = 'opic'"):
            by_kind.setdefault(r["task"], []).append(r["points"])
        total = con.execute("SELECT COUNT(*) FROM speaking_attempts WHERE exam = 'opic'").fetchone()[0]
    if not rows:
        return {"n": 0, "avg": None, "words": None, "grade": None}
    w = [r["words"] for r in rows if r["words"] is not None]
    avg = sum(r["points"] for r in rows) / len(rows)
    words = sum(w) / len(w) if w else None
    return {"n": total, "avg": avg, "words": words, "grade": opic_grade(avg, words) if len(rows) >= 5 else None}


def topic_progress(exam: str = "opic") -> dict[str, dict]:
    """오픽 주제별 답변 수·평균 (문항 → 주제는 은행에서 찾는다)."""
    with db.connect() as con:
        rows = con.execute("SELECT task, item_id, points FROM speaking_attempts WHERE exam = ?", (exam,)).fetchall()
    return [dict(r) for r in rows]


def recent(exam: str, limit: int = 15) -> list[dict]:
    with db.connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT created_at, task, item_id, qidx, points, max_points, words, seconds, accuracy, response, mock_id "
            "FROM speaking_attempts WHERE exam = ? ORDER BY id DESC LIMIT ?", (exam, limit))]


def trend(exam: str, task: str | None = None, days: int = 60) -> list[dict]:
    """날짜별 평균 (점수 비율·단어 수) — 시간이 지나며 늘었는지 본다."""
    q = ("SELECT substr(created_at, 1, 10) d, COUNT(*) n, AVG(points / max_points) r, AVG(words) w "
         "FROM speaking_attempts WHERE exam = ? AND created_at >= date('now', ?)")
    args: list = [exam, f"-{days} days"]
    if task:
        q += " AND task = ?"
        args.append(task)
    q += " GROUP BY d ORDER BY d"
    with db.connect() as con:
        return [dict(r) for r in con.execute(q, args)]


# ---- 모의고사 -------------------------------------------------------------------------

def create_mock(exam: str, plan: list[dict], settings: dict | None = None) -> int:
    with db.connect() as con:
        cur = con.execute("INSERT INTO speaking_mocks(exam, created_at, plan, settings) VALUES (?,?,?,?)",
                          (exam, db.now(), json.dumps(plan, ensure_ascii=False), json.dumps(settings or {}, ensure_ascii=False)))
        return int(cur.lastrowid)


def get_mock(mid: int) -> dict | None:
    with db.connect() as con:
        r = con.execute("SELECT * FROM speaking_mocks WHERE id = ?", (mid,)).fetchone()
    if not r:
        return None
    m = dict(r)
    m["plan"] = json.loads(m["plan"])
    m["settings"] = json.loads(m["settings"] or "{}")
    m["result"] = json.loads(m["result"]) if m["result"] else None
    return m


def list_mocks(exam: str, limit: int = 20) -> list[dict]:
    with db.connect() as con:
        rows = con.execute("SELECT id, created_at, finished_at, score, result FROM speaking_mocks "
                           "WHERE exam = ? AND finished_at IS NOT NULL ORDER BY id DESC LIMIT ?", (exam, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["result"] = json.loads(d["result"]) if d["result"] else {}
        out.append(d)
    return out


def finish_mock(bank: SpeakingBank, mid: int, rows: list, duration: float | None = None) -> dict:
    m = get_mock(mid)
    if not m:
        raise ValueError("모의고사가 없습니다.")
    if m["finished_at"]:
        return m
    planned = {(s["ref"]["task"], s["ref"]["item_id"], s["ref"]["qidx"]) for u in m["plan"] for s in u["steps"]}
    rows = [r for r in rows if isinstance(r, dict)
            and (str(r.get("task")), str(r.get("item_id")), int(_num(r.get("qidx"), 0, 20) or 0)) in planned]
    saved = record(bank, m["exam"], rows, mock_id=mid)
    if not saved:
        raise ValueError("채점한 답변이 없습니다.")
    words = [r["words"] for r in saved if r["words"] is not None]
    avg_words = sum(words) / len(words) if words else None
    if m["exam"] == "tsp":
        got = {(r["task"], r["item_id"], r["qidx"]): r["points"] for r in saved}
        raw = sum(got.get(k, 0) for k in planned)              # 채점 안 한 문항은 0점
        score = tsp_score(raw)
        by_task = {}
        for r in saved:
            by_task.setdefault(r["task"], []).append(r["points"] / r["max"])
        result = {"raw": raw, "raw_max": TSP_RAW_MAX, "score": score, "level": tsp_level(score),
                  "by_task": {t: sum(v) / len(v) for t, v in by_task.items()}, "words": avg_words,
                  "answered": len(saved), "total": len(planned), "duration": duration}
        label = str(score)
    else:
        scored = [r for r in saved if not r["item_id"].startswith("oq-intro")]   # 자기소개는 채점 비중 낮음 → 제외
        pts = [r["points"] for r in scored] or [r["points"] for r in saved]
        avg = sum(pts) / len(pts)
        grade = opic_grade(avg, avg_words)
        result = {"avg": avg, "grade": grade, "grade_name": OPIC_GRADE_NAME.get(grade, ""), "words": avg_words,
                  "answered": len(saved), "total": len(planned), "duration": duration,
                  "level": m["settings"].get("level")}
        label = grade or ""
    with db.connect() as con:
        con.execute("UPDATE speaking_mocks SET finished_at = ?, result = ?, score = ? WHERE id = ?",
                    (db.now(), json.dumps(result, ensure_ascii=False), label, mid))
    return get_mock(mid)
