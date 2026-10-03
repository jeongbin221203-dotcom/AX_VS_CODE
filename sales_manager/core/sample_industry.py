"""업종별 샘플 데이터 — 업종마다 다른 품목·거래처·영업기회·결제 습관을 가진 거래처를 만든다 (개발·시연용)

  업종: 제조 · 유통 · 건설 · IT/SW · 의료 · 교육 · 금융 · 공공 (회사 설정의 업종 목록과 같은 이름)
  업종마다: 품목 마스터(과세·영세·면세 섞임), 거래처(올바른 사업자번호·여신·결제조건·ERP 코드), 담당자 여럿,
  매달 반복 구매 매출, 업종별 입금 습관(공공은 늦지만 다 냄, 건설은 부분 입금·연체가 잦음 등), 반품,
  영업기회 → 견적 → 수주 → 분할 납품, 업종에 맞는 영업활동.
  모두 화면과 같은 저장 함수를 거치므로 입금 내역·감사로그·ERP 대기열이 실제처럼 쌓인다.
  여러 번 실행하면 그만큼 더 쌓인다(이름 뒤 번호가 이어짐). 월 마감한 달의 날짜는 건너뛴다.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Optional

from . import sales_db as db

# 품목: (코드, 품목명, 규격, 분류, 단위, 정가, 과세구분)
# deals: (영업기회 이름, [품목코드...])  regular: 매달 반복 구매 후보
# pay: (입금 확률, 그중 전액 입금 확률, 만기 대비 입금일 범위(일))  returns: 반품 확률
PRESETS: dict[str, dict] = {
    "제조": {
        "label": "제조업", "icon": "🏭", "memo": "제조업 샘플 데이터", "corp": "(주)", "branch": "공장", "erp": "M",
        "products": [
            ("RM-SUS304", "스테인리스 판재 SUS304", "2t × 1219 × 2438", "원자재", "톤", 4_850_000, "과세"),
            ("RM-AL6061", "알루미늄 압출재 6061", "T6, 6m", "원자재", "톤", 5_600_000, "과세"),
            ("RM-SS400", "일반구조용 강판 SS400", "6t", "원자재", "톤", 1_050_000, "과세"),
            ("PT-BRG6205", "베어링 6205ZZ", "25×52×15", "부품", "EA", 6_800, "과세"),
            ("PT-SRV1K", "AC 서보모터 1kW", "220V, 3000rpm", "부품", "EA", 1_380_000, "과세"),
            ("PT-PLC-CPU", "PLC CPU 모듈", "I/O 256점", "부품", "EA", 2_150_000, "과세"),
            ("PT-SENS-PX", "근접 센서", "M18, PNP", "부품", "EA", 48_000, "과세"),
            ("EQ-CNC-5AX", "5축 CNC 머시닝센터", "테이블 650mm", "설비", "대", 385_000_000, "과세"),
            ("EQ-ROBOT-6", "6축 산업용 로봇", "가반하중 20kg", "설비", "대", 92_000_000, "과세"),
            ("EQ-PANEL", "자동화 제어반", "주문 제작", "설비", "식", 28_000_000, "과세"),
            ("SV-MOLD", "사출 금형 제작", "2캐비티", "가공서비스", "벌", 34_000_000, "과세"),
            ("SV-MACH", "정밀 가공 임가공", "시간당", "가공서비스", "시간", 95_000, "과세"),
            ("SV-PM", "설비 정기 점검", "분기 1회", "유지보수", "회", 1_800_000, "과세"),
            ("CS-OIL-20", "절삭유 수용성", "20L", "소모품", "통", 128_000, "과세"),
            ("CS-TOOL", "초경 엔드밀 세트", "Ø6~12, 10종", "소모품", "세트", 420_000, "과세"),
            ("EX-PKG-MFG", "수출용 부품 키트", "선적 단위", "부품", "식", 18_000_000, "영세"),
        ],
        "regular": ["RM-SUS304", "RM-AL6061", "RM-SS400", "PT-BRG6205", "PT-SENS-PX", "CS-OIL-20", "CS-TOOL", "SV-MACH"],
        "lot_units": ["대", "식", "벌"],
        "names": ["한성정밀", "대성금속", "세진오토텍", "동양기계", "우진테크", "삼화공업", "태성산업", "경일엠텍", "신화정공",
                  "부광메탈", "영신하이텍", "대원오토", "미래소재", "제일프레스", "광명기공", "현대정밀부품", "성우테크",
                  "동아다이캐스팅", "한국베어링", "진흥전기", "서울사출", "유성엔지니어링", "청림오토메이션", "금강스틸", "정우로보틱스"],
        "regions": ["경기 화성시", "경기 안산시 반월공단", "인천 남동공단", "충남 아산시", "경남 창원시", "경북 구미시", "울산 북구",
                    "대구 달서구 성서공단", "광주 광산구 하남산단", "전북 군산시"],
        "street": "산업로",
        "deals": [("노후 CNC 설비 교체", ["EQ-CNC-5AX"]), ("조립라인 로봇 도입", ["EQ-ROBOT-6", "EQ-PANEL"]),
                  ("연간 베어링·센서 단가 계약", ["PT-BRG6205", "PT-SENS-PX"]), ("스마트팩토리 제어 고도화", ["PT-PLC-CPU", "EQ-PANEL"]),
                  ("신규 차종 사출 금형", ["SV-MOLD"]), ("원자재 연간 공급", ["RM-SUS304", "RM-AL6061"]),
                  ("설비 유지보수 계약", ["SV-PM"]), ("해외 법인 부품 수출", ["EX-PKG-MFG"])],
        "activities": ["공장 방문 — 생산라인 확인", "샘플 테스트 결과 협의", "구매팀 단가 협상", "품질 이슈 대응 회의", "시운전 참관",
                       "설비 사양 검토 회의", "연간 계약 갱신 협의", "납기 일정 조율", "기술 세미나 초청", "현장 불량 원인 분석"],
        "next": ["견적 수정 발송", "샘플 재제출", "현장 재방문"],
        "contacts": [("구매", "구매팀", "과장"), ("회계·세금계산서", "재무팀", "대리"), ("현업", "생산기술팀", "차장"),
                     ("의사결정", "공장장실", "상무"), ("기술", "품질보증팀", "책임")],
        "grades": {"VIP": 800_000_000, "A": 300_000_000, "B": 120_000_000, "C": 40_000_000},
        "terms": [30, 45, 60, 90], "pay": (0.75, 0.82, (-10, 20)), "returns": 0.03, "return_reason": "입고 검사 불량",
        "competitors": ["A사", "B사", "해외 OEM"],
    },
    "유통": {
        "label": "유통·도매", "icon": "🛒", "memo": "유통업 샘플 데이터", "corp": "(주)", "branch": "지점", "erp": "D",
        "products": [
            ("DS-RICE-20", "국내산 백미", "20kg", "농산물", "포", 62_000, "면세"),
            ("DS-EGG-30", "특란", "30구", "농산물", "판", 8_900, "면세"),
            ("DS-OIL-18", "식용유 대두유", "18L", "가공식품", "통", 54_000, "과세"),
            ("DS-NOODLE", "봉지 라면", "40입", "가공식품", "박스", 28_500, "과세"),
            ("DS-WATER", "생수", "2L × 6", "음료", "팩", 4_200, "과세"),
            ("DS-TISSUE", "롤 화장지", "30롤", "생활용품", "팩", 15_800, "과세"),
            ("DS-DETERG", "세탁 세제", "3L × 4", "생활용품", "박스", 39_000, "과세"),
            ("DS-PB-SNACK", "PB 스낵 기획전", "행사 단위", "PB상품", "식", 12_000_000, "과세"),
            ("DS-SHELF", "매장 진열대", "1200×450×1800", "매장설비", "대", 680_000, "과세"),
            ("DS-LOGI", "물류 대행 수수료", "월", "서비스", "월", 3_500_000, "과세"),
        ],
        "regular": ["DS-RICE-20", "DS-EGG-30", "DS-OIL-18", "DS-NOODLE", "DS-WATER", "DS-TISSUE", "DS-DETERG"],
        "lot_units": ["식", "대", "월"],
        "names": ["한빛마트", "우리식자재", "새벽유통", "동네마켓", "정성푸드", "그린상회", "대한식품유통", "행복마트",
                  "온누리도매", "싱싱청과", "바른유통", "한마음편의점", "제일식자재", "미소마트", "알뜰상사", "서해수산유통"],
        "regions": ["서울 송파구 가락동", "경기 이천시", "경기 용인시 처인구", "인천 서구", "대전 대덕구", "부산 강서구", "광주 북구",
                    "충북 청주시", "경남 김해시"],
        "street": "유통단지로",
        "deals": [("신규 점포 입점 상품 공급", ["DS-NOODLE", "DS-WATER", "DS-TISSUE"]), ("PB 스낵 행사 기획", ["DS-PB-SNACK"]),
                  ("매장 리뉴얼 진열대", ["DS-SHELF"]), ("연간 쌀 공급 계약", ["DS-RICE-20"]), ("물류 대행 계약", ["DS-LOGI"])],
        "activities": ["매장 방문 — 진열 상태 확인", "발주 담당자 미팅", "행사 일정 협의", "반품 정산 협의", "신상품 소개",
                       "판매 데이터 공유", "물류센터 납품 시간 조율", "단가 인상 안내"],
        "next": ["행사 제안서 발송", "샘플 상품 전달", "정산서 송부"],
        "contacts": [("구매", "상품팀", "MD"), ("회계·세금계산서", "경리팀", "주임"), ("현업", "점포운영팀", "점장"),
                     ("의사결정", "영업본부", "이사")],
        "grades": {"VIP": 300_000_000, "A": 120_000_000, "B": 50_000_000, "C": 15_000_000},
        "terms": [15, 30, 45], "pay": (0.88, 0.9, (-3, 10)), "returns": 0.06, "return_reason": "유통기한 임박·파손",
        "competitors": ["대형 도매상", "온라인 B2B몰"],
    },
    "건설": {
        "label": "건설", "icon": "🏗️", "memo": "건설업 샘플 데이터", "corp": "(주)", "branch": "현장", "erp": "C",
        "products": [
            ("CN-REBAR", "이형철근 SD400", "D13", "자재", "톤", 980_000, "과세"),
            ("CN-REMICON", "레미콘", "25-24-150", "자재", "㎥", 92_000, "과세"),
            ("CN-FORM", "알루미늄 거푸집 임대", "월", "임대", "월", 4_200_000, "과세"),
            ("CN-CRANE", "타워크레인 임대", "8톤, 월", "임대", "월", 18_500_000, "과세"),
            ("CN-ELEV", "승강기 설치 공사", "15인승", "공사", "대", 78_000_000, "과세"),
            ("CN-HVAC", "공조 설비 공사", "주문 사양", "공사", "식", 145_000_000, "과세"),
            ("CN-SAFETY", "안전 장구 세트", "안전모·벨트·조끼", "소모품", "세트", 89_000, "과세"),
            ("CN-INSP", "구조 안전 진단", "건별", "용역", "건", 6_500_000, "과세"),
        ],
        "regular": ["CN-REBAR", "CN-REMICON", "CN-FORM", "CN-SAFETY"],
        "lot_units": ["식", "대", "건"],
        "names": ["대림종합건설", "한양토건", "삼우건설", "동부E&C", "태영개발", "신일주택", "우성종합건설", "세움건축",
                  "푸른도시개발", "남해건설", "백두토목", "한라엔지니어링"],
        "regions": ["경기 평택시 고덕신도시", "세종시 행정중심복합도시", "인천 송도국제도시", "부산 에코델타시티", "경기 하남시 교산",
                    "충남 천안시 서북구", "대구 수성구"],
        "street": "신도시로",
        "deals": [("아파트 현장 승강기 공사", ["CN-ELEV"]), ("물류센터 공조 공사", ["CN-HVAC"]),
                  ("현장 철근 연간 공급", ["CN-REBAR"]), ("타워크레인 장기 임대", ["CN-CRANE"]),
                  ("노후 건물 안전 진단", ["CN-INSP"])],
        "activities": ["현장 방문 — 공정 확인", "기성 청구 협의", "설계 변경 회의", "자재 반입 일정 조율", "안전 점검 동행",
                       "입찰 설명회 참석", "하자 보수 협의", "준공 검사 대응"],
        "next": ["기성 청구서 제출", "변경 견적 발송", "현장 재방문"],
        "contacts": [("구매", "자재팀", "과장"), ("회계·세금계산서", "공무팀", "대리"), ("현업", "현장사무소", "현장소장"),
                     ("의사결정", "건축본부", "전무")],
        "grades": {"VIP": 1_500_000_000, "A": 600_000_000, "B": 250_000_000, "C": 80_000_000},
        "terms": [60, 90, 120], "pay": (0.62, 0.55, (0, 45)), "returns": 0.01, "return_reason": "규격 불일치",
        "competitors": ["지역 업체", "원청 직발주"],
    },
    "IT/SW": {
        "label": "IT·소프트웨어", "icon": "💻", "memo": "IT업 샘플 데이터", "corp": "(주)", "branch": "사업부", "erp": "I",
        "products": [
            ("IT-SAAS-STD", "클라우드 ERP 구독 (Standard)", "사용자당 월", "구독", "명", 45_000, "과세"),
            ("IT-SAAS-ENT", "클라우드 ERP 구독 (Enterprise)", "사용자당 월", "구독", "명", 89_000, "과세"),
            ("IT-LIC-PERP", "그룹웨어 영구 라이선스", "100 사용자", "라이선스", "식", 48_000_000, "과세"),
            ("IT-MAINT", "SW 유지보수", "연 18%", "유지보수", "월", 1_200_000, "과세"),
            ("IT-SI-MM", "SI 개발 인력", "중급 개발자", "용역", "MM", 9_800_000, "과세"),
            ("IT-CONSULT", "업무 프로세스 컨설팅", "주 단위", "용역", "주", 7_500_000, "과세"),
            ("IT-SERVER", "x86 서버", "2CPU · 512GB", "HW", "대", 23_000_000, "과세"),
            ("IT-TRAIN", "사용자 교육", "1일 8시간", "교육", "회", 1_500_000, "과세"),
            ("IT-EXPORT", "해외 법인 SW 라이선스", "수출", "라이선스", "식", 36_000_000, "영세"),
        ],
        "regular": ["IT-SAAS-STD", "IT-SAAS-ENT", "IT-MAINT"],
        "lot_units": ["식", "대", "MM", "주"],
        "names": ["넥스트소프트", "클라우드브릿지", "데이터온", "스마트웍스", "코드플러스", "인사이트랩", "블루큐브",
                  "디지털허브", "에이아이솔루션", "테크노베이션", "오픈시스템즈", "시냅스IT"],
        "regions": ["서울 강남구 테헤란로", "경기 성남시 판교", "서울 구로구 디지털단지", "서울 마포구 상암DMC", "대전 유성구 대덕밸리",
                    "부산 해운대구 센텀시티"],
        "street": "벤처로",
        "deals": [("ERP 클라우드 전환", ["IT-SAAS-ENT", "IT-CONSULT"]), ("그룹웨어 구축", ["IT-LIC-PERP", "IT-TRAIN"]),
                  ("차세대 시스템 SI", ["IT-SI-MM"]), ("서버 증설", ["IT-SERVER"]), ("해외 법인 확산", ["IT-EXPORT"])],
        "activities": ["제품 데모", "요구사항 워크숍", "PoC 결과 보고", "보안 심사 대응", "계약 조건 협의", "사용자 교육 진행",
                       "장애 회고 미팅", "갱신 안내 통화"],
        "next": ["제안서 보완", "데모 계정 발급", "갱신 견적 발송"],
        "contacts": [("구매", "구매팀", "매니저"), ("회계·세금계산서", "재경팀", "매니저"), ("현업", "정보전략팀", "팀장"),
                     ("의사결정", "CIO실", "CIO"), ("기술", "인프라팀", "책임")],
        "grades": {"VIP": 500_000_000, "A": 200_000_000, "B": 80_000_000, "C": 20_000_000},
        "terms": [30, 45, 60], "pay": (0.85, 0.92, (-5, 15)), "returns": 0.0, "return_reason": "",
        "competitors": ["글로벌 SaaS", "국내 SI 대기업"],
    },
    "의료": {
        "label": "의료·병원", "icon": "🏥", "memo": "의료업 샘플 데이터", "corp": "", "branch": "분원", "erp": "H",
        "products": [
            ("MD-GLOVE", "니트릴 검사 장갑", "M, 100매", "소모품", "박스", 9_800, "과세"),
            ("MD-SYRINGE", "일회용 주사기", "5mL, 100개", "소모품", "박스", 14_500, "면세"),
            ("MD-MASK", "KF94 의료용 마스크", "50매", "소모품", "박스", 21_000, "면세"),
            ("MD-MONITOR", "환자 감시 장치", "5파라미터", "의료기기", "대", 12_800_000, "과세"),
            ("MD-US", "초음파 진단기", "컬러 도플러", "의료기기", "대", 86_000_000, "과세"),
            ("MD-BED", "전동 병상", "3모터", "의료기기", "대", 3_900_000, "과세"),
            ("MD-MAINT", "의료기기 점검", "연간 계약", "유지보수", "회", 2_400_000, "과세"),
        ],
        "regular": ["MD-GLOVE", "MD-SYRINGE", "MD-MASK"],
        "lot_units": ["대", "회"],
        "names": ["한빛중앙병원", "한마음의원", "새봄요양병원", "밝은눈안과", "튼튼정형외과", "미소치과병원", "참사랑재활병원",
                  "늘푸른한방병원", "온가족내과", "희망여성병원"],
        "regions": ["서울 종로구", "경기 고양시 일산동구", "인천 연수구", "대구 중구", "부산 서구", "광주 동구", "전북 전주시 완산구"],
        "street": "병원로",
        "deals": [("영상 진단 장비 교체", ["MD-US"]), ("병동 리모델링 병상 교체", ["MD-BED", "MD-MONITOR"]),
                  ("연간 소모품 공급", ["MD-GLOVE", "MD-SYRINGE"]), ("의료기기 점검 계약", ["MD-MAINT"])],
        "activities": ["원무과 미팅", "진료과 장비 시연", "구매위원회 발표", "감염관리실 협의", "입찰 서류 제출", "사용 교육",
                       "A/S 접수 대응"],
        "next": ["시연 일정 확정", "입찰 서류 보완", "견적 재제출"],
        "contacts": [("구매", "구매팀", "주임"), ("회계·세금계산서", "원무과", "계장"), ("현업", "영상의학과", "실장"),
                     ("의사결정", "행정부원장실", "부원장")],
        "grades": {"VIP": 400_000_000, "A": 150_000_000, "B": 60_000_000, "C": 20_000_000},
        "terms": [30, 60, 90], "pay": (0.8, 0.85, (0, 30)), "returns": 0.02, "return_reason": "포장 파손",
        "competitors": ["글로벌 의료기기사", "대리점"],
    },
    "교육": {
        "label": "교육", "icon": "🎓", "memo": "교육 샘플 데이터", "corp": "", "branch": "캠퍼스", "erp": "E",
        "products": [
            ("ED-TABLET", "학생용 태블릿", "10.9형, 128GB", "기자재", "대", 620_000, "과세"),
            ("ED-BOARD", "전자칠판", "86형", "기자재", "대", 3_200_000, "과세"),
            ("ED-LMS", "학습관리시스템 구독", "학생당 월", "구독", "명", 2_500, "과세"),
            ("ED-BOOK", "교재", "학기용", "교재", "권", 18_000, "면세"),
            ("ED-TRAIN", "교원 연수", "1일 과정", "교육", "회", 2_800_000, "면세"),
            ("ED-LAB", "과학실 실험 키트", "30인", "기자재", "세트", 1_450_000, "과세"),
        ],
        "regular": ["ED-LMS", "ED-BOOK", "ED-LAB"],
        "lot_units": ["대", "회"],
        "names": ["한국대학교", "미래고등학교", "푸른중학교", "새솔초등학교", "글로벌외국어학원", "바른에듀", "지혜평생교육원",
                  "창의과학고", "누리직업전문학교", "한빛사이버대학"],
        "regions": ["서울 관악구", "경기 수원시 영통구", "대전 서구", "대구 북구", "부산 금정구", "강원 춘천시", "전남 순천시"],
        "street": "학교로",
        "deals": [("스마트 교실 구축", ["ED-BOARD", "ED-TABLET"]), ("LMS 도입", ["ED-LMS", "ED-TRAIN"]),
                  ("학기 교재 공급", ["ED-BOOK"]), ("과학실 현대화", ["ED-LAB"])],
        "activities": ["행정실 방문", "교사 대상 시연", "학기 예산 협의", "입찰 공고 확인", "연수 일정 조율", "학부모 설명회 지원"],
        "next": ["예산 일정 확인", "시연 장비 회수", "견적 재제출"],
        "contacts": [("구매", "행정실", "주무관"), ("회계·세금계산서", "행정실", "실장"), ("현업", "정보부", "부장교사"),
                     ("의사결정", "교장실", "교장")],
        "grades": {"VIP": 200_000_000, "A": 80_000_000, "B": 30_000_000, "C": 10_000_000},
        "terms": [30, 60], "pay": (0.9, 0.95, (5, 40)), "returns": 0.02, "return_reason": "초기 불량",
        "competitors": ["교육청 일괄 구매", "타 에듀테크"],
    },
    "금융": {
        "label": "금융", "icon": "🏦", "memo": "금융업 샘플 데이터", "corp": "", "branch": "지점", "erp": "F",
        "products": [
            ("FN-KIOSK", "무인 키오스크", "카드·통장 겸용", "기기", "대", 18_500_000, "과세"),
            ("FN-ATM-MT", "ATM 유지보수", "월", "유지보수", "월", 420_000, "과세"),
            ("FN-SEC", "보안 관제 서비스", "월", "서비스", "월", 6_800_000, "과세"),
            ("FN-DOC", "문서 스캔·보관", "1,000매", "서비스", "건", 350_000, "과세"),
            ("FN-CONSULT", "규제 대응 컨설팅", "프로젝트", "용역", "식", 95_000_000, "과세"),
        ],
        "regular": ["FN-ATM-MT", "FN-SEC", "FN-DOC"],
        "lot_units": ["대", "식"],
        "names": ["한국저축은행", "미래캐피탈", "새마을금고 중앙", "든든보험", "열린증권", "희망신용협동조합", "바른자산운용", "누리카드"],
        "regions": ["서울 중구 을지로", "서울 영등포구 여의도", "부산 남구 문현금융단지", "경기 성남시 분당구"],
        "street": "금융로",
        "deals": [("지점 무인화", ["FN-KIOSK"]), ("보안 관제 전환", ["FN-SEC"]), ("규제 대응 프로젝트", ["FN-CONSULT"]),
                  ("문서 전자화", ["FN-DOC"])],
        "activities": ["보안성 심의 대응", "IT본부 미팅", "지점 실사", "계약 조건 검토", "준법감시 협의", "분기 운영 보고"],
        "next": ["보안 서약서 제출", "제안서 보완", "운영 보고서 송부"],
        "contacts": [("구매", "총무부", "차장"), ("회계·세금계산서", "재무회계부", "과장"), ("현업", "IT본부", "부장"),
                     ("의사결정", "경영지원본부", "본부장"), ("기술", "정보보호부", "CISO")],
        "grades": {"VIP": 600_000_000, "A": 250_000_000, "B": 100_000_000, "C": 30_000_000},
        "terms": [30], "pay": (0.97, 0.98, (-2, 5)), "returns": 0.0, "return_reason": "",
        "competitors": ["금융 IT 자회사"],
    },
    "공공": {
        "label": "공공기관", "icon": "🏛️", "memo": "공공기관 샘플 데이터", "corp": "", "branch": "청사", "erp": "G",
        "products": [
            ("PB-CCTV", "방범 CCTV", "200만 화소, 설치 포함", "기기", "대", 1_250_000, "과세"),
            ("PB-PC", "업무용 PC", "조달 규격", "기기", "대", 1_180_000, "과세"),
            ("PB-SI", "행정 정보시스템 구축", "조달 사업", "용역", "식", 320_000_000, "과세"),
            ("PB-MAINT", "정보시스템 유지관리", "월", "유지보수", "월", 8_500_000, "과세"),
            ("PB-CLEAN", "청사 미화 용역", "월", "용역", "월", 12_000_000, "과세"),
        ],
        "regular": ["PB-MAINT", "PB-CLEAN", "PB-PC"],
        "lot_units": ["식"],
        "names": ["가온시청", "누리구청", "한국미래산업진흥원", "새솔교육지원청", "국립누리박물관", "하늘도청", "한국가람공사",
                  "가람소방서"],
        "regions": ["세종시 정부청사", "서울 중구", "대전 서구 둔산동", "경기 수원시 팔달구", "전남 나주시 혁신도시", "경북 김천시 혁신도시"],
        "street": "청사로",
        "deals": [("나라장터 정보시스템 구축", ["PB-SI"]), ("방범 CCTV 확충", ["PB-CCTV"]), ("PC 일괄 교체", ["PB-PC"]),
                  ("유지관리 연간 계약", ["PB-MAINT"])],
        "activities": ["사전 규격 공개 의견 제출", "제안 발표", "기술 협상", "검수 입회", "감사 자료 제출", "예산 편성 일정 확인"],
        "next": ["제안서 제출", "검수 서류 준비", "착수계 제출"],
        "contacts": [("구매", "계약팀", "주무관"), ("회계·세금계산서", "재무과", "주무관"), ("현업", "정보통신과", "팀장"),
                     ("의사결정", "총무국", "국장")],
        "grades": {"VIP": 1_000_000_000, "A": 400_000_000, "B": 150_000_000, "C": 50_000_000},
        "terms": [30, 45], "pay": (0.95, 1.0, (10, 50)), "returns": 0.0, "return_reason": "",
        "competitors": ["조달 우수 업체", "지역 업체"],
    },
}
INDUSTRY_KEYS = list(PRESETS)

PERSON = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임", "한", "오"]
GIVEN = ["민수", "지훈", "서연", "현우", "수진", "도윤", "예린", "성호", "은지", "태영", "하늘", "준호", "다은", "승민"]


def _biz(rng: random.Random) -> str:
    from .documents import valid_biz_no
    while True:
        head = f"{rng.randint(101, 899)}{rng.randint(81, 88)}{rng.randint(1000, 9999)}"
        for c in range(10):
            if valid_biz_no(head + str(c)):
                return f"{head[:3]}-{head[3:5]}-{head[5:]}{c}"


def _safe_day(day: date) -> Optional[str]:
    from . import periods
    iso = day.isoformat()
    return None if periods.date_problem(iso) else iso


def periods_blocked(day: str) -> bool:
    from . import periods
    return bool(periods.date_problem(day))


def seed(industry: str = "제조", customers: int = 20, months: int = 12, rnd_seed: Optional[int] = None) -> dict:
    """industry 업종의 샘플 거래처 customers 곳과 지난 months 개월 거래를 만든다."""
    from . import catalog, contacts, orders, returns
    from . import enterprise as ent
    from . import quotes as qt
    if industry not in PRESETS:
        raise ValueError(f"알 수 없는 업종입니다: {industry} (가능: {', '.join(INDUSTRY_KEYS)})")
    P = PRESETS[industry]
    rng = random.Random(rnd_seed)
    today = date.today()
    db.set_context("system", None)
    reps = [u for u in (ent.get_user(name=n) for n in ("김영업", "이수주", "박고객", "최성과")) if u]
    if not reps:
        raise ValueError("담당자 계정이 없습니다. '샘플 조직·계정 생성'을 먼저 실행하세요.")
    out = {"products": 0, "customers": 0, "contacts": 0, "deals": 0, "quotes": 0, "orders": 0, "sales": 0,
           "payments": 0, "returns": 0, "activities": 0, "skipped_closed": 0}

    if industry not in db.INDUSTRIES:
        from . import company
        company.save({"industries": [*db.INDUSTRIES, industry]}, "샘플")
    pids, units, prices = {}, {}, {}
    for code, name, spec, cat, unit, price, tax in P["products"]:
        row = db._one("SELECT id FROM products WHERE code=?", [code])
        if not row:
            catalog.upsert_product({"code": code, "name": name, "spec": spec, "category": cat, "unit": unit,
                                    "list_price": price, "tax_type": tax, "erp_material": code})
            out["products"] += 1
            row = db._one("SELECT id FROM products WHERE code=?", [code])
        pids[code], units[code], prices[code] = int(row["id"]), unit, price

    pay_rate, full_rate, (late_lo, late_hi) = P["pay"]

    def pay(sid: int, day: str) -> None:
        """결제기일이 지난 매출은 업종 습관대로 입금 (전액·부분·미입금)."""
        sale = db.get_sale(sid)
        total, due = int(sale["total_amount"]), sale["due_date"]
        pay_day = min(date.fromisoformat(due) + timedelta(days=rng.randint(late_lo, late_hi)), today).isoformat()
        old = (today - date.fromisoformat(due)).days > 60          # 기일이 두 달 넘게 지난 건은 대부분 회수됨
        rate, full = (max(pay_rate, 0.95), max(full_rate, 0.95)) if old else (pay_rate, full_rate)
        if due < today.isoformat() and rng.random() < rate and not periods_blocked(pay_day):
            amount = total if rng.random() < full else int(round(total * rng.uniform(0.3, 0.8), -3))
            ent.record_payment(sid, amount, pay_date=max(pay_day, day),
                               method=rng.choice(["계좌이체", "계좌이체", "어음" if industry in ("제조", "건설") else "계좌이체"]))
            out["payments"] += 1
    names = P["names"]
    start_no = int(db._scalar("SELECT COUNT(*) FROM customers WHERE memo=?", [P["memo"]]))
    for n in range(customers):
        rep = rng.choice(reps)
        ent.apply_context(ent.get_user(user_id=rep["id"]))
        base = names[(start_no + n) % len(names)]
        suffix = (start_no + n) // len(names)
        name = f"{P['corp']}{base}" + (f" 제{suffix + 1}{P['branch']}" if suffix else "")
        grade = rng.choices(list(P["grades"]), [1, 3, 4, 2])[0]
        try:
            cid = db.upsert_customer({"name": name, "biz_no": _biz(rng), "industry": industry, "grade": grade,
                                      "owner_id": rep["id"], "credit_limit": P["grades"][grade],
                                      "payment_terms": rng.choice(P["terms"]),
                                      "address": f"{rng.choice(P['regions'])} {P['street']} {rng.randint(10, 400)}",
                                      "erp_code": f"{P['erp']}{rng.randint(100000, 999999)}", "status": "활성",
                                      "memo": P["memo"]})
        except ValueError:
            continue
        out["customers"] += 1
        tag = f"{P['erp'].lower()}{start_no + n + 1:03d}"
        for i, (role, dept, title) in enumerate(rng.sample(P["contacts"], rng.randint(2, min(4, len(P["contacts"]))))):
            contacts.save(cid, {"name": rng.choice(PERSON) + rng.choice(GIVEN), "dept": dept, "title": title, "role": role,
                                "phone": f"010-{rng.randint(2000, 9999)}-{rng.randint(1000, 9999)}",
                                "email": f"contact{i + 1}@{tag}.example.kr", "is_primary": i == 0})
            out["contacts"] += 1

        # 매달 반복 구매
        regular = rng.sample(P["regular"], min(3, len(P["regular"])))
        for m in range(months, -1, -1):                       # m=0: 이번 달(오늘까지)도 정기 구매가 있다
            if rng.random() < 0.25:
                continue
            first = (today.replace(day=1) - timedelta(days=30 * m)).replace(day=1)
            span = 25 if m else max(0, min(25, today.day - 1))
            day = _safe_day(first + timedelta(days=rng.randint(0, span)))
            if not day:
                out["skipped_closed"] += 1
                continue
            code = rng.choice(regular)
            price = catalog.price_for(cid, pids[code], day)
            unit_price = price["unit_price"]
            if units[code] in ("명",):                        # 구독: 사용자 수
                qty = rng.choice([20, 50, 80, 120, 200, 350])
            elif unit_price < 100_000:
                qty = rng.randint(10, 300)
            elif unit_price < 1_000_000:
                qty = rng.randint(2, 40)
            else:
                qty = rng.randint(1, 4)
            sid = db.upsert_sale({"customer_id": cid, "sale_date": day, "item": price["name"], "item_code": code,
                                  "product_id": pids[code], "qty": qty, "unit_price": unit_price,
                                  "tax_type": price["tax_type"], "owner_id": rep["id"], "memo": "정기 구매"})
            out["sales"] += 1
            pay(sid, day)
            if P["returns"] and rng.random() < P["returns"] and qty > 2 and _safe_day(today):
                returns.create(sid, "반품", P["return_reason"], qty=rng.randint(1, max(1, qty // 4)))
                out["returns"] += 1

        # 영업기회 · 견적 · 수주 · 분할 납품 (수주·실주는 지난 날짜로 마감)
        title, codes = rng.choice(P["deals"])
        stage = rng.choices([*db.OPEN_STAGES[2:5], db.STAGE_WON, db.STAGE_LOST], [3, 3, 2, 3, 1])[0]
        close = (today + timedelta(days=rng.randint(3, 90))).isoformat()
        did = db.upsert_deal({"customer_id": cid, "title": f"{title} ({base})", "stage": db.OPEN_STAGES[4] if stage in (db.STAGE_WON, db.STAGE_LOST) else stage,
                              "owner_id": rep["id"], "list_amount": 0, "amount": 0, "expected_close": close,
                              "source": rng.choice(db.LEAD_SOURCES), "competitor": rng.choice(["", *P["competitors"]]),
                              **{f: 1 for f in db.MEDDIC_FIELDS}}, force=True, force_reason=f"{P['label']} 샘플")
        out["deals"] += 1
        q_day = _safe_day(today - timedelta(days=rng.randint(10, 80) if stage in ("수주", "실주") else rng.randint(3, 30)))
        if q_day:
            items = []
            for c in codes:
                if units[c] in P["lot_units"]:
                    q = 1 if prices[c] >= 50_000_000 else rng.randint(1, 3)    # 대형 설비는 1대
                elif units[c] == "명":
                    q = rng.choice([50, 100, 300])
                else:
                    q = rng.randint(20, 200)
                items.append({"product_id": pids[c], "qty": q})
            qid = qt.save_quote({"customer_id": cid, "deal_id": did, "title": title, "issue_date": q_day}, items)
            out["quotes"] += 1
            if stage == "수주":
                order_day = date.fromisoformat(q_day) + timedelta(days=rng.randint(3, 10))
                with db.get_conn() as conn:          # 시연 데이터: 결재·발송 과정 없이 수락 상태로
                    conn.execute("UPDATE quotes SET status='수락', decided_at=? WHERE id=?",
                                 (f"{order_day.isoformat()} 10:00:00", qid))
                oid = orders.from_quote(qid, {"delivery_date": (order_day + timedelta(days=45)).isoformat(),
                                              "customer_po": f"PO-{rng.randint(10000, 99999)}"})
                with db.get_conn() as conn:          # 수주일 = 견적 수락일 (오늘이 아니라)
                    conn.execute("UPDATE sales_orders SET order_date=? WHERE id=?", (order_day.isoformat(), oid))
                    conn.execute("UPDATE deals SET stage='수주', probability=100, closed_at=?, stage_since=?, "
                                 "amount=(SELECT COALESCE(SUM(qty*unit_price),0) FROM sales_order_items WHERE order_id=?) "
                                 "WHERE id=?", (f"{order_day.isoformat()} 10:00:00", order_day.isoformat(), oid, did))
                out["orders"] += 1
                o = orders.get(oid)
                first_lot = {int(it["id"]): max(1, int(it["qty"]) // 2) for it in o["items"]}
                deliver_day = _safe_day(min(order_day + timedelta(days=rng.randint(7, 30)), today))   # 1차 납품은 지난 날짜
                if deliver_day:
                    for sid in orders.deliver(oid, first_lot, deliver_day):
                        out["sales"] += 1
                        pay(sid, deliver_day)
                    second = date.fromisoformat(deliver_day) + timedelta(days=rng.randint(20, 45))
                    rest = {int(it["id"]): int(it["remain"]) for it in orders.get(oid)["items"] if int(it["remain"]) > 0}
                    if rest and second < today and _safe_day(second) and rng.random() < 0.6:   # 2차 납품(잔량)
                        for sid in orders.deliver(oid, rest, second.isoformat()):
                            out["sales"] += 1
                            pay(sid, second.isoformat())
            elif stage == "실주":
                lost_day = date.fromisoformat(q_day) + timedelta(days=rng.randint(5, 20))
                with db.get_conn() as conn:
                    conn.execute("UPDATE quotes SET status='거절', decided_at=? WHERE id=?", (f"{lost_day.isoformat()} 10:00:00", qid))
                    conn.execute("UPDATE deals SET stage='실주', probability=0, closed_at=?, stage_since=?, lost_reason=? "
                                 "WHERE id=?", (f"{lost_day.isoformat()} 10:00:00", lost_day.isoformat(),
                                                rng.choice(db.LOST_REASONS), did))
        for _ in range(rng.randint(2, 5)):
            a_day = _safe_day(today - timedelta(days=rng.randint(0, 120)))
            if not a_day:
                continue
            db.add_activity({"customer_id": cid, "deal_id": did, "act_date": a_day, "act_type": rng.choice(db.ACT_TYPES),
                             "owner_id": rep["id"], "summary": rng.choice(P["activities"]),
                             "next_action": rng.choice([*P["next"], ""]) or None,
                             "next_date": (today + timedelta(days=rng.randint(1, 14))).isoformat()})
            out["activities"] += 1
    backdate_customers(rnd_seed)
    db.set_context("system", None)
    db.audit("샘플데이터", "시스템", None, {"종류": P["label"], **out})
    return out


def realign_targets(rnd_seed: Optional[int] = None) -> int:
    """시연용: 담당자별 월 목표를 그 담당자의 최근 평균 매출에 맞춘다(±15%). 업종 샘플의 대형 수주까지 포함해
    달성률이 수백 %로 튀지 않게. 바꾼 행 수."""
    rng = random.Random(rnd_seed)
    this = date.today().replace(day=1)
    avg = db._df("SELECT owner_id, SUM(amount) / 12.0 AS avg FROM sales WHERE status <> '취소' AND owner_id IS NOT NULL "
                 "AND sale_date >= ? AND sale_date < ? GROUP BY owner_id",
                 [(this - timedelta(days=365)).isoformat(), this.isoformat()])
    months = sorted({r for (r,) in db._df("SELECT DISTINCT yyyymm FROM targets").itertuples(index=False)})
    n = 0
    for owner_id, base in avg.itertuples(index=False):
        for ym in months:
            db.upsert_target(ym, int(owner_id), int(round(float(base) * rng.uniform(0.85, 1.15), -6)))
            n += 1
    return n


def backdate_customers(rnd_seed: Optional[int] = None) -> int:
    """샘플 거래처 등록일을 첫 거래 10~60일 전으로 옮긴다 — 모두 '이번 달 신규 거래처'로 잡히지 않게. 바꾼 수.

    먼저 영업기회 생성일도 종료일·단계 진입일보다 앞으로 옮긴다 (오늘 만든 샘플이 지난 날짜에 끝난 것으로 나오면
    영업 주기가 음수가 되고 데이터 점검에 '종료일이 생성일보다 빠름'으로 잡힌다)."""
    rng = random.Random(rnd_seed)
    c, s = "COALESCE(closed_at, '9999')", "COALESCE(substr(stage_since, 1, 10), '9999')"
    first = f"CASE WHEN {c} < {s} THEN {c} ELSE {s} END"         # 둘 중 이른 날 (SQLite·PostgreSQL 공통)
    deals = db._df(f"SELECT id, {first} AS first FROM deals WHERE {first} < substr(created_at, 1, 10)")
    with db.get_conn() as conn:
        for did, first in deals.itertuples(index=False):
            made = date.fromisoformat(str(first)[:10]) - timedelta(days=rng.randint(20, 90))
            conn.execute("UPDATE deals SET created_at=? WHERE id=?", (f"{made.isoformat()} 09:00:00", int(did)))
    rows = db._df("SELECT c.id, MIN(x.d) AS first FROM customers c JOIN ("
                  "  SELECT customer_id, sale_date AS d FROM sales"
                  "  UNION ALL SELECT customer_id, substr(created_at, 1, 10) FROM deals) x ON x.customer_id=c.id "
                  "GROUP BY c.id HAVING MIN(x.d) < substr(c.created_at, 1, 10)")
    with db.get_conn() as conn:
        for cid, first in rows.itertuples(index=False):
            reg = date.fromisoformat(str(first)[:10]) - timedelta(days=rng.randint(10, 60))
            conn.execute("UPDATE customers SET created_at=? WHERE id=?", (f"{reg.isoformat()} 09:00:00", int(cid)))
    return len(rows)


def seed_many(industries: list[str] | None = None, customers: int = 8, months: int = 12,
              rnd_seed: Optional[int] = None) -> dict:
    """여러 업종을 한 번에 — 업종마다 customers 곳. 결과는 업종별 건수."""
    result = {}
    for i, key in enumerate(industries or INDUSTRY_KEYS):
        result[key] = seed(key, customers=customers, months=months,
                           rnd_seed=None if rnd_seed is None else rnd_seed + i)
    return result
