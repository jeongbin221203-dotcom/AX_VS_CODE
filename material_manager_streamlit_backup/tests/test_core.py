"""업무 로직 단위 테스트.

core 계층은 Streamlit에 의존하지 않으므로 Streamlit 설치 없이 실행된다.

    python tests/test_core.py        # 단독 실행
    pytest tests/test_core.py        # pytest 사용 시
"""

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# config 임포트 전에 테스트 전용 DB 경로를 지정한다.
_TMP = Path(tempfile.mkdtemp(prefix="mm_test_")) / "test.db"
os.environ["MM_DB_PATH"] = str(_TMP)

import pandas as pd  # noqa: E402

import config  # noqa: E402
from core import db, repository as repo, seed, services  # noqa: E402

TODAY = date.today().isoformat()


def setup_db() -> None:
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
    db.init_db()


def mid_of(code: str) -> int:
    with db.get_conn() as conn:
        return conn.execute("SELECT id FROM materials WHERE code = ?", (code,)).fetchone()["id"]


def test_stock_calculation() -> None:
    setup_db()
    seed.seed()
    expected = {"PKG-001": 35, "PKG-002": 18, "PKG-003": 170,
                "LSH-001": 50, "LSH-002": 30, "LBL-001": 1100, "SFT-001": 87}
    got = dict(zip(repo.stock_df()["code"], repo.stock_df()["stock"]))
    assert got == {k: float(v) for k, v in expected.items()}, got


def test_outbound_blocked_over_stock() -> None:
    setup_db()
    seed.seed()
    mid = mid_of("PKG-001")
    r = services.register_transaction(mid, "OUT", 999, TODAY, 18000)
    assert not r.ok and "재고 부족" in r.message, r.message
    ok = services.register_transaction(mid, "OUT", 5, TODAY, 18000)
    assert ok.ok and ok.stock_after == 30, ok


def test_adjustment_records_difference() -> None:
    setup_db()
    seed.seed()
    mid = mid_of("PKG-001")
    r = services.register_transaction(mid, "ADJ", 30, TODAY, 18000)
    assert r.ok and r.qty == -5 and r.stock_after == 30, r
    same = services.register_transaction(mid, "ADJ", 30, TODAY, 18000)
    assert not same.ok, "동일 수량 조정은 거부되어야 한다"


def test_safety_stock_warning() -> None:
    setup_db()
    seed.seed()
    mid = mid_of("LSH-001")  # 재고 50, 안전재고 40
    r = services.register_transaction(mid, "OUT", 15, TODAY, 15000)
    assert r.ok and r.stock_after == 35 and r.warning, r


def test_delete_blocked_when_negative() -> None:
    setup_db()
    seed.seed()
    mid = mid_of("PKG-001")
    with db.get_conn() as conn:
        in_id = conn.execute(
            "SELECT id FROM transactions WHERE material_id = ? AND tx_type = 'IN'", (mid,)
        ).fetchone()["id"]
        out_id = conn.execute(
            "SELECT id FROM transactions WHERE material_id = ? AND tx_type = 'OUT'", (mid,)
        ).fetchone()["id"]
    assert not services.delete_transaction(in_id).ok, "입고 삭제 시 음수 재고 → 차단"
    assert services.delete_transaction(out_id).ok, "출고 삭제는 허용"
    assert not services.delete_transaction(out_id).ok, "이미 삭제된 건은 거부"


def test_upload_normalization() -> None:
    setup_db()
    raw = pd.DataFrame({
        "자재코드": [" pkg-001 ", "NEW-001", "new-001", None, "BAD-001", "  "],
        "자재명": ["팔레트", "신규A", "신규A(중복)", "코드없음", None, "공백코드"],
        "단위": [None, "BOX", "  ", None, None, None],
        "안전재고": ["10", None, "abc", 5, 5, 5],
        "단가": [1000, None, "2000", -3, 1, 1],
    })
    r = services.normalize_upload(raw)
    assert r.dropped == 3, r.dropped          # 코드 없음 / 자재명 없음 / 공백 코드
    assert r.duplicated == 1, r.duplicated    # NEW-001 중복
    assert list(r.df["code"]) == ["PKG-001", "NEW-001"], list(r.df["code"])
    assert list(r.df["unit"]) == ["EA", "EA"], list(r.df["unit"])
    assert (r.df["unit_price"] >= 0).all() and (r.df["safety_stock"] >= 0).all()

    repo.upsert_materials(list(r.df.itertuples(index=False, name=None)))
    saved = repo.list_materials()
    assert len(saved) == 2 and saved["code"].notna().all()


def test_upload_missing_required_column() -> None:
    r = services.normalize_upload(pd.DataFrame({"이름": ["A"]}))
    assert r.errors and "자재코드" in r.errors[0], r.errors


def test_excel_export() -> None:
    setup_db()
    seed.seed()
    data = services.stock_display(repo.stock_df())
    blob = __import__("core.utils", fromlist=["to_excel_bytes"]).to_excel_bytes({"재고현황": data})
    assert blob[:2] == b"PK" and len(blob) > 3000


def test_empty_db_paths() -> None:
    setup_db()
    df = repo.stock_df()
    assert df.empty and "shortage" in df.columns
    assert services.material_options() == {}
    assert repo.count_materials() == 0


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
