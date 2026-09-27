"""재고 대사: 이 앱의 재고와 SAP 재고를 (SAP 자재번호, 플랜트, 저장위치) 단위로 비교한다.

  앱 재고            이 앱의 창고별 현재고
  미전기 수량         SAP로 아직 전기되지 않은 거래의 증감 (대기·오류·실패). 창고 간 이동 입고는 출고 쪽 전송 상태를 따른다.
  SAP 예상 재고       앱 재고 − 미전기 수량  (SAP가 지금 갖고 있어야 할 값)
  차이               SAP 재고 − SAP 예상 재고. 0이 아니면 조사 대상(SAP에서 직접 처리한 거래, 연동 전 기초 재고 차이 등)

SAP 재고는 (1) SAP 재고 조회 화면(MB52 등)에서 내려받은 엑셀을 올리거나 (2) 연동 모드에서 조회한다.
"""

import json
import urllib.request

import pandas as pd

import config
from core import db, repository as repo
from core.utils import code_series

# 업로드 엑셀 헤더 → 표준 이름 (SAP 한글/영문 화면에서 흔한 이름들)
ALIASES = {
    "sap_matnr": ("SAP자재번호", "자재", "자재번호", "Material", "MATNR"),
    "plant": ("플랜트", "Plant", "WERKS"),
    "sloc": ("저장위치", "저장 위치", "Storage Location", "SLoc", "LGORT"),
    "sap_qty": ("수량", "가용재고", "제한없는 사용", "Unrestricted", "LABST", "Quantity"),
}


def normalize_sap_stock(raw: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    rename = {}
    for std, names in ALIASES.items():
        for c in raw.columns:
            if str(c).strip() in names:
                rename[c] = std
                break
    df = raw.rename(columns=rename)
    missing = [ALIASES[k][0] for k in ALIASES if k not in df.columns]
    if missing:
        return pd.DataFrame(), f"필수 컬럼이 없습니다: {', '.join(missing)} (SAP자재번호·플랜트·저장위치·수량)"
    df = df[list(ALIASES)].copy()
    for col in ("sap_matnr", "plant", "sloc"):
        df[col] = code_series(df[col]).str.upper()
    df["sap_qty"] = pd.to_numeric(df["sap_qty"], errors="coerce").fillna(0.0)
    df = df[df["sap_matnr"] != ""]
    return df.groupby(["sap_matnr", "plant", "sloc"], as_index=False)["sap_qty"].sum(), ""


def unsent_df(wh_ids=None) -> pd.DataFrame:
    """(자재, 창고)별 SAP 미전기 증감."""
    frag, wp = db.in_clause(wh_ids)
    return db.query_df(f"""
        SELECT t.material_id, t.warehouse_id, SUM({repo.EFFECT.format(t='t')}) AS unsent
        FROM transactions t
        LEFT JOIN sap_outbox o ON o.tx_id = t.id
        LEFT JOIN transactions s ON t.transfer_no <> '' AND t.tx_type = 'IN' AND s.transfer_no = t.transfer_no
             AND s.tx_type = 'OUT' AND s.lot_no = t.lot_no AND ((s.reversal_of IS NULL) = (t.reversal_of IS NULL))
        LEFT JOIN sap_outbox so ON so.tx_id = s.id
        WHERE COALESCE(o.status, so.status) IN ('PENDING', 'SENDING', 'ERROR', 'FAILED')
          {'AND t.warehouse_id' + frag if frag else ''}
        GROUP BY t.material_id, t.warehouse_id
        """, wp)


def compare(sap_stock: pd.DataFrame, wh_ids=None) -> pd.DataFrame:
    app = repo.stock_by_wh(wh_ids=wh_ids, include_inactive=True)
    unsent = unsent_df(wh_ids)
    app = app.merge(unsent, on=["material_id", "warehouse_id"], how="left")
    app["unsent"] = app["unsent"].fillna(0.0)
    app["expected"] = app["stock"] - app["unsent"]
    app = app.rename(columns={"sap_plant": "plant", "sap_sloc": "sloc"})
    for col in ("sap_matnr", "plant", "sloc"):
        app[col] = app[col].fillna("").astype(str).str.upper()
    merged = app.merge(sap_stock, on=["sap_matnr", "plant", "sloc"], how="outer", indicator=True)
    merged["sap_qty"] = merged["sap_qty"].fillna(0.0)
    for col in ("stock", "unsent", "expected"):
        merged[col] = merged[col].fillna(0.0)
    merged["diff"] = merged["sap_qty"] - merged["expected"]

    def verdict(r) -> str:
        if not r["sap_matnr"]:
            return "SAP 자재번호 없음"
        if r["_merge"] == "right_only":
            return "SAP에만 있음"
        if abs(r["diff"]) < 1e-9:
            return "일치 (전송 대기 반영)" if r["unsent"] else "일치"
        return "차이 — 조사 필요"

    merged["verdict"] = merged.apply(verdict, axis=1)
    merged = merged.drop(columns=["_merge"])
    order = {"차이 — 조사 필요": 0, "SAP에만 있음": 1, "SAP 자재번호 없음": 2}
    merged["_o"] = merged["verdict"].map(order).fillna(3)
    return merged.sort_values(["_o", "code", "wh_code"]).drop(columns=["_o"]).reset_index(drop=True)


def fetch_sap_stock(wh_ids=None) -> tuple[pd.DataFrame, str]:
    """연동 모드에서 SAP 재고를 가져온다. mock은 '전기 완료된 거래의 합'을 SAP 재고로 흉내 낸다."""
    if config.SAP_MODE == "mock":
        frag, wp = db.in_clause(wh_ids)
        df = db.query_df(f"""
            SELECT m.sap_matnr, p.sap_plant AS plant, w.sap_sloc AS sloc,
                   SUM({repo.EFFECT.format(t='t')}) AS sap_qty
            FROM transactions t
            JOIN materials m ON m.id = t.material_id JOIN warehouses w ON w.id = t.warehouse_id
            JOIN plants p ON p.id = w.plant_id
            LEFT JOIN sap_outbox o ON o.tx_id = t.id
            LEFT JOIN transactions s ON t.transfer_no <> '' AND t.tx_type = 'IN' AND s.transfer_no = t.transfer_no
                 AND s.tx_type = 'OUT' AND s.lot_no = t.lot_no AND ((s.reversal_of IS NULL) = (t.reversal_of IS NULL))
            LEFT JOIN sap_outbox so ON so.tx_id = s.id
            WHERE COALESCE(o.status, so.status, 'SENT') = 'SENT' {'AND t.warehouse_id' + frag if frag else ''}
            GROUP BY m.sap_matnr, p.sap_plant, w.sap_sloc
            """, wp)
        return normalize_sap_stock(df.rename(columns={"sap_matnr": "SAP자재번호", "plant": "플랜트",
                                                      "sloc": "저장위치", "sap_qty": "수량"}))
    if config.SAP_MODE == "http":
        from core import sap
        sap.HttpClient._check_endpoint()
        req = urllib.request.Request(config.SAP_ENDPOINT.rstrip("/") + "/stock",
                                     headers={"Authorization": f"Bearer {config.SAP_TOKEN}"} if config.SAP_TOKEN else {})
        with urllib.request.urlopen(req, timeout=config.SAP_TIMEOUT) as res:
            rows = json.loads(res.read().decode() or "[]")
        df = pd.DataFrame(rows).rename(columns={"material": "SAP자재번호", "plant": "플랜트",
                                               "storageLocation": "저장위치", "quantity": "수량"})
        return normalize_sap_stock(df)
    return pd.DataFrame(), "SAP 연동이 꺼져 있습니다. SAP 재고 엑셀을 올려 비교하세요."


def display(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["verdict", "code", "name", "wh_code", "sap_matnr", "plant", "sloc", "stock", "unsent",
              "expected", "sap_qty", "diff"]].copy()
    out.columns = ["판정", "자재코드", "자재명", "창고", "SAP자재", "플랜트", "저장위치", "앱 재고", "미전기",
                   "SAP 예상", "SAP 재고", "차이"]
    return out
