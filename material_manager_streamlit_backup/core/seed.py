"""데모용 샘플 데이터 (포워딩 창고 포장·고박 자재)."""

from datetime import date, timedelta

from core import db, repository as repo
from core.utils import now_str

MATERIALS = [
    ("PKG-001", "수출용 목재 팔레트", "1100x1100 훈증", "EA", "포장재", 50, 18000, "A-01", "대한팔레트"),
    ("PKG-002", "스트레치 필름", "500mm x 300m", "ROLL", "포장재", 30, 9500, "A-02", "한국필름"),
    ("PKG-003", "5겹 골판지 박스", "600x400x400", "EA", "포장재", 200, 1200, "A-03", "동양포장"),
    ("LSH-001", "라싱 벨트", "50mm 5T 9m", "EA", "고박자재", 40, 15000, "B-01", "세이프라싱"),
    ("LSH-002", "에어백(던니지백)", "900x1800", "EA", "고박자재", 60, 7000, "B-02", "세이프라싱"),
    ("LBL-001", "취급주의 라벨", "100x150", "EA", "라벨/소모품", 500, 80, "C-01", "라벨코리아"),
    ("SFT-001", "안전장갑", "코팅 L", "PAIR", "안전용품", 100, 1500, "C-02", "안전나라"),
]

MOVEMENTS = [
    ("PKG-001", "IN", 120, 25), ("PKG-001", "OUT", 45, 18), ("PKG-001", "OUT", 40, 6),
    ("PKG-002", "IN", 40, 20), ("PKG-002", "OUT", 22, 4),
    ("PKG-003", "IN", 500, 28), ("PKG-003", "OUT", 180, 15), ("PKG-003", "OUT", 150, 3),
    ("LSH-001", "IN", 80, 22), ("LSH-001", "OUT", 30, 10),
    ("LSH-002", "IN", 100, 19), ("LSH-002", "OUT", 70, 2),
    ("LBL-001", "IN", 2000, 27), ("LBL-001", "OUT", 900, 12),
    ("SFT-001", "IN", 150, 24), ("SFT-001", "OUT", 60, 8), ("SFT-001", "ADJ", -3, 1),
]


def seed() -> None:
    ts = now_str()
    with db.transaction() as conn:
        for m in MATERIALS:
            conn.execute(
                """
                INSERT OR IGNORE INTO materials
                    (code, name, spec, unit, category, safety_stock, unit_price,
                     location, supplier, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (*m, ts, ts),
            )
        ids = {r["code"]: (r["id"], r["unit_price"])
               for r in conn.execute("SELECT id, code, unit_price FROM materials")}
        for code, tx_type, qty, days_ago in MOVEMENTS:
            mid, price = ids[code]
            repo.insert_transaction(conn, {
                "material_id": mid, "tx_type": tx_type, "qty": qty, "unit_price": price,
                "tx_date": (date.today() - timedelta(days=days_ago)).isoformat(),
                "ref_no": f"SAMPLE-{code}", "partner": "샘플거래처",
                "note": "샘플 데이터", "created_by": "system",
            })
