"""데이터 접근 계층. SQL은 전부 이 파일에만 존재한다."""

import sqlite3
from typing import Sequence

import pandas as pd

from core import db
from core.utils import now_str

MATERIAL_FIELDS = ("code", "name", "spec", "unit", "category",
                   "safety_stock", "unit_price", "location", "supplier")


# ── 자재 마스터 ──────────────────────────────────────────────
def insert_material(data: dict) -> int:
    ts = now_str()
    return db.execute(
        """
        INSERT INTO materials
            (code, name, spec, unit, category, safety_stock, unit_price,
             location, supplier, active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (*[data[f] for f in MATERIAL_FIELDS], ts, ts),
    )


def update_material(material_id: int, data: dict) -> None:
    db.execute(
        """
        UPDATE materials
        SET name = ?, spec = ?, unit = ?, category = ?, safety_stock = ?,
            unit_price = ?, location = ?, supplier = ?, updated_at = ?
        WHERE id = ?
        """,
        (data["name"], data["spec"], data["unit"], data["category"],
         data["safety_stock"], data["unit_price"], data["location"],
         data["supplier"], now_str(), material_id),
    )


def set_material_active(material_id: int, active: bool) -> None:
    db.execute("UPDATE materials SET active = ?, updated_at = ? WHERE id = ?",
               (1 if active else 0, now_str(), material_id))


def upsert_materials(rows: Sequence[Sequence]) -> None:
    """자재코드 기준 Upsert. 재고는 건드리지 않는다(입출고로만 변동)."""
    ts = now_str()
    db.executemany(
        """
        INSERT INTO materials
            (code, name, spec, unit, category, safety_stock, unit_price,
             location, supplier, active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            name = excluded.name, spec = excluded.spec, unit = excluded.unit,
            category = excluded.category, safety_stock = excluded.safety_stock,
            unit_price = excluded.unit_price, location = excluded.location,
            supplier = excluded.supplier, updated_at = excluded.updated_at
        """,
        [(*r, ts, ts) for r in rows],
    )


def get_material(material_id: int) -> dict | None:
    df = db.query_df("SELECT * FROM materials WHERE id = ?", (material_id,))
    return None if df.empty else df.iloc[0].to_dict()


def list_materials(active_only: bool = True) -> pd.DataFrame:
    where = "WHERE active = 1" if active_only else ""
    return db.query_df(f"SELECT * FROM materials {where} ORDER BY code")


def count_materials() -> int:
    return int(db.query_df("SELECT COUNT(*) AS c FROM materials").iloc[0]["c"])


# ── 재고 ────────────────────────────────────────────────────
def current_stock(conn: sqlite3.Connection, material_id: int) -> float:
    row = conn.execute(
        f"SELECT {db.STOCK_EXPR} AS stock FROM transactions t WHERE t.material_id = ?",
        (material_id,),
    ).fetchone()
    return float(row["stock"])


def stock_df(include_inactive: bool = False) -> pd.DataFrame:
    where = "" if include_inactive else "WHERE m.active = 1"
    df = db.query_df(
        f"""
        SELECT m.id, m.code, m.name, m.spec, m.unit, m.category,
               m.safety_stock, m.unit_price, m.location, m.supplier, m.active,
               {db.STOCK_EXPR} AS stock
        FROM materials m
        LEFT JOIN transactions t ON t.material_id = m.id
        {where}
        GROUP BY m.id
        ORDER BY m.code
        """
    )
    df["stock_value"] = df["stock"] * df["unit_price"]
    df["shortage"] = df["stock"] < df["safety_stock"]
    df["shortage_qty"] = (df["safety_stock"] - df["stock"]).clip(lower=0)
    return df


# ── 거래 ────────────────────────────────────────────────────
def insert_transaction(conn: sqlite3.Connection, payload: dict) -> int:
    cur = conn.execute(
        """
        INSERT INTO transactions
            (material_id, tx_type, qty, unit_price, tx_date,
             ref_no, partner, note, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (payload["material_id"], payload["tx_type"], payload["qty"], payload["unit_price"],
         payload["tx_date"], payload["ref_no"], payload["partner"], payload["note"],
         payload["created_by"], now_str()),
    )
    return cur.lastrowid


def get_transaction(conn: sqlite3.Connection, tx_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, material_id, tx_type, qty FROM transactions WHERE id = ?", (tx_id,)
    ).fetchone()


def delete_transaction(conn: sqlite3.Connection, tx_id: int) -> None:
    conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))


def history_df(start: str, end: str, tx_types: Sequence[str],
               material_ids: Sequence[int] = ()) -> pd.DataFrame:
    sql = f"""
        SELECT t.id, t.tx_date, t.tx_type, m.code, m.name, m.unit,
               t.qty, t.unit_price, t.qty * t.unit_price AS amount,
               t.ref_no, t.partner, t.created_by, t.note, t.created_at
        FROM transactions t
        JOIN materials m ON m.id = t.material_id
        WHERE t.tx_date BETWEEN ? AND ?
          AND t.tx_type IN ({",".join("?" * len(tx_types))})
    """
    params = [start, end, *tx_types]
    if material_ids:
        sql += f" AND t.material_id IN ({','.join('?' * len(material_ids))})"
        params += list(material_ids)
    sql += " ORDER BY t.tx_date DESC, t.id DESC"
    return db.query_df(sql, params)


def monthly_tx_count(month_start: str) -> dict:
    df = db.query_df(
        "SELECT tx_type, COUNT(*) AS cnt FROM transactions WHERE tx_date >= ? GROUP BY tx_type",
        (month_start,),
    )
    return df.set_index("tx_type")["cnt"].to_dict()


def trend_df(since: str) -> pd.DataFrame:
    return db.query_df(
        """
        SELECT tx_date, tx_type, SUM(qty) AS qty
        FROM transactions
        WHERE tx_date >= ? AND tx_type IN ('IN', 'OUT')
        GROUP BY tx_date, tx_type
        """,
        (since,),
    )


def dump_all() -> dict[str, pd.DataFrame]:
    return {
        "materials": db.query_df("SELECT * FROM materials ORDER BY code"),
        "transactions": db.query_df("SELECT * FROM transactions ORDER BY tx_date, id"),
    }
