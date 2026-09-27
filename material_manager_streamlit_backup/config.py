"""애플리케이션 전역 설정.

DB 경로는 환경변수 MM_DB_PATH로 덮어쓸 수 있다.
(운영/테스트 분리, 컨테이너 볼륨 마운트 대응)
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("MM_DB_PATH", DATA_DIR / "materials.db"))

APP_TITLE = "자재관리 시스템"
APP_ICON = "📦"

# 거래 유형 코드 ↔ 화면 표기
TX_LABEL = {"IN": "입고", "OUT": "출고", "ADJ": "조정"}

# 자재 마스터 컬럼 코드 ↔ 엑셀 헤더
MATERIAL_COLS = {
    "code": "자재코드",
    "name": "자재명",
    "spec": "규격",
    "unit": "단위",
    "category": "분류",
    "safety_stock": "안전재고",
    "unit_price": "단가",
    "location": "보관위치",
    "supplier": "공급처",
}

DEFAULT_UNIT = "EA"
DEFAULT_CATEGORY = "미분류"
