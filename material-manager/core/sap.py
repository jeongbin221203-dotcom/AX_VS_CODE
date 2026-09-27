"""SAP 연동 (자재 이동 전기).

흐름
  1) 입출고·취소를 등록할 때 같은 DB 트랜잭션에서 sap_outbox에 '전송 대기'를 넣는다(enqueue).
  2) process_outbox()가 대기 건을 '전송 중'으로 선점한 뒤 SAP(또는 사내 연계서버)로 보낸다.
     화면의 '지금 전송' 버튼, 또는 `python sap_sync.py --loop 60` 같은 주기 실행으로 돌린다.
  3) 성공하면 SAP 자재문서번호를 저장한다. 일시 오류는 점점 간격을 늘려 자동 재시도하고,
     업무 오류(자재 없음·재고 부족 등)나 재시도 한도 초과는 FAILED로 두어 사람이 조치 후 재전송한다.

중복 전기 방지: 모든 요청에 멱등키(MM-TX-<거래ID>)를 붙인다. 응답을 못 받아 다시 보내더라도
받는 쪽이 같은 키를 한 번만 처리하면 두 번 전기되지 않는다.

전송 방식(config.SAP_MODE)
  off   전송하지 않는다(대기열도 만들지 않음).
  mock  실제 SAP 없이 자재문서번호를 흉내 낸다(시연·테스트용). SAP 자재번호가 'FAIL'로 시작하면 업무 오류를 낸다.
  http  config.SAP_ENDPOINT로 이 모듈의 중립 JSON을 보낸다. 사내 연계서버(EAI, SAP Integration Suite 등)가
        받아 BAPI_GOODSMVT_CREATE / 자재문서 OData API 등으로 변환하는 구성을 가정한다.
        (이 저장소에는 실제 SAP 시스템에 붙여 검증한 코드가 없다. 연계서버 규격은 SAP 담당 팀과 맞춰야 한다.)
"""

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta

import config
from core import audit, db
from core.utils import now_str

SOURCE_SYSTEM = "MATERIAL-MANAGER"
STALE_SENDING_MINUTES = 10          # '전송 중'인 채로 이보다 오래되면 중단된 것으로 보고 다시 보낸다


class SapError(Exception):
    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


def enabled() -> bool:
    return config.SAP_MODE in ("mock", "http")


# ── 이동유형 ─────────────────────────────────────────────────
def transfer_movement_type(same_plant: bool) -> str:
    return config.SAP_MOVEMENT_TYPES["TRF_SLOC" if same_plant else "TRF_PLANT"]


def movement_type(tx_type: str, qty: float, po_no: str = "") -> str:
    t = config.SAP_MOVEMENT_TYPES
    if tx_type == "IN":
        return t["IN_PO"] if po_no else t["IN_NO_PO"]
    if tx_type == "OUT":
        return t["OUT"]
    return t["ADJ_PLUS"] if qty >= 0 else t["ADJ_MINUS"]


def reversal_movement_type(original: str) -> str:
    return config.SAP_REVERSAL_TYPES.get(original, "")


def warehouse_problem(warehouse: dict) -> str:
    missing = [label for key, label in (("sap_plant", "SAP 플랜트"), ("sap_sloc", "SAP 저장위치"))
               if not str(warehouse.get(key) or "").strip()]
    if missing:
        return f"창고 {warehouse.get('code')}에 SAP 매핑({', '.join(missing)})이 없습니다. 시스템관리자가 조직 화면에서 입력해야 합니다."
    return ""


def mapping_problem(material: dict, tx_type: str, cost_center: str, po_no: str, po_item: str,
                    warehouse: dict | None = None) -> str:
    """SAP로 보낼 수 없는 입력이면 사유. 연동이 켜져 있을 때만 검사한다.
    SAP 자재번호는 자재에, 플랜트·저장위치는 창고에 있다."""
    if not str(material.get("sap_matnr") or "").strip():
        return "이 자재는 SAP 자재번호가 없어 등록할 수 없습니다. 자재 마스터에서 먼저 입력하세요."
    if warehouse is not None:
        problem = warehouse_problem(warehouse)
        if problem:
            return problem
    if tx_type == "OUT" and not cost_center:
        return "SAP 연동 중에는 출고에 원가센터가 필요합니다."
    if po_no and not po_item:
        return "구매오더 품목번호를 입력하세요 (예: 10)."
    return ""


# ── 대기열 ───────────────────────────────────────────────────
def enqueue(conn, tx_id: int, reversal_of: int | None = None) -> str:
    """거래와 같은 트랜잭션에서 호출한다. 넣은 상태를 돌려준다.

    취소 거래인데 원거래가 아직 SAP에 가지 않았다면 둘 다 보낼 필요가 없다 → 둘 다 CANCELLED.
    """
    ts = now_str()
    status = "PENDING"
    if reversal_of is not None:
        orig = conn.execute("SELECT id, status FROM sap_outbox WHERE tx_id = ?", (reversal_of,)).fetchone()
        if orig is None:                              # 원거래가 SAP 연동 전 거래 → SAP에도 없으니 보낼 것 없음
            return "NONE"
        if orig["status"] in ("PENDING", "ERROR", "FAILED"):
            conn.execute("UPDATE sap_outbox SET status = 'CANCELLED', last_error = ?, updated_at = ? WHERE id = ?",
                         ("전송 전에 취소됨", ts, orig["id"]))
            status = "CANCELLED"
    conn.execute(
        "INSERT INTO sap_outbox (tx_id, status, last_error, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (tx_id, status, "원거래가 전송 전에 취소되어 보낼 필요 없음" if status == "CANCELLED" else "", ts, ts),
    )
    return status


TX_WITH_MAPPING = """
    SELECT t.*, m.code, m.unit, m.sap_matnr, w.sap_sloc AS sloc, p.sap_plant AS plant
    FROM transactions t JOIN materials m ON m.id = t.material_id
    JOIN warehouses w ON w.id = t.warehouse_id JOIN plants p ON p.id = w.plant_id
    WHERE t.id = ?
"""


def build_payload(conn, tx_id: int) -> tuple[dict | None, str]:
    """(보낼 내용, 아직 보낼 수 없는 사유). 사유가 'WAIT'이면 원거래 전기를 기다린다.
    창고 간 이동은 출고 쪽 한 건에 받는 창고를 함께 실어 311/301 한 번으로 전기한다."""
    t = conn.execute(TX_WITH_MAPPING, (tx_id,)).fetchone()
    if t is None:
        return None, "거래가 없습니다."
    key = f"MM-TX-{tx_id}"
    if t["reversal_of"] is not None:
        orig = conn.execute("SELECT status, sap_doc_no, sap_doc_year FROM sap_outbox WHERE tx_id = ?",
                            (t["reversal_of"],)).fetchone()
        if orig is None:
            return None, "원거래가 SAP로 전송된 기록이 없습니다 (연동 전 거래). SAP에서 직접 처리하세요."
        if orig["status"] != "SENT":
            return None, "WAIT"
        return {"idempotencyKey": key, "action": "CANCEL", "sourceSystem": SOURCE_SYSTEM,
                "postingDate": t["tx_date"], "movementType": t["movement_type"],
                "originalDocument": orig["sap_doc_no"], "originalYear": orig["sap_doc_year"],
                "reason": t["note"]}, ""
    payload = {"idempotencyKey": key, "action": "POST", "sourceSystem": SOURCE_SYSTEM,
               "postingDate": t["tx_date"], "documentDate": t["tx_date"], "movementType": t["movement_type"],
               "material": t["sap_matnr"], "plant": t["plant"], "storageLocation": t["sloc"],
               "quantity": abs(float(t["qty"])), "unit": t["unit"],
               "purchaseOrder": t["po_no"], "purchaseOrderItem": t["po_item"], "costCenter": t["cost_center"],
               "reference": t["ref_no"], "headerText": f"{t['code']} {t['partner']}".strip()[:25],
               "enteredBy": t["created_by"], "batch": t["lot_no"] or ""}
    if t["po_no"]:                               # 이 시스템 발주면 SAP 구매오더 번호로 바꿔 보낸다
        po = conn.execute("SELECT sap_po_no FROM purchase_orders WHERE po_no = ?", (t["po_no"],)).fetchone()
        if po is not None:
            if not po["sap_po_no"]:
                return None, f"발주 {t['po_no']}에 SAP 구매오더 번호가 없습니다. 구매 화면에서 입력한 뒤 재전송하세요."
            payload["purchaseOrder"] = po["sap_po_no"]
    if t["transfer_no"]:
        dest = conn.execute(
            "SELECT id FROM transactions WHERE transfer_no = ? AND id <> ? AND reversal_of IS NULL "
            "AND tx_type = 'IN' AND lot_no = ?", (t["transfer_no"], tx_id, t["lot_no"])).fetchone()
        d = conn.execute(TX_WITH_MAPPING, (dest["id"],)).fetchone() if dest else None
        if d is None:
            return None, "창고 간 이동의 받는 쪽 거래가 없습니다."
        payload.update({"receivingPlant": d["plant"], "receivingStorageLocation": d["sloc"],
                        "transferNo": t["transfer_no"]})
    return payload, ""


# ── 전송 방식 ────────────────────────────────────────────────
@dataclass
class Posted:
    doc_no: str
    year: str


class MockClient:
    """SAP 없이 동작을 확인하는 모의 전송. 같은 멱등키에는 항상 같은 문서번호를 돌려준다."""

    def send(self, payload: dict) -> Posted:
        if payload["action"] == "POST" and str(payload.get("material", "")).upper().startswith("FAIL"):
            raise SapError("(모의) M7 021: 자재가 플랜트에 없습니다", retryable=False)
        n = int(hashlib.sha1(payload["idempotencyKey"].encode(), usedforsecurity=False).hexdigest()[:8], 16) % 10**8
        return Posted(f"49{n:08d}", payload["postingDate"][:4])


class HttpClient:
    """사내 연계서버로 JSON을 보낸다. 응답 형식: {"materialDocument": "...", "year": "2026"}"""

    @staticmethod
    def _check_endpoint() -> None:
        if not config.SAP_ENDPOINT:
            raise SapError("MM_SAP_ENDPOINT가 설정되지 않았습니다.", retryable=False)
        host = urllib.parse.urlsplit(config.SAP_ENDPOINT).hostname or ""
        if not config.SAP_ENDPOINT.startswith("https://") and host not in ("127.0.0.1", "localhost", "::1"):
            # 인증 토큰과 거래 내용이 평문으로 나가지 않게 한다 (같은 PC의 테스트 서버만 예외)
            raise SapError("SAP 연계서버 주소는 https:// 여야 합니다.", retryable=False)

    def send(self, payload: dict) -> Posted:
        self._check_endpoint()
        req = urllib.request.Request(
            config.SAP_ENDPOINT.rstrip("/") + "/goods-movements",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", "Idempotency-Key": payload["idempotencyKey"],
                     **({"Authorization": f"Bearer {config.SAP_TOKEN}"} if config.SAP_TOKEN else {})},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=config.SAP_TIMEOUT) as res:
                body = json.loads(res.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            # 4xx = 보낸 내용이 잘못됨(재시도해도 같음), 5xx = 상대 서버 문제(재시도)
            raise SapError(f"HTTP {exc.code}: {detail}", retryable=exc.code >= 500 or exc.code == 429) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SapError(f"연결 실패: {exc}", retryable=True) from exc
        doc = str(body.get("materialDocument") or "")
        if not doc:
            raise SapError(f"응답에 자재문서번호가 없습니다: {str(body)[:200]}", retryable=False)
        return Posted(doc, str(body.get("year") or payload["postingDate"][:4]))


def client():
    return MockClient() if config.SAP_MODE == "mock" else HttpClient()


# ── 처리 ─────────────────────────────────────────────────────
def _backoff(attempts: int) -> str:
    return (datetime.now() + timedelta(minutes=min(2 ** attempts, 60))).strftime("%Y-%m-%d %H:%M:%S")


def process_outbox(limit: int = 50) -> dict:
    """대기 건을 보낸다. {'sent': n, 'error': n, 'failed': n, 'waiting': n}"""
    counts = {"sent": 0, "error": 0, "failed": 0, "waiting": 0}
    if not enabled():
        return counts
    now = now_str()
    stale = (datetime.now() - timedelta(minutes=STALE_SENDING_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    with db.transaction() as conn:
        conn.execute("UPDATE sap_outbox SET status = 'ERROR', last_error = '전송 중 중단됨 — 다시 보냄', "
                     "updated_at = ? WHERE status = 'SENDING' AND updated_at < ?", (now, stale))
        rows = conn.execute(
            """
            SELECT o.id, o.tx_id, o.attempts FROM sap_outbox o
            WHERE o.status = 'PENDING' OR (o.status = 'ERROR' AND o.next_try_at <= ?)
            ORDER BY o.tx_id LIMIT ?
            """ + db.skip_locked(), (now, limit)).fetchall()
        # PostgreSQL: 다른 배치 서버가 잡은 행은 건너뛴다(SKIP LOCKED) → 같은 건을 두 서버가 보내지 않는다
        claimed = []
        for r in rows:
            payload, why = build_payload(conn, r["tx_id"])
            if why == "WAIT":
                counts["waiting"] += 1
                continue
            if payload is None:
                conn.execute("UPDATE sap_outbox SET status = 'FAILED', last_error = ?, updated_at = ? WHERE id = ?",
                             (why, now, r["id"]))
                counts["failed"] += 1
                continue
            conn.execute("UPDATE sap_outbox SET status = 'SENDING', payload = ?, updated_at = ? WHERE id = ?",
                         (json.dumps(payload, ensure_ascii=False), now, r["id"]))
            claimed.append((r, payload))

    sender = client()
    for r, payload in claimed:                      # 네트워크 호출은 DB 잠금 밖에서 한다
        attempts = r["attempts"] + 1
        try:
            posted = sender.send(payload)
        except SapError as exc:
            final = not exc.retryable or attempts >= config.SAP_MAX_ATTEMPTS
            with db.transaction() as conn:
                conn.execute(
                    "UPDATE sap_outbox SET status = ?, attempts = ?, last_error = ?, next_try_at = ?, "
                    "updated_at = ? WHERE id = ?",
                    ("FAILED" if final else "ERROR", attempts, str(exc)[:500],
                     "" if final else _backoff(attempts), now_str(), r["id"]))
            counts["failed" if final else "error"] += 1
            continue
        with db.transaction() as conn:
            conn.execute(
                "UPDATE sap_outbox SET status = 'SENT', attempts = ?, last_error = '', sap_doc_no = ?, "
                "sap_doc_year = ?, updated_at = ? WHERE id = ?",
                (attempts, posted.doc_no, posted.year, now_str(), r["id"]))
            # 전송 중에 취소가 들어와 '전송 전 취소'로 처리됐다면, 원거래가 실제로 전기됐으니 취소도 보내야 한다
            conn.execute(
                """
                UPDATE sap_outbox SET status = 'PENDING', last_error = '', updated_at = ?
                WHERE status = 'CANCELLED'
                  AND tx_id IN (SELECT id FROM transactions WHERE reversal_of = ?)
                """, (now_str(), r["tx_id"]))
        counts["sent"] += 1
    return counts


def retry(outbox_id: int, actor: dict | None, wh_ids=None) -> str:
    """실패 건을 다시 대기로 돌린다 (자재 매핑 등을 고친 뒤)."""
    with db.transaction() as conn:
        row = conn.execute("SELECT o.status, o.tx_id, t.warehouse_id FROM sap_outbox o "
                           "JOIN transactions t ON t.id = o.tx_id WHERE o.id = ?", (outbox_id,)).fetchone()
        if row is None or row["status"] not in ("ERROR", "FAILED"):
            return ""
        if wh_ids is not None and row["warehouse_id"] not in wh_ids:
            return ""
        conn.execute("UPDATE sap_outbox SET status = 'PENDING', attempts = 0, next_try_at = '', updated_at = ? "
                     "WHERE id = ?", (now_str(), outbox_id))
        audit.record(conn, actor, "SAP_RETRY", "sap_outbox", outbox_id, {"tx_id": row["tx_id"]})
    return "재전송 대기로 돌렸습니다."


def unsent_until(conn, end_date: str) -> int:
    """end_date까지의 거래 중 SAP 전기가 끝나지 않은 건수 (월 마감 전 확인용)."""
    return conn.execute(
        """
        SELECT COUNT(*) FROM sap_outbox o JOIN transactions t ON t.id = o.tx_id
        WHERE t.tx_date <= ? AND o.status NOT IN ('SENT', 'CANCELLED')
        """, (end_date,)).fetchone()[0]


def summary(wh_ids=None) -> dict[str, int]:
    frag, wp = db.in_clause(wh_ids)
    df = db.query_df("SELECT o.status, COUNT(*) AS n FROM sap_outbox o JOIN transactions t ON t.id = o.tx_id"
                     f"{' WHERE t.warehouse_id' + frag if frag else ''} GROUP BY o.status", wp)
    return {k: int(v) for k, v in zip(df["status"], df["n"])}


def outbox_page(statuses: list[str] | None = None, wh_ids=None, page: int = 1, size: int = 100):
    from core import repository as repo
    sql = """
        SELECT o.id, o.tx_id, t.tx_date, t.tx_type, t.movement_type, w.code AS wh_code, m.code, m.name,
               m.sap_matnr, t.qty, m.unit, o.status, o.attempts, o.sap_doc_no, o.sap_doc_year, o.last_error,
               o.next_try_at, o.updated_at, t.reversal_of, t.transfer_no
        FROM sap_outbox o
        JOIN transactions t ON t.id = o.tx_id
        JOIN materials m ON m.id = t.material_id
        JOIN warehouses w ON w.id = t.warehouse_id
        WHERE 1 = 1
    """
    params: list = []
    if statuses:
        sql += f" AND o.status IN ({','.join('?' * len(statuses))})"
        params += statuses
    frag, wp = db.in_clause(wh_ids)
    if frag:
        sql += f" AND t.warehouse_id{frag}"
        params += wp
    return repo.paged(sql + " ORDER BY o.id DESC", params, page, size)
