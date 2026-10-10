"""HTML 화면 파일들을 한꺼번에 만든다 (화면마다 머리·스크립트 목록이 거의 같아서 실수를 줄이려고).

실행:  python tools/make_pages.py            (TS_word 폴더에서)

새 화면을 더하려면 아래 PAGES 에 한 줄을 넣고 다시 실행한다.
  "파일.html": (제목, [스크립트...], {body 속성})
스크립트 묶음
  BASE   모든 화면이 읽는 기본 (도구·점수표·메뉴 정의·단어/시험 저장소·음성·화면 공통)
  EXAM   토익 문제 화면에 필요한 것 (문제 개수 요약·가이드 데이터·문제 은행·통계·계획·세션)
화면을 더한 뒤에는 sw.js 의 ASSETS 에 새 파일을 넣고 CACHE 이름을 올릴 것.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BASE = ["js/tsutil.js", "js/scoring.js", "js/exams.js", "js/store.js", "js/tsstore.js", "js/tts.js", "js/ui.js"]
EXAM = ["data/meta.js", "data/guide.js", "js/bank.js", "js/stats.js", "js/planner.js", "js/sessions.js"]
TOEFL = ["data/meta.js", "js/toefl-core.js"]               # 토플 화면 공통 (문제 데이터는 과제별로 필요할 때 읽음)
TOEFL_SPEECH = ["js/toefl-speech.js"]                       # 말하기(음성 인식·녹음)를 쓰는 화면
SPK = ["js/spk-core.js"]                                    # 토익스피킹·오픽 공통 (문제 데이터는 화면이 필요할 때 읽음; data/meta.js 는 EXAM/TOEFL 에 있음)
CHART = ["vendor/chart.umd.min.js", "js/charts.js"]

PAGES: dict[str, tuple[str, list[str], dict[str, str]]] = {
    # 허브·설정
    "index.html": ("TS", BASE + EXAM + TOEFL[1:] + SPK + ["js/hub.js", "js/hub-extra.js"], {}),
    "settings.html": ("설정 · TS", BASE + ["js/backup.js", "js/settings.js"], {}),
    # 단어 (기존)
    "words.html": ("단어 · TS", BASE + ["js/home.js"], {}),
    "study.html": ("카드 · TS 단어", BASE + ["js/study.js"], {}),
    "quiz.html": ("시험 · TS 단어", BASE + ["js/quiz.js"], {}),
    "list.html": ("단어장 · TS 단어", BASE + ["js/list.js"], {}),
    "listen.html": ("듣기 · TS 단어", BASE + ["data/audio-list.js", "js/listen.js"], {}),
    # 토익
    "toeic.html": ("오늘의 토익 · TS", BASE + EXAM + CHART + ["js/toeic.js"], {}),
    "practice.html": ("파트 연습 · TS", BASE + EXAM + ["js/practice.js"], {}),
    "diagnostic.html": ("진단 테스트 · TS", BASE + EXAM + ["js/diagnostic.js"], {}),
    "mock.html": ("모의고사 · TS", BASE + EXAM + ["js/mock.js"], {}),
    "solve.html": ("풀이 · TS", BASE + EXAM + ["js/engine.js", "js/solve.js"], {"class": "ward quiz-page"}),
    "result.html": ("결과 · TS", BASE + EXAM + ["js/engine.js", "js/result.js"], {"class": "ward quiz-page"}),
    "review.html": ("오답노트 · TS", BASE + EXAM + ["js/review.js"], {}),
    "dictation.html": ("받아쓰기 · TS", BASE + EXAM + ["js/dictation.js"], {}),
    "stats.html": ("통계 · TS", BASE + EXAM + CHART + ["js/stats-page.js"], {}),
    "history.html": ("전체 기록 · TS", BASE + EXAM + ["js/history.js"], {}),
    "guide.html": ("등급 가이드 · TS", BASE + EXAM + ["js/guide.js"], {}),
    # 토플
    "toefl.html": ("토플 · TS", BASE + TOEFL + ["js/toefl-home.js"], {}),
    "toefl-practice.html": ("토플 연습 · TS", BASE + TOEFL + TOEFL_SPEECH + ["js/toefl-practice.js"], {"class": "ward quiz-page"}),
    "toefl-mock.html": ("토플 모의고사 · TS", BASE + TOEFL + ["js/toefl-mock.js"], {}),
    "toefl-mock-run.html": ("토플 모의고사 · TS", BASE + TOEFL + TOEFL_SPEECH + ["js/toefl-mock-run.js"], {"class": "ward quiz-page"}),
    "toefl-mock-result.html": ("토플 모의고사 결과 · TS", BASE + TOEFL + ["js/toefl-mock-result.js"], {}),
    "toefl-review.html": ("오답노트 · 토플 · TS", BASE + TOEFL + ["js/toefl-review.js"], {}),
    "toefl-history.html": ("기록 · 토플 · TS", BASE + TOEFL + ["js/toefl-history.js"], {}),
    # 토익스피킹·오픽 (js/spk-pages.js 목록·안내 화면, js/spk-run.js 진행 화면 — body 의 data-spk 로 화면 종류를 정함)
    "toeic-speaking.html": ("토익스피킹 · TS", BASE + SPK + ["js/spk-pages.js"], {"data-spk": "toeic-speaking"}),
    "tsp-guide.html": ("답변 틀 · 토익스피킹 · TS", BASE + SPK + ["js/spk-pages.js"], {"data-spk": "tsp-guide"}),
    "tsp-mock.html": ("모의고사 · 토익스피킹 · TS", BASE + SPK + ["js/spk-pages.js"], {"data-spk": "tsp-mock"}),
    "tsp-mock-result.html": ("모의고사 결과 · 토익스피킹 · TS", BASE + SPK + ["js/spk-pages.js"], {"data-spk": "tsp-mock-result"}),
    "tsp-practice.html": ("유형별 연습 · 토익스피킹 · TS", BASE + SPK + ["js/spk-run.js"], {"class": "ward quiz-page", "data-spk": "tsp-practice"}),
    "tsp-mock-run.html": ("모의고사 · 토익스피킹 · TS", BASE + SPK + ["js/spk-run.js"], {"class": "ward quiz-page", "data-spk": "tsp-mock-run"}),
    "opic.html": ("오픽 · TS", BASE + SPK + ["js/spk-pages.js"], {"data-spk": "opic"}),
    "opic-survey.html": ("설문·난이도 · 오픽 · TS", BASE + SPK + ["js/spk-pages.js"], {"data-spk": "opic-survey"}),
    "opic-guide.html": ("답변 틀 · 오픽 · TS", BASE + SPK + ["js/spk-pages.js"], {"data-spk": "opic-guide"}),
    "opic-mock.html": ("모의고사 · 오픽 · TS", BASE + SPK + ["js/spk-pages.js"], {"data-spk": "opic-mock"}),
    "opic-mock-result.html": ("모의고사 결과 · 오픽 · TS", BASE + SPK + ["js/spk-pages.js"], {"data-spk": "opic-mock-result"}),
    "opic-practice.html": ("주제별 연습 · 오픽 · TS", BASE + SPK + ["js/spk-run.js"], {"class": "ward quiz-page", "data-spk": "opic-practice"}),
    "opic-mock-run.html": ("모의고사 · 오픽 · TS", BASE + SPK + ["js/spk-run.js"], {"class": "ward quiz-page", "data-spk": "opic-mock-run"}),
    "speaking-history.html": ("기록 · 말하기 · TS", BASE + SPK + ["js/spk-pages.js"], {"data-spk": "speaking-history"}),
}

TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <link rel="icon" href="favicon.svg" type="image/svg+xml">
  <link rel="icon" href="favicon.ico" sizes="48x48">
  <link rel="apple-touch-icon" href="apple-touch-icon.png">
  <meta name="theme-color" content="#2F5BD3">
  <link rel="stylesheet" href="css/app.css">
  <link rel="stylesheet" href="css/ward.css">
  <script src="js/theme.js"></script>
</head>
<body{attrs}>
<div id="hdr"></div>
<main class="wrap" id="app"><div class="empty">불러오는 중…</div></main>
{scripts}
</body>
</html>
"""


def render(title: str, scripts: list[str], attrs: dict[str, str]) -> str:
    attrs = {"class": "ward", **attrs}
    a = "".join(f' {k}="{v}"' for k, v in attrs.items())
    return TEMPLATE.format(title=title, attrs=a, scripts="\n".join(f'<script src="{s}"></script>' for s in scripts))


def main() -> None:
    for name, (title, scripts, attrs) in PAGES.items():
        path = ROOT / name
        if name == "index.html" or name.endswith(".html"):
            for s in scripts:
                if not (ROOT / s).exists():
                    print(f"경고: {name} 이 읽는 {s} 가 없습니다")
        path.write_text(render(title, scripts, attrs), encoding="utf-8", newline="\n")
    print(f"{len(PAGES)}개 화면 파일을 만들었습니다.")


if __name__ == "__main__":
    main()
