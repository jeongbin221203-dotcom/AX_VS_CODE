"""데이터 접근 계층 (자재 · 재고 · 거래 · 증빙). 화면 계층은 SQL을 직접 쓰지 않는다.

wh_ids 인자: None이면 모든 창고, 집합이면 그 창고만(사용자 데이터 범위). 빈 집합이면 아무것도 보이지 않는다.
"""

from datetime import date, timedelta
from typing import Iterable, Sequence

import pandas as pd

from core import db
from core.db import Conn
from core.utils import month_end, now_str

MATERIAL_FIELDS = ("code", "name", "spec", "unit", "category", "safety_stock", "unit_price",
                   "location", "supplier", "sap_matnr", "lot_managed", "expiry_managed", "barcode",
                   "lead_time_days", "min_order_qty", "order_multiple")
_EDITABLE = MATERIAL_FIELDS[1:]          # 자재코드는 바꾸지 않는다
# 엑셀 일괄 업로드로 바꿀 수 있는 항목 (로트 설정 제외)
UPLOAD_FIELDS = MATERIAL_FIELDS[:10] + ("barcode", "lead_time_days", "min_order_qty", "order_multiple")
MATERIAL_DEFAULTS = {"spec": "", "unit": "EA", "category": "미분류", "safety_stock": 0, "unit_price": 0,
                     "location": "", "supplier": "", "sap_matnr": "", "lot_managed": 0, "expiry_managed": 0,
                     "barcode": "", "lead_time_days": 0, "min_order_qty": 0, "order_multiple": 0}

# 거래 한 행이 재고에 주는 증감
EFFECT = "CASE WHEN {t}.tx_type = 'IN' THEN {t}.qty WHEN {t}.tx_type = 'OUT' THEN -{t}.qty ELSE {t}.qty END"


def _run(conn: Conn | None, sql: str, params: Sequence) -> int:
    """conn이 있으면 그 트랜잭션 안에서, 없으면 단독 트랜잭션으로 실행한다."""
    if conn is None:
        return db.execute(sql, params)
    return conn.execute(sql, params).lastrowid


def paged(sql: str, params: Sequence, page: int, size: int) -> tuple[pd.DataFrame, int]:
    """(해당 페이지 행, 전체 건수). sql에는 ORDER BY까지 넣는다."""
    with db.get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM ({sql}) x", params).fetchone()[0]
        df = db.frame(conn, f"{sql} LIMIT ? OFFSET ?", [*params, size, max(page - 1, 0) * size])
    return df, int(total)


# ── 자재 마스터 ──────────────────────────────────────────────
def insert_material(data: dict, conn: Conn | None = None) -> int:
    ts = now_str()
    data = {**MATERIAL_DEFAULTS, **data}
    return _run(conn, f"""
        INSERT INTO materials ({", ".join(MATERIAL_FIELDS)}, active, created_at, updated_at)
        VALUES ({", ".join("?" * len(MATERIAL_FIELDS))}, 1, ?, ?)
        """, (*[data.get(f, "") for f in MATERIAL_FIELDS], ts, ts))


def update_material(material_id: int, data: dict, conn: Conn | None = None) -> None:
    data = {"lot_managed": 0, "expiry_managed": 0, "barcode": "", "lead_time_days": 0, "min_order_qty": 0,
            "order_multiple": 0, **data}
    _run(conn, f"""
        UPDATE materials SET {", ".join(f"{f} = ?" for f in _EDITABLE)}, updated_at = ?
        WHERE id = ?
        """, (*[data.get(f, "") for f in _EDITABLE], now_str(), material_id))


def set_material_active(material_id: int, active: bool, conn: Conn | None = None) -> None:
    _run(conn, "UPDATE materials SET active = ?, updated_at = ? WHERE id = ?",
         (1 if active else 0, now_str(), material_id))


def upsert_materials(rows: Sequence[Sequence], conn: Conn | None = None) -> None:
    """자재코드 기준 Upsert. 행은 UPLOAD_FIELDS 순서. 재고·로트 설정은 건드리지 않는다."""
    ts = now_str()
    editable = UPLOAD_FIELDS[1:]
    sql = f"""
        INSERT INTO materials ({", ".join(UPLOAD_FIELDS)}, active, created_at, updated_at)
        VALUES ({", ".join("?" * len(UPLOAD_FIELDS))}, 1, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            {", ".join(f"{f} = excluded.{f}" for f in editable)}, updated_at = excluded.updated_at
    """
    params = [(*r, ts, ts) for r in rows]
    if conn is None:
        db.executemany(sql, params)
    else:
        conn.executemany(sql, params)


def get_material(material_id: int, conn: Conn | None = None) -> dict | None:
    if conn is not None:
        row = conn.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
        return dict(row) if row else None
    df = db.query_df("SELECT * FROM materials WHERE id = ?", (material_id,))
    return None if df.empty else df.iloc[0].to_dict()


def codes_existing(codes: Sequence[str]) -> set[str]:
    if not codes:
        return set()
    df = db.query_df(f"SELECT code FROM materials WHERE code IN ({','.join('?' * len(codes))})", codes)
    return set(df["code"])


def list_materials(active_only: bool = True) -> pd.DataFrame:
    where = "WHERE active = 1" if active_only else ""
    return db.query_df(f"SELECT * FROM materials {where} ORDER BY code")


def count_materials() -> int:
    return int(db.scalar("SELECT COUNT(*) FROM materials"))


# ── 마감 · 재고 ─────────────────────────────────────────────
# 현재고 = 마지막 마감월 스냅샷 + 마감일 이후 거래 증감 (자재 × 창고).
def closed_through(conn: Conn) -> str:
    row = conn.execute("SELECT closed_through FROM period_closes ORDER BY id DESC LIMIT 1").fetchone()
    return row["closed_through"] if row else ""


def _wh(col: str, wh_ids) -> tuple[str, list]:
    frag, params = db.in_clause(wh_ids)
    return (f" AND {col}{frag}" if frag else ""), params


def current_stock(conn: Conn, material_id: int, warehouse_id: int | None = None, lot_no: str | None = None) -> float:
    """현재고. 창고·로트를 주면 그 단위, 안 주면 합계."""
    ym = closed_through(conn)
    wsql, wp = _wh("warehouse_id", None if warehouse_id is None else [warehouse_id])
    lsql, lp = (" AND lot_no = ?", [lot_no]) if lot_no is not None else ("", [])
    snap = conn.execute(f"SELECT COALESCE(SUM(qty), 0) FROM inventory_snapshots WHERE ym = ? AND material_id = ?{wsql}{lsql}",
                        (ym, material_id, *wp, *lp)).fetchone()[0]
    twsql, twp = _wh("t.warehouse_id", None if warehouse_id is None else [warehouse_id])
    tlsql = " AND t.lot_no = ?" if lot_no is not None else ""
    delta = conn.execute(f"SELECT {db.STOCK_EXPR} FROM transactions t WHERE t.material_id = ? AND t.tx_date > ?{twsql}{tlsql}",
                         (material_id, month_end(ym), *twp, *lp)).fetchone()[0]
    return float(snap) + float(delta)


def balance_window(conn: Conn, material_id: int, warehouse_id: int, lot_no: str | None,
                   tx_date: str) -> tuple[float, float]:
    """(tx_date 말 재고, tx_date 이후 어느 날이든 가장 낮았던 일말 재고).

    과거 일자로 출고·이동·조정을 넣으면 그날부터 오늘까지의 모든 일말 재고가 함께 줄어든다.
    그래서 출고 가능량은 '오늘 재고'가 아니라 이 최저값이다."""
    now = current_stock(conn, material_id, warehouse_id, lot_no)
    lsql, lp = (" AND t.lot_no = ?", [lot_no]) if lot_no is not None else ("", [])
    rows = conn.execute(
        f"SELECT t.tx_date, {db.STOCK_EXPR} AS net FROM transactions t "
        f"WHERE t.material_id = ? AND t.warehouse_id = ? AND t.tx_date > ?{lsql} "
        "GROUP BY t.tx_date ORDER BY t.tx_date DESC", (material_id, warehouse_id, tx_date, *lp)).fetchall()
    bal = low = now
    for r in rows:                                    # 오늘부터 거꾸로: 그 날짜 거래를 빼면 전날 말 재고
        bal -= float(r["net"])
        low = min(low, bal)
    return bal, low


def _base_snapshot(conn: Conn, as_of: str) -> str:
    """as_of(포함) 이전에 끝난 마감월 중 가장 최근 달. 없으면 ''."""
    ym = closed_through(conn)
    if not ym:
        return ""
    rows = conn.execute("SELECT DISTINCT ym FROM inventory_snapshots WHERE ym <= ? ORDER BY ym DESC", (ym,)).fetchall()
    for r in rows:
        if month_end(r["ym"]) <= as_of:
            return r["ym"]
    return ""


def stock_as_of(conn: Conn, as_of: str, wh_ids=None) -> dict[tuple[int, int, str], float]:
    """as_of(포함)까지의 (자재, 창고, 로트)별 재고. 마감 스냅샷이 있으면 거기서부터 더한다."""
    base = _base_snapshot(conn, as_of)
    swsql, swp = _wh("warehouse_id", wh_ids)
    twsql, twp = _wh("t.warehouse_id", wh_ids)
    rows = conn.execute(f"""
        SELECT material_id, warehouse_id, lot_no, SUM(q) AS qty FROM (
            SELECT material_id, warehouse_id, lot_no, qty AS q FROM inventory_snapshots WHERE ym = ?{swsql}
            UNION ALL
            SELECT t.material_id, t.warehouse_id, t.lot_no, {EFFECT.format(t='t')} AS q FROM transactions t
            WHERE t.tx_date > ? AND t.tx_date <= ?{twsql}
        ) k GROUP BY material_id, warehouse_id, lot_no
        """, (base, *swp, month_end(base), as_of, *twp)).fetchall()
    return {(int(r["material_id"]), int(r["warehouse_id"]), r["lot_no"] or ""): float(r["qty"]) for r in rows}


def stock_by_lot(wh_ids=None, material_id: int | None = None, warehouse_id: int | None = None,
                 only_positive: bool = True) -> pd.DataFrame:
    """로트별 현재고와 유효기한 (로트 관리 자재만)."""
    with db.get_conn() as conn:
        ym = closed_through(conn)
        swsql, swp = _wh("warehouse_id", wh_ids)
        twsql, twp = _wh("t.warehouse_id", wh_ids)
        extra, ep = "", []
        if material_id is not None:
            extra += " AND k.material_id = ?"
            ep.append(material_id)
        if warehouse_id is not None:
            extra += " AND k.warehouse_id = ?"
            ep.append(warehouse_id)
        df = db.frame(conn, f"""
            SELECT m.id AS material_id, m.code, m.name, m.unit, w.id AS warehouse_id, w.code AS wh_code,
                   k.lot_no, COALESCE(l.expiry_date, '') AS expiry_date, k.stock
            FROM (SELECT material_id, warehouse_id, lot_no, SUM(q) AS stock FROM (
                    SELECT material_id, warehouse_id, lot_no, qty AS q FROM inventory_snapshots WHERE ym = ?{swsql}
                    UNION ALL
                    SELECT t.material_id, t.warehouse_id, t.lot_no, {EFFECT.format(t='t')} AS q FROM transactions t
                    WHERE t.tx_date > ?{twsql}
                  ) u GROUP BY material_id, warehouse_id, lot_no) k
            JOIN materials m ON m.id = k.material_id
            JOIN warehouses w ON w.id = k.warehouse_id
            LEFT JOIN lots l ON l.material_id = k.material_id AND l.lot_no = k.lot_no
            WHERE k.lot_no <> ''{extra}
            ORDER BY CASE WHEN COALESCE(l.expiry_date, '') = '' THEN 1 ELSE 0 END, l.expiry_date, k.lot_no
            """, (ym, *swp, month_end(ym), *twp, *ep))
    if only_positive:
        df = df[df["stock"] > 1e-9]
    today = date.today()
    df["days_left"] = [(date.fromisoformat(e) - today).days if e else None for e in df["expiry_date"]]
    return df.reset_index(drop=True)


def lot_balances(conn: Conn, material_id: int, warehouse_id: int) -> list[dict]:
    """한 창고의 로트별 잔량(0보다 큰 것만), 유효기한 빠른 순 (기한 없는 로트는 뒤)."""
    ym = closed_through(conn)
    rows = conn.execute(f"""
        SELECT k.lot_no, SUM(k.q) AS qty, COALESCE(MAX(l.expiry_date), '') AS expiry_date FROM (
            SELECT lot_no, qty AS q FROM inventory_snapshots WHERE ym = ? AND material_id = ? AND warehouse_id = ?
            UNION ALL
            SELECT t.lot_no, {EFFECT.format(t='t')} AS q FROM transactions t
            WHERE t.tx_date > ? AND t.material_id = ? AND t.warehouse_id = ?
        ) k LEFT JOIN lots l ON l.material_id = ? AND l.lot_no = k.lot_no
        WHERE k.lot_no <> ''
        GROUP BY k.lot_no
        """, (ym, material_id, warehouse_id, month_end(ym), material_id, warehouse_id, material_id)).fetchall()
    out = [{"lot_no": x["lot_no"], "qty": float(x["qty"]), "expiry_date": x["expiry_date"] or ""}
           for x in rows if float(x["qty"]) > 1e-9]
    return sorted(out, key=lambda b: (b["expiry_date"] == "", b["expiry_date"], b["lot_no"]))


def get_lot(conn: Conn, material_id: int, lot_no: str):
    return conn.execute("SELECT * FROM lots WHERE material_id = ? AND lot_no = ?", (material_id, lot_no)).fetchone()


def ensure_lot(conn: Conn, material_id: int, lot_no: str, expiry_date: str) -> str:
    """로트 마스터 등록(처음 입고) 또는 유효기한 확인. 문제 있으면 사유."""
    lot = get_lot(conn, material_id, lot_no)
    if lot is None:
        conn.execute("INSERT INTO lots (material_id, lot_no, expiry_date, created_at) VALUES (?, ?, ?, ?)",
                     (material_id, lot_no, expiry_date, now_str()))
        return ""
    if expiry_date and lot["expiry_date"] and lot["expiry_date"] != expiry_date:
        return f"로트 {lot_no}의 유효기한은 {lot['expiry_date']}로 등록되어 있습니다. 같은 로트에 다른 유효기한을 쓸 수 없습니다."
    return ""


def stock_by_wh(wh_ids=None, include_inactive: bool = False) -> pd.DataFrame:
    """(자재, 창고)별 현재고. 거래나 스냅샷이 있는 조합만."""
    with db.get_conn() as conn:
        ym = closed_through(conn)
        swsql, swp = _wh("warehouse_id", wh_ids)
        twsql, twp = _wh("t.warehouse_id", wh_ids)
        df = db.frame(conn, f"""
            SELECT m.id AS material_id, m.code, m.name, m.unit, m.category, m.unit_price, m.sap_matnr,
                   w.id AS warehouse_id, w.code AS wh_code, w.name AS wh_name, w.sap_sloc,
                   p.code AS plant_code, p.sap_plant, k.stock
            FROM (SELECT material_id, warehouse_id, SUM(q) AS stock FROM (
                    SELECT material_id, warehouse_id, qty AS q FROM inventory_snapshots WHERE ym = ?{swsql}
                    UNION ALL
                    SELECT t.material_id, t.warehouse_id, {EFFECT.format(t='t')} AS q FROM transactions t
                    WHERE t.tx_date > ?{twsql}
                  ) u GROUP BY material_id, warehouse_id) k
            JOIN materials m ON m.id = k.material_id
            JOIN warehouses w ON w.id = k.warehouse_id
            JOIN plants p ON p.id = w.plant_id
            {"" if include_inactive else "WHERE m.active = 1"}
            ORDER BY m.code, w.code
            """, (ym, *swp, month_end(ym), *twp))
    df["stock_value"] = df["stock"] * df["unit_price"]
    return df


def stock_df(include_inactive: bool = False, wh_ids=None) -> pd.DataFrame:
    """자재별 현재고 (wh_ids 창고 합계)."""
    where = "" if include_inactive else "WHERE m.active = 1"
    with db.get_conn() as conn:
        ym = closed_through(conn)
        swsql, swp = _wh("warehouse_id", wh_ids)
        twsql, twp = _wh("t.warehouse_id", wh_ids)
        df = db.frame(conn, f"""
            SELECT m.id, m.code, m.name, m.spec, m.unit, m.category,
                   m.safety_stock, m.unit_price, m.location, m.supplier, m.active, m.sap_matnr,
                   m.lot_managed, m.expiry_managed, m.sap_synced_at,
                   COALESCE(s.qty, 0) + COALESCE(d.delta, 0) AS stock
            FROM materials m
            LEFT JOIN (SELECT material_id, SUM(qty) AS qty FROM inventory_snapshots
                       WHERE ym = ?{swsql} GROUP BY material_id) s ON s.material_id = m.id
            LEFT JOIN (SELECT t.material_id, {db.STOCK_EXPR} AS delta FROM transactions t
                       WHERE t.tx_date > ?{twsql} GROUP BY t.material_id) d ON d.material_id = m.id
            {where}
            ORDER BY m.code
            """, (ym, *swp, month_end(ym), *twp))
    df["stock"] = df["stock"].astype(float)
    df["stock_value"] = df["stock"] * df["unit_price"]
    df["shortage"] = df["stock"] < df["safety_stock"]
    if wh_ids is not None and len(df):            # 일부 창고 권한: 그 창고에서 다룬 적 있는 자재만 미달로 센다 (다른 공장 자재 제외)
        twsql2, twp2 = _wh("warehouse_id", wh_ids)
        used = db.query_df(f"SELECT DISTINCT material_id FROM transactions WHERE 1 = 1{twsql2}", tuple(twp2))["material_id"]
        df["shortage"] = df["shortage"] & df["id"].isin(used.astype(int).tolist())
    df["shortage_qty"] = (df["safety_stock"] - df["stock"]).clip(lower=0).where(df["shortage"], 0)
    return df


def material_stock(material_id: int, wh_ids=None) -> float:
    """자재 하나의 현재고 (wh_ids 창고 합계)."""
    twsql, twp = _wh("t.warehouse_id", wh_ids)
    return float(db.scalar(f"SELECT COALESCE(SUM({EFFECT.format(t='t')}), 0) FROM transactions t "
                           f"WHERE t.material_id = ?{twsql}", (material_id, *twp)) or 0)


def shortage_count(wh_ids=None) -> int:
    """안전재고 미달 자재 수 (사이드바). stock_df 와 같은 기준이지만 숫자 하나만 센다."""
    df = stock_df(wh_ids=wh_ids)
    return int(df["shortage"].sum()) if not df.empty else 0


# ── 수불부 ──────────────────────────────────────────────────
def ledger_df(start: str, end: str, wh_ids=None) -> pd.DataFrame:
    """기간 수불부: 기초 + 입고 − 출고 ± 조정 + 이동입고 − 이동출고 = 기말 (자재별, 창고 합계).

    취소 거래는 수량 부호가 반대라 해당 항목에서 그대로 빠진다.
    """
    opening_day = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
    twsql, twp = _wh("t.warehouse_id", wh_ids)
    with db.get_conn() as conn:
        opening = stock_as_of(conn, opening_day, wh_ids)
        moves = db.frame(conn, f"""
            SELECT t.material_id,
                   SUM(CASE WHEN t.tx_type = 'IN'  AND t.transfer_no = '' THEN t.qty ELSE 0 END) AS in_qty,
                   SUM(CASE WHEN t.tx_type = 'OUT' AND t.transfer_no = '' THEN t.qty ELSE 0 END) AS out_qty,
                   SUM(CASE WHEN t.tx_type = 'ADJ' THEN t.qty ELSE 0 END) AS adj_qty,
                   SUM(CASE WHEN t.tx_type = 'IN'  AND t.transfer_no <> '' THEN t.qty ELSE 0 END) AS trf_in,
                   SUM(CASE WHEN t.tx_type = 'OUT' AND t.transfer_no <> '' THEN t.qty ELSE 0 END) AS trf_out
            FROM transactions t WHERE t.tx_date BETWEEN ? AND ?{twsql}
            GROUP BY t.material_id
            """, (start, end, *twp))
        mats = db.frame(conn, "SELECT id AS material_id, code, name, unit, unit_price, active FROM materials ORDER BY code")
    open_by_mat: dict[int, float] = {}
    for (mid, _wh_id, _lot), q in opening.items():
        open_by_mat[mid] = open_by_mat.get(mid, 0.0) + q
    df = mats.merge(moves, on="material_id", how="left").fillna(
        {"in_qty": 0, "out_qty": 0, "adj_qty": 0, "trf_in": 0, "trf_out": 0})
    df["opening"] = df["material_id"].map(open_by_mat).fillna(0.0)
    df["closing"] = df["opening"] + df["in_qty"] - df["out_qty"] + df["adj_qty"] + df["trf_in"] - df["trf_out"]
    moved = (df[["in_qty", "out_qty", "adj_qty", "trf_in", "trf_out"]].abs().sum(axis=1) > 0)
    df = df[(df["opening"] != 0) | moved | (df["closing"] != 0)].copy()
    df["closing_value"] = df["closing"] * df["unit_price"]
    return df.reset_index(drop=True)


# ── 거래 ────────────────────────────────────────────────────
TX_FIELDS = ("material_id", "tx_type", "qty", "unit_price", "tx_date", "ref_no", "partner", "note",
             "created_by", "reversal_of", "po_no", "po_item", "cost_center", "movement_type",
             "warehouse_id", "transfer_no", "created_by_id", "approved_by", "lot_no", "statement_id",
             "partner_id", "production_id", "batch_no", "entry_unit", "entry_qty")


def insert_transaction(conn: Conn, payload: dict) -> int:
    defaults = {"reversal_of": None, "po_no": "", "po_item": "", "cost_center": "", "movement_type": "",
                "warehouse_id": None, "transfer_no": "", "created_by_id": None, "approved_by": "", "lot_no": "",
                "statement_id": None, "partner_id": None, "production_id": None, "batch_no": "", "entry_unit": "",
                "entry_qty": None}
    data = {**defaults, **payload}
    if data["warehouse_id"] is None:
        data["warehouse_id"] = default_warehouse_id(conn)
    cur = conn.execute(
        f"""
        INSERT INTO transactions ({", ".join(TX_FIELDS)}, created_at)
        VALUES ({", ".join("?" * len(TX_FIELDS))}, ?)
        """,
        (*[data[f] for f in TX_FIELDS], now_str()),
    )
    return cur.lastrowid


def default_warehouse_id(conn: Conn) -> int:
    return int(conn.execute("SELECT id FROM warehouses WHERE active = 1 ORDER BY id LIMIT 1").fetchone()[0])


def get_transaction(conn: Conn, tx_id: int):
    return conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()


def reversal_id(conn: Conn, tx_id: int) -> int | None:
    row = conn.execute("SELECT id FROM transactions WHERE reversal_of = ?", (tx_id,)).fetchone()
    return None if row is None else int(row["id"])


def transfer_legs(conn: Conn, transfer_no: str) -> list:
    """창고 간 이동의 원거래 행들 (출고 먼저). 로트가 여러 개면 로트마다 출고·입고 한 쌍."""
    return conn.execute("SELECT * FROM transactions WHERE transfer_no = ? AND reversal_of IS NULL "
                        "ORDER BY CASE tx_type WHEN 'OUT' THEN 0 ELSE 1 END", (transfer_no,)).fetchall()


HISTORY_SELECT = """
    SELECT t.id, t.tx_date, t.tx_type, t.transfer_no, w.code AS wh_code, m.code, m.name, t.lot_no, m.unit,
           t.qty, t.unit_price, t.qty * t.unit_price AS amount,
           t.ref_no, t.partner, t.po_no, t.cost_center, t.movement_type,
           t.created_by, t.approved_by, t.note, t.created_at, t.reversal_of,
           (SELECT r.id FROM transactions r WHERE r.reversal_of = t.id) AS reversed_by,
           (SELECT COUNT(*) FROM documents d WHERE d.tx_id = t.id) AS doc_cnt,
           o.status AS sap_status, o.sap_doc_no
    FROM transactions t
    JOIN materials m ON m.id = t.material_id
    JOIN warehouses w ON w.id = t.warehouse_id
    LEFT JOIN sap_outbox o ON o.tx_id = t.id
"""


def _history_where(start, end, tx_types, material_ids, wh_ids, keyword: str = "") -> tuple[str, list]:
    sql = f" WHERE t.tx_date BETWEEN ? AND ? AND t.tx_type IN ({','.join('?' * len(tx_types))})"
    params = [start, end, *tx_types]
    if keyword.strip():
        # 문서번호·거래처·묶음·이동번호·로트·비고 (여러 줄 입출고 묶음 번호로 찾을 때 등)
        sql += (" AND (LOWER(t.ref_no) LIKE ? OR LOWER(t.partner) LIKE ? OR LOWER(t.batch_no) LIKE ?"
                " OR LOWER(t.transfer_no) LIKE ? OR LOWER(t.lot_no) LIKE ? OR LOWER(t.note) LIKE ?)")
        params += [f"%{keyword.strip().lower()}%"] * 6
    if material_ids:
        sql += f" AND t.material_id IN ({','.join('?' * len(material_ids))})"
        params += list(material_ids)
    wsql, wp = _wh("t.warehouse_id", wh_ids)
    return sql + wsql, params + wp


def history_page(start: str, end: str, tx_types: Sequence[str], material_ids: Sequence[int] = (),
                 wh_ids=None, page: int = 1, size: int = 100, keyword: str = "") -> tuple[pd.DataFrame, int, dict]:
    """(해당 페이지, 전체 건수, 합계{in_qty, out_qty})."""
    where, params = _history_where(start, end, tx_types, material_ids, wh_ids, keyword)
    df, total = paged(HISTORY_SELECT + where + " ORDER BY t.tx_date DESC, t.id DESC", params, page, size)
    with db.get_conn() as conn:
        row = conn.execute(f"""
            SELECT COALESCE(SUM(CASE WHEN t.tx_type = 'IN' THEN t.qty ELSE 0 END), 0),
                   COALESCE(SUM(CASE WHEN t.tx_type = 'OUT' THEN t.qty ELSE 0 END), 0)
            FROM transactions t {where}""", params).fetchone()
    return df, total, {"in_qty": float(row[0]), "out_qty": float(row[1])}


def history_df(start: str, end: str, tx_types: Sequence[str], material_ids: Sequence[int] = (),
               wh_ids=None, limit: int = 100_000, keyword: str = "") -> pd.DataFrame:
    """엑셀 내보내기용 전체 조회."""
    where, params = _history_where(start, end, tx_types, material_ids, wh_ids, keyword)
    return db.query_df(HISTORY_SELECT + where + " ORDER BY t.tx_date DESC, t.id DESC LIMIT ?", [*params, limit])


def monthly_tx_count(month_start: str, wh_ids=None) -> dict:
    """이번 달 건수. 취소 거래·취소된 원거래·창고 간 이동은 세지 않는다."""
    wsql, wp = _wh("t.warehouse_id", wh_ids)
    df = db.query_df(
        f"""
        SELECT tx_type, COUNT(*) AS cnt FROM transactions t
        WHERE tx_date >= ? AND reversal_of IS NULL AND transfer_no = ''{wsql}
          AND NOT EXISTS (SELECT 1 FROM transactions r WHERE r.reversal_of = t.id)
        GROUP BY tx_type
        """,
        (month_start, *wp),
    )
    return {k: int(v) for k, v in zip(df["tx_type"], df["cnt"])}


def trend_df(since: str, wh_ids=None) -> pd.DataFrame:
    wsql, wp = _wh("t.warehouse_id", wh_ids)
    return db.query_df(
        f"""
        SELECT tx_date, tx_type, SUM(qty) AS qty
        FROM transactions t
        WHERE tx_date >= ? AND tx_type IN ('IN', 'OUT') AND transfer_no = ''{wsql}
        GROUP BY tx_date, tx_type
        """,
        (since, *wp),
    )


# 전체 백업에서 빼는 표 (실행 중 상태·임시 기록) 와 열 (비밀값)
BACKUP_SKIP_TABLES = {"form_once", "job_locks", "sqlite_sequence", "schema_version", "app_settings"}
BACKUP_SKIP_COLUMNS = {"password_hash", "key_hash", "totp_secret", "recovery_codes"}
BACKUP_FIRST = ["materials", "plants", "warehouses", "partners", "transactions", "lots", "documents", "statements",
                "purchase_requests", "pr_items", "purchase_orders", "po_items", "boms", "bom_items", "productions",
                "production_lines", "approval_requests", "period_closes", "inventory_snapshots", "audit_log"]


def backup_tables() -> list[str]:
    with db.get_conn() as conn:
        if conn.pg:
            names = [r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables "
                                                "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'")]
        else:
            names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    names = [n for n in names if n not in BACKUP_SKIP_TABLES]
    return [n for n in BACKUP_FIRST if n in names] + sorted(n for n in names if n not in BACKUP_FIRST)


def dump_all() -> dict[str, pd.DataFrame]:
    """전체 엑셀 백업: 업무 표 전부 (비밀번호·API 키 해시 등 비밀값 열은 넣지 않는다)."""
    out = {}
    for table in backup_tables():
        with db.get_conn() as conn:
            cols = [c for c in db._columns(conn, table) if c not in BACKUP_SKIP_COLUMNS]
        order = " ORDER BY id" if "id" in cols else ""
        out[table] = db.query_df(f"SELECT {', '.join(sorted(cols))} FROM {table}{order}")   # 표 이름은 DB 목록에서만
    return out


# ── 증빙 ────────────────────────────────────────────────────
DOC_FIELDS = ("tx_id", "doc_type", "issue_date", "approval_no", "supplier_biz_no",
              "supplier_name", "supply_amount", "tax_amount", "file_name", "stored_name",
              "mime", "size", "sha256", "note", "created_by", "created_by_id")


def insert_document(conn: Conn, payload: dict) -> int:
    data = {"created_by_id": None, **payload}
    cur = conn.execute(
        f"""
        INSERT INTO documents ({", ".join(DOC_FIELDS)}, created_at)
        VALUES ({", ".join("?" * len(DOC_FIELDS))}, ?)
        """,
        (*[data[f] for f in DOC_FIELDS], now_str()),
    )
    return cur.lastrowid


def find_document_id(conn: Conn, sha256: str = "", approval_no: str = "") -> int | None:
    """같은 파일(해시) 또는 같은 승인번호로 이미 등록된 증빙 ID."""
    row = conn.execute(
        "SELECT id FROM documents WHERE sha256 = ? OR (approval_no <> '' AND approval_no = ?)",
        (sha256, approval_no),
    ).fetchone()
    return None if row is None else int(row["id"])


def get_document(doc_id: int) -> dict | None:
    df = db.query_df(
        """
        SELECT d.*, t.tx_date, t.tx_type, t.qty, t.warehouse_id, m.code, m.name, m.unit
        FROM documents d
        LEFT JOIN transactions t ON t.id = d.tx_id
        LEFT JOIN materials m ON m.id = t.material_id
        WHERE d.id = ?
        """,
        (doc_id,),
    )
    return None if df.empty else df.iloc[0].to_dict()


def doc_visible(doc: dict, wh_ids, user_id: int | None) -> bool:
    """거래에 연결된 증빙은 그 창고 권한이 있어야, 미연결 증빙은 전체 권한이거나 본인이 올린 것만."""
    if wh_ids is None:
        return True
    if pd.notna(doc.get("tx_id")):
        return pd.notna(doc.get("warehouse_id")) and int(doc["warehouse_id"]) in wh_ids
    return pd.notna(doc.get("created_by_id")) and int(doc["created_by_id"]) == user_id


def _documents_where(start, end, doc_types, keyword, tx_id, unlinked, wh_ids, user_id) -> tuple[str, list]:
    sql = " WHERE d.issue_date BETWEEN ? AND ?"
    params: list = [start, end]
    if doc_types:
        sql += f" AND d.doc_type IN ({','.join('?' * len(doc_types))})"
        params += list(doc_types)
    if keyword:
        sql += """ AND (LOWER(d.supplier_name) LIKE ? OR d.supplier_biz_no LIKE ? OR d.approval_no LIKE ?
                        OR LOWER(d.file_name) LIKE ? OR LOWER(d.note) LIKE ?)"""
        params += [f"%{keyword.lower()}%"] * 5
    if tx_id is not None:
        sql += " AND d.tx_id = ?"
        params.append(tx_id)
    if unlinked:
        sql += " AND d.tx_id IS NULL"
    if wh_ids is not None:
        frag, wp = db.in_clause(wh_ids)
        sql += f" AND ((d.tx_id IS NOT NULL AND t.warehouse_id{frag}) OR (d.tx_id IS NULL AND d.created_by_id = ?))"
        params += [*wp, user_id]
    return sql, params


DOCS_SELECT = """
    SELECT d.id, d.issue_date, d.doc_type, d.supplier_name, d.supplier_biz_no,
           d.supply_amount, d.tax_amount, d.supply_amount + d.tax_amount AS total_amount,
           d.approval_no, d.tx_id, m.code, m.name, d.file_name, d.created_by, d.created_at
    FROM documents d
    LEFT JOIN transactions t ON t.id = d.tx_id
    LEFT JOIN materials m ON m.id = t.material_id
"""


def documents_page(start: str, end: str, doc_types: Sequence[str] = (), keyword: str = "",
                   tx_id: int | None = None, unlinked: bool = False, wh_ids=None, user_id: int | None = None,
                   page: int = 1, size: int = 100) -> tuple[pd.DataFrame, int, dict]:
    where, params = _documents_where(start, end, doc_types, keyword, tx_id, unlinked, wh_ids, user_id)
    df, total = paged(DOCS_SELECT + where + " ORDER BY d.issue_date DESC, d.id DESC", params, page, size)
    with db.get_conn() as conn:
        row = conn.execute(f"""
            SELECT COALESCE(SUM(d.supply_amount), 0), COALESCE(SUM(d.tax_amount), 0),
                   COALESCE(SUM(CASE WHEN d.tx_id IS NULL THEN 1 ELSE 0 END), 0)
            FROM documents d LEFT JOIN transactions t ON t.id = d.tx_id {where}""", params).fetchone()
    return df, total, {"supply": int(row[0]), "tax": int(row[1]), "unlinked": int(row[2])}


def set_document_tx(conn: Conn, doc_id: int, tx_id: int | None) -> None:
    conn.execute("UPDATE documents SET tx_id = ? WHERE id = ?", (tx_id, doc_id))


def delete_document(conn: Conn, doc_id: int) -> None:
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


def recent_tx_options(limit: int = 200, wh_ids=None) -> list[tuple[int, str]]:
    """증빙을 연결할 거래 선택지 (최근 거래 우선, 권한 있는 창고만)."""
    wsql, wp = _wh("t.warehouse_id", wh_ids)
    df = db.query_df(
        f"""
        SELECT t.id, t.tx_date, t.tx_type, t.qty, t.partner, m.code, m.name, m.unit, w.code AS wh
        FROM transactions t JOIN materials m ON m.id = t.material_id JOIN warehouses w ON w.id = t.warehouse_id
        WHERE t.reversal_of IS NULL{wsql}
        ORDER BY t.tx_date DESC, t.id DESC LIMIT ?
        """,
        (*wp, limit),
    )
    return [(int(r.id), f"#{r.id} · {r.tx_date} {r.tx_type} {r.wh} [{r.code}] {r.name} {r.qty:,.2f}{r.unit}"
             + (f" · {r.partner}" if r.partner else "")) for r in df.itertuples()]


def ids_in(values: Iterable) -> list[int]:
    return [int(v) for v in values if str(v).isascii() and str(v).isdigit() and len(str(v)) <= 18]
