"""데이터 점검 — 쌓여 있는 데이터의 결함을 찾아 고칠 곳으로 안내한다 (관리자 > 🩺 데이터 점검).

새로 들어오는 데이터는 화면·업로드 검증이 막지만, 이관·예전 버전에서 들어온 데이터에는 결함이 남아 있을 수 있다.
항목마다 건수·예시(최대 SAMPLE 행)·고칠 화면 링크를 준다. 규칙이 분명한 두 가지만 자동 수정한다:
  owner_link    담당자 이름이 사용자 한 명과 정확히 같으면 연결 (동명이인은 그대로 — '담당자 이관'으로)
  closed_sync   종료 단계(수주·실주)인데 종료일이 없으면 단계 진입일로, 진행 단계인데 종료일이 있으면 비움
입금액(paid_amount)은 입금 내역(payments) 합계와 같아야 하는 불변식이라 여기서 직접 고치지 않는다 — 매출 화면에서 입금·반제로.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from . import enterprise as ent
from . import sales_db as db

SAMPLE = 50
SEVERITY = {"high": "높음", "mid": "보통", "low": "낮음"}


def _df(sql: str, params: tuple = ()) -> pd.DataFrame:
    return db._df(sql, params)


def _check(key: str, title: str, severity: str, why: str, df: pd.DataFrame, link: tuple | None = None,
           fix: str | None = None, count: int | None = None, fix_label: str = "") -> dict:
    return {"key": key, "title": title, "severity": severity, "severity_label": SEVERITY[severity], "why": why,
            "count": int(len(df) if count is None else count), "rows": df.head(SAMPLE), "link": link,
            "fix": fix, "fix_label": fix_label}


def duplicate_customers() -> dict:
    groups = db.duplicate_groups()
    rows = pd.DataFrame([{"기준": g["기준"], "거래처": " / ".join(f"#{c['id']} {c['name']}" for c in g["거래처"]),
                          "건수": len(g["거래처"])} for g in groups], columns=["기준", "거래처", "건수"])
    return _check("dup_customers", "중복 거래처", "high",
                  "사업자번호가 같거나 이름이 거의 같은 거래처 — 매출·채권이 나뉘어 집계됩니다. '중복 · 병합'에서 합치세요.",
                  rows, link=("crm.customers", {"tab": "merge"}, None))


def bad_biz_no() -> dict:
    df = _df("SELECT id, name AS 거래처, biz_no AS 사업자번호, owner AS 담당자 FROM customers "
             "WHERE merged_into IS NULL AND COALESCE(biz_no, '') <> '' ORDER BY id")
    if not df.empty:
        df = df[~df["사업자번호"].map(db.valid_biz_no)]
    return _check("bad_biz_no", "사업자번호 검증번호 오류", "high",
                  "10자리가 아니거나 마지막 검증번호가 맞지 않습니다 — 세금계산서 발행·ERP 거래처 연동에서 거부됩니다.",
                  df, link=("crm.customers", {"tab": "edit"}, "id"))


def won_without_sales() -> dict:
    df = _df(f"""SELECT d.id, c.name AS 거래처, d.title AS 기회명, d.amount AS 예상금액, d.closed_at AS 종료일, d.owner AS 담당자
                 FROM deals d LEFT JOIN customers c ON c.id = d.customer_id
                 WHERE d.stage = ? AND NOT EXISTS (SELECT 1 FROM sales s WHERE s.deal_id = d.id
                                                    AND COALESCE(s.status, '') <> '{db.SALE_CANCELLED}')
                 ORDER BY d.closed_at DESC, d.id DESC""", (db.STAGE_WON,))
    return _check("won_no_sales", "수주했는데 매출이 없음", "mid",
                  "수주 단계인데 연결된 매출이 없습니다 — 실적·채권에서 빠집니다. 매출을 등록하거나 단계를 바로잡으세요.",
                  df, link=("crm.deals", {"tab": "edit"}, "id"))


def date_inverted() -> dict:
    df = _df("""SELECT d.id, c.name AS 거래처, d.title AS 기회명, substr(d.created_at, 1, 10) AS 생성일,
                       d.closed_at AS 종료일, d.stage AS 단계
                FROM deals d LEFT JOIN customers c ON c.id = d.customer_id
                WHERE d.closed_at IS NOT NULL AND d.closed_at < substr(d.created_at, 1, 10) ORDER BY d.id""")
    return _check("date_inverted", "종료일이 생성일보다 빠름", "mid",
                  "영업 주기(리드타임)·분석이 음수로 계산됩니다. 수정 화면에서 날짜를 확인하세요.",
                  df, link=("crm.deals", {"tab": "edit"}, "id"))


def closed_mismatch() -> dict:
    open_list = ",".join("?" * len(db.OPEN_STAGES))
    df = _df(f"""SELECT d.id, c.name AS 거래처, d.title AS 기회명, d.stage AS 단계, d.closed_at AS 종료일
                 FROM deals d LEFT JOIN customers c ON c.id = d.customer_id
                 WHERE (d.stage IN (?, ?) AND d.closed_at IS NULL)
                    OR (d.stage IN ({open_list}) AND d.closed_at IS NOT NULL) ORDER BY d.id""",
             (db.STAGE_WON, db.STAGE_LOST, *db.OPEN_STAGES))
    return _check("closed_mismatch", "단계와 종료일이 맞지 않음", "low",
                  "끝난 기회에 종료일이 없거나, 진행 중인데 종료일이 있습니다 — 월별 수주·실주 집계가 틀어집니다.",
                  df, link=("crm.deals", {"tab": "edit"}, "id"), fix="closed_sync",
                  fix_label="종료일 맞추기 (끝난 건 = 단계 진입일, 진행 중 = 비움)")


def paid_mismatch() -> dict:
    df = _df(f"""SELECT s.id, c.name AS 거래처, s.sale_date AS 매출일, COALESCE(s.total_amount, s.amount) AS 합계,
                        COALESCE(s.paid_amount, 0) AS 입금액, COALESCE(p.total, 0) AS 입금내역합계
                 FROM sales s LEFT JOIN customers c ON c.id = s.customer_id
                 LEFT JOIN (SELECT sale_id, SUM(amount) AS total FROM payments GROUP BY sale_id) p ON p.sale_id = s.id
                 WHERE COALESCE(s.status, '') <> '{db.SALE_CANCELLED}'
                   AND COALESCE(s.paid_amount, 0) <> COALESCE(p.total, 0)
                 ORDER BY s.id""")
    return _check("paid_mismatch", "입금액 ≠ 입금 내역 합계", "high",
                  "매출의 입금액과 입금 내역(payments) 합계가 다릅니다 — 미수금·대사가 틀립니다. "
                  "입금액은 직접 고치지 말고 매출 화면에서 입금 등록·반제로 맞추세요.",
                  df, link=("finance.sales", {}, "sid"))


def overpaid() -> dict:
    df = _df(f"""SELECT s.id, c.name AS 거래처, s.sale_date AS 매출일, COALESCE(s.total_amount, s.amount) AS 합계,
                        s.paid_amount AS 입금액
                 FROM sales s LEFT JOIN customers c ON c.id = s.customer_id
                 WHERE COALESCE(s.status, '') <> '{db.SALE_CANCELLED}' AND COALESCE(s.total_amount, s.amount) > 0
                   AND COALESCE(s.paid_amount, 0) > COALESCE(s.total_amount, s.amount) ORDER BY s.id""")
    return _check("overpaid", "청구액보다 많이 입금됨", "mid",
                  "초과분은 선수금으로 옮기거나 환불해야 합니다 (매출 화면 → 선수금).",
                  df, link=("finance.sales", {}, "sid"))


def lost_without_reason() -> dict:
    df = _df("""SELECT d.id, c.name AS 거래처, d.title AS 기회명, d.closed_at AS 종료일, d.owner AS 담당자
                FROM deals d LEFT JOIN customers c ON c.id = d.customer_id
                WHERE d.stage = ? AND COALESCE(d.lost_reason, '') = '' ORDER BY d.id""", (db.STAGE_LOST,))
    return _check("lost_no_reason", "실주 사유 없음", "low",
                  "실주 분석(가격·경쟁사 등)에서 빠집니다.", df, link=("crm.deals", {"tab": "edit"}, "id"))


def stale_open() -> dict:
    open_list = ",".join("?" * len(db.OPEN_STAGES))
    df = _df(f"""SELECT d.id, c.name AS 거래처, d.title AS 기회명, d.stage AS 단계, d.expected_close AS 예상마감일,
                        d.amount AS 예상금액, d.owner AS 담당자
                 FROM deals d LEFT JOIN customers c ON c.id = d.customer_id
                 WHERE d.stage IN ({open_list}) AND d.expected_close IS NOT NULL AND d.expected_close < ?
                 ORDER BY d.expected_close""", (*db.OPEN_STAGES, date.today().isoformat()))
    return _check("stale_open", "예상마감일이 지난 진행 기회", "low",
                  "매출예측(파이프라인)에 남아 숫자를 부풀립니다. 마감일을 새로 잡거나 실주 처리하세요.",
                  df, link=("crm.deals", {"tab": "edit"}, "id"))


def unlinked_owner() -> dict:
    counts = ent.unlinked_counts()
    labels = {"customers": "거래처", "deals": "영업기회", "activities": "영업활동", "sales": "매출", "targets": "목표"}
    df = pd.DataFrame([{"대상": labels[t], "건수": n} for t, n in counts.items() if n], columns=["대상", "건수"])
    return _check("unlinked_owner", "담당자가 사용자와 연결되지 않음", "mid",
                  "담당자 이름만 있고 사용자 계정과 연결되지 않아, 담당자 본인 화면·권한 범위에서 보이지 않습니다.",
                  df, count=sum(counts.values()), link=("admin.org", {"tab": "transfer"}, None), fix="owner_link",
                  fix_label="이름이 같은 사용자에게 연결 (동명이인은 제외)")


CHECKS = [duplicate_customers, bad_biz_no, paid_mismatch, won_without_sales, overpaid, unlinked_owner,
          date_inverted, closed_mismatch, lost_without_reason, stale_open]


def run_all() -> list[dict]:
    return [c() for c in CHECKS]


def fix(action: str) -> int:
    """자동 수정. 바꾼 건수를 돌려주고 감사로그를 남긴다."""
    if action == "owner_link":
        before = sum(ent.unlinked_counts().values())
        with db.get_conn() as conn:
            db.link_owner_ids(conn)
        changed = before - sum(ent.unlinked_counts().values())
    elif action == "closed_sync":
        open_list = ",".join("?" * len(db.OPEN_STAGES))
        with db.get_conn() as conn:
            changed = conn.execute(
                "UPDATE deals SET closed_at = substr(COALESCE(stage_since, updated_at, created_at), 1, 10), "
                "row_version = COALESCE(row_version, 0) + 1 WHERE stage IN (?, ?) AND closed_at IS NULL",
                (db.STAGE_WON, db.STAGE_LOST)).rowcount
            changed += conn.execute(
                f"UPDATE deals SET closed_at = NULL, row_version = COALESCE(row_version, 0) + 1 "
                f"WHERE stage IN ({open_list}) AND closed_at IS NOT NULL", tuple(db.OPEN_STAGES)).rowcount
    else:
        raise ValueError("알 수 없는 수정 항목입니다.")
    db.audit("데이터점검수정", "시스템", None, {"항목": action, "건수": changed})
    return int(changed)


def summary(results: list[dict]) -> dict[str, Any]:
    bad = [r for r in results if r["count"]]
    return {"total": sum(r["count"] for r in bad), "items": len(bad), "high": sum(1 for r in bad if r["severity"] == "high")}
