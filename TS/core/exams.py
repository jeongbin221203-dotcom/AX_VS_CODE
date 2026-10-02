"""시험 카테고리와 각 시험의 등급 체계·구성 요약 (말하기 시험 가이드 화면에서도 쓴다).
(등급·구성은 공개 자료 기준 요약이며 시험 주관사 공식 자료로 다시 확인할 것)
"""
from __future__ import annotations

EXAMS = {
    "toeic": {"name": "토익", "en": "TOEIC", "ready": True, "endpoint": "main.dashboard"},
    "toefl": {"name": "토플", "en": "TOEFL iBT", "ready": True, "endpoint": "toefl.home"},
    "toeic-speaking": {
        "name": "토익스피킹", "en": "TOEIC Speaking", "ready": True, "endpoint": "speaking.tsp_home",
        "summary": "컴퓨터로 11문항에 영어로 말하는 시험 · 약 20분 · 0~200점 · 레벨 8단계",
        "scale_title": "등급 (점수 → 레벨)",
        "scale": [("Advanced High", "200"), ("Advanced Mid", "180~190"), ("Advanced Low", "160~170"),
                  ("Intermediate High", "140~150"), ("Intermediate Mid 1~3", "110~130"),
                  ("Intermediate Low", "90~100"), ("Novice High", "60~80"), ("Novice Mid / Low", "0~50")],
        "sections_title": "문항 구성",
        "sections": [("Q1~2", "문장 읽기 (Read a text aloud)", "준비 45초 · 답변 45초"),
                     ("Q3~4", "사진 묘사 (Describe a picture)", "준비 45초 · 답변 30초"),
                     ("Q5~7", "질문에 답하기 (Respond to questions)", "준비 3초 · 답변 15~30초"),
                     ("Q8~10", "표 정보로 답하기 (Respond using information)", "표 읽기 45초 · 답변 15~30초"),
                     ("Q11", "의견 말하기 (Express an opinion)", "준비 45초 · 답변 60초")],
        "planned": ["실제 시험처럼 준비·답변 타이머와 녹음, 다시 듣기",
                    "문항 유형별 답변 틀(템플릿)과 레벨별 모범 답안",
                    "레벨 목표(IM3·IH·AL)별 학습 경로",
                    "자기 평가 체크리스트(발음·유창성·문법·내용)",
                    "녹음 기록과 시간 경과 비교"],
    },
    "opic": {
        "name": "오픽", "en": "OPIc", "ready": True, "endpoint": "speaking.opic_home",
        "summary": "1:1 인터뷰형 말하기 시험 · 약 40분 · 12~15문항 · 사전 설문(Background Survey)으로 주제가 정해짐",
        "scale_title": "등급 (높음 → 낮음, 최고 AL)",
        "scale": [("AL", "Advanced Low"), ("IH", "Intermediate High"), ("IM3", "Intermediate Mid 3"),
                  ("IM2", "Intermediate Mid 2"), ("IM1", "Intermediate Mid 1"), ("IL", "Intermediate Low"),
                  ("NH", "Novice High"), ("NM", "Novice Mid"), ("NL", "Novice Low")],
        "sections_title": "문항 유형",
        "sections": [("자기소개", "첫 문항 (채점 비중 낮음)", ""),
                     ("묘사", "좋아하는 장소·사람·물건 묘사", "설문 주제"),
                     ("경험", "과거 경험·최근 있었던 일", "설문 주제"),
                     ("롤플레이", "상황극: 질문하기·문제 해결", "난이도 5~6"),
                     ("돌발", "설문에 없는 주제·비교·사회 이슈", "")],
        "planned": ["Background Survey 추천 조합과 주제별 답변 뼈대",
                    "목표 등급(IM2·IH·AL)별 답변 길이·표현 가이드",
                    "문항 녹음·타이머, 자주 쓰는 연결 표현 모음",
                    "롤플레이·돌발 질문 연습 세트"],
    },
}
