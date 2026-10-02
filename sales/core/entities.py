"""법인(사업장)과 외화 — 한 설치에서 여러 법인의 매출을 나눠 관리하고, 외화 거래를 원화로 환산한다.

법인
  법인마다 상호·사업자번호·대표·주소·ERP 회사코드·SAP 판매조직이 다르다. 영업기회·견적·매출에 법인을 붙이면
  견적서 공급자란, 세금계산서 공급자 확인, ERP 전송(회사코드·판매조직)이 그 법인 기준이 된다.
  법인을 하나도 등록하지 않으면 회사 설정의 회사 정보를 쓴다(기존과 같음).

외화
  견적·매출에 통화와 환율을 붙인다. 실적·채권·부가세는 원화(환율 적용 금액)로 집계하고, 외화 금액은 따로 보관해
  견적서·ERP 전송에 쓴다. 환율은 관리자가 넣거나 ERP 가 /api/v1/erp/fx 로 보낸다.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

import pandas as pd

from . import sales_db as db

CURRENCIES = ["KRW", "USD", "EUR", "JPY", "CNY", "GBP", "VND"]


# ---------------------------------------------------------------------------
# 법인
# ---------------------------------------------------------------------------
def list_entities(active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM entities" + (" WHERE active = 1" if active_only else "") + " ORDER BY id"
    return db._df(sql).to_dict("records")


def options() -> list[tuple[int, str]]:
    return [(int(e["id"]), f"{e['code']} · {e['name']}") for e in list_entities()]


def get(entity_id: Any) -> Optional[dict]:
    if not str(entity_id or "").isdigit():
        return None
    return db._one("SELECT * FROM entities WHERE id=?", [int(entity_id)])


def default_id() -> Optional[int]:
    rows = list_entities()
    return int(rows[0]["id"]) if rows else None


def resolve(entity_id: Any) -> Optional[int]:
    """폼 값 → 법인 id. 비우면 기본 법인(첫 번째), 법인이 없으면 None."""
    if str(entity_id or "").isdigit():
        row = get(entity_id)
        if not row or not row["active"]:
            raise ValueError("사용하지 않는 법인입니다.")
        return int(row["id"])
    return default_id()


def info(entity_id: Any) -> dict:
    """공급자 정보: 법인이 있으면 법인, 없으면 회사 설정."""
    from . import company
    row = get(entity_id)
    if row:
        return {"name": row["name"], "biz_no": row.get("biz_no") or "", "ceo": row.get("ceo") or "",
                "address": row.get("address") or "", "erp_company_code": row.get("erp_company_code"),
                "sap_sales_org": row.get("sap_sales_org"), "code": row["code"]}
    return {"name": company.get("company_name"), "biz_no": company.get("company_biz_no"),
            "ceo": company.get("company_ceo"), "address": company.get("company_address"),
            "erp_company_code": None, "sap_sales_org": None, "code": None}


def upsert(data: dict) -> int:
    from .documents import valid_biz_no
    code = str(data.get("code") or "").strip().upper()
    name = str(data.get("name") or "").strip()
    if not re.fullmatch(r"[A-Z0-9_\-]{1,20}", code):
        raise ValueError("법인 코드는 영문 대문자·숫자 20자 이내입니다 (예: KR01).")
    if not name:
        raise ValueError("법인명을 입력하세요.")
    biz = re.sub(r"\D", "", str(data.get("biz_no") or ""))
    if biz and not valid_biz_no(biz):
        raise ValueError("사업자번호가 올바르지 않습니다 (검증번호 불일치).")
    fields = {"code": code, "name": name, "biz_no": biz or None, "ceo": (data.get("ceo") or "").strip() or None,
              "address": (data.get("address") or "").strip() or None,
              "erp_company_code": (data.get("erp_company_code") or "").strip() or None,
              "sap_sales_org": (data.get("sap_sales_org") or "").strip() or None,
              "active": 1 if data.get("active", 1) in (1, "1", True, "on") else 0}
    dup = db._one("SELECT id FROM entities WHERE code=? AND id<>?", [code, int(data.get("id") or 0)])
    if dup:
        raise ValueError(f"법인 코드 {code} 가 이미 있습니다.")
    with db.get_conn() as conn:
        if data.get("id"):
            conn.execute(f"UPDATE entities SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                         (*fields.values(), int(data["id"])))
            eid = int(data["id"])
        else:
            cur = conn.execute(f"INSERT INTO entities ({', '.join(fields)}, created_at) VALUES "
                               f"({', '.join('?' * len(fields))}, ?)", (*fields.values(), db._now()))
            eid = int(cur.lastrowid)
    db.audit("수정" if data.get("id") else "등록", "시스템", eid, {"법인": f"{code} {name}"})
    return eid


# ---------------------------------------------------------------------------
# 환율
# ---------------------------------------------------------------------------
def set_rate(currency: str, rate_date: str, rate: Any, source: str = "수기") -> None:
    currency = str(currency or "").upper()
    if currency not in CURRENCIES or currency == "KRW":
        raise ValueError(f"통화는 {', '.join(c for c in CURRENCIES if c != 'KRW')} 중 하나입니다.")
    day = db._d(rate_date) or date.today().isoformat()
    try:
        value = float(str(rate).replace(",", ""))
    except ValueError as exc:
        raise ValueError("환율은 숫자여야 합니다 (1 외화당 원).") from exc
    if not 0 < value < 1_000_000:
        raise ValueError("환율 값이 범위를 벗어났습니다.")
    with db.get_conn() as conn:
        if conn.execute("UPDATE fx_rates SET rate=?, source=?, updated_at=? WHERE currency=? AND rate_date=?",
                        (value, source, db._now(), currency, day)).rowcount == 0:
            conn.execute("INSERT INTO fx_rates (currency, rate_date, rate, source, updated_at) VALUES (?,?,?,?,?)",
                         (currency, day, value, source, db._now()))
    db.audit("환율", "시스템", None, {"통화": currency, "일자": day, "환율": value, "출처": source})


def rate_on(currency: str, on: Optional[str] = None) -> Optional[float]:
    """그 날짜 이전 가장 최근 환율 (KRW 는 1)."""
    currency = str(currency or "KRW").upper()
    if currency == "KRW":
        return 1.0
    row = db._one("SELECT rate FROM fx_rates WHERE currency=? AND rate_date <= ? ORDER BY rate_date DESC LIMIT 1",
                  [currency, db._d(on) or date.today().isoformat()])
    return float(row["rate"]) if row else None


def latest_rates() -> pd.DataFrame:
    return db._df("SELECT f.currency AS 통화, f.rate_date AS 기준일, f.rate AS 환율, f.source AS 출처 FROM fx_rates f "
                  "WHERE f.rate_date = (SELECT MAX(rate_date) FROM fx_rates g WHERE g.currency = f.currency) "
                  "ORDER BY f.currency")


def to_krw(currency: str, foreign_amount: Any, rate: Any = None, on: Optional[str] = None) -> tuple[int, float]:
    """(원화 금액, 적용 환율). 원 미만 반올림."""
    currency = str(currency or "KRW").upper()
    if currency == "KRW":
        return int(round(float(foreign_amount or 0))), 1.0
    if currency not in CURRENCIES:
        raise ValueError(f"지원하지 않는 통화입니다: {currency}")
    value = float(rate) if rate not in (None, "") else rate_on(currency, on)
    if not value or value <= 0:
        raise ValueError(f"{currency} 환율이 없습니다. 회사 설정 > 환율에 넣거나 환율을 직접 입력하세요.")
    return int(round(float(foreign_amount or 0) * value)), value
