"""영업관리에서 가져온 보강: 감사로그 해시 체인·요청 번호, 작업 임대 갱신, API 인증 실패 IP 차단, 입력 오류 전역 처리."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("MM_DB_PATH", str(Path(tempfile.mkdtemp(prefix="mm_r8_")) / "test.db"))
os.environ.setdefault("MM_PW_ITERATIONS", "1000")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import config  # noqa: E402
from core import api_keys, audit, db, jobs  # noqa: E402
from test_advanced import fresh  # noqa: E402,F401
from test_app import app, login, post  # noqa: E402,F401


def _log(n: int, action: str = "SEED") -> None:
    for i in range(n):
        audit.log({"id": None, "name": "t", "role": "ADMIN", "ip": "1.2.3.4"}, action, "x", i, {"i": i})


# ── 해시 체인 ────────────────────────────────────────────────
def test_chain_seal_and_verify(app):  # noqa: F811
    _log(5)
    assert audit.verify()["ok"]
    r = audit.verify()
    assert r["ok"] and r["sealed"] >= 5 and r["unsealed"] == 0 and len(r["head"]) == 64
    _log(3)                                                  # 새 기록은 봉인 대기 → 검증 때 이어 붙는다
    r2 = audit.verify()
    assert r2["ok"] and r2["sealed"] == r["sealed"] + 3 and r2["head"] != r["head"]
    assert audit.chain_head()["hash"] == r2["head"]


def test_sealed_rows_cannot_be_edited_or_deleted(app):  # noqa: F811
    _log(2)
    audit.seal()
    with pytest.raises(Exception):
        db.execute("UPDATE audit_log SET detail = 'x' WHERE id = (SELECT MIN(id) FROM audit_log)")
    with pytest.raises(Exception):
        db.execute("DELETE FROM audit_log WHERE id = (SELECT MIN(id) FROM audit_log)")
    _log(1)                                                  # 봉인 전 기록도 봉인 열 말고는 못 고친다
    with pytest.raises(Exception):
        db.execute("UPDATE audit_log SET detail = 'y' WHERE seq IS NULL")


def test_verify_detects_tampering_even_with_trigger_off(app):  # noqa: F811
    if db.is_pg():
        pytest.skip("트리거 해제는 SQLite 로 확인")
    _log(6)
    assert audit.verify()["ok"]
    first = int(db.scalar("SELECT MIN(seq) FROM audit_log"))
    mid = first + 2
    db.execute("DROP TRIGGER audit_seal_only")
    db.execute("DROP TRIGGER audit_no_delete")
    db.execute("UPDATE audit_log SET detail = '바뀜' WHERE seq = ?", (mid,))
    r = audit.verify()
    assert not r["ok"] and r["broken_seq"] == mid and "바뀌었습니다" in r["reason"]
    db.execute("UPDATE audit_log SET detail = detail WHERE seq = ?", (mid,))        # 원복은 못 함(내용이 달라진 채)
    db.execute("DELETE FROM audit_log WHERE seq = ?", (mid + 1,))                   # 지우기도 찾아낸다
    r = audit.verify()
    assert not r["ok"]


def test_request_id_recorded_and_searchable(app):  # noqa: F811
    client = login(app.test_client(), "admin")
    res = client.get("/admin/audit", headers={"X-Request-ID": "req-abc-123"})
    assert res.status_code == 200
    res = post(client, "/admin/audit/verify", headers={"X-Request-ID": "req-find-me-1"})
    assert res.status_code == 302
    page = client.get("/admin/audit?q=req-find-me-1").get_data(as_text=True)
    assert "req-find-me-1" in page and "무결성 검증" in page
    df, total = audit.audit_page("2000-01-01", "2999-12-31", keyword="req-find-me-1")
    assert total >= 1 and set(df["request_id"]) == {"req-find-me-1"}


# ── 작업 임대 갱신 ───────────────────────────────────────────
def test_job_lease_is_renewed_while_running(fresh, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "JOB_LEASE_SECONDS", 1.5)
    seen = {}

    def slow() -> str:
        time.sleep(2.6)                                     # 임대(1.5초)보다 오래 걸린다
        seen["lease"] = db.scalar("SELECT lease_until FROM job_locks WHERE name = 'slowjob'")
        seen["other"] = jobs._acquire("slowjob", 0, "other-server", True)     # 다른 서버가 가로채지 못함
        return "끝"

    monkeypatch.setitem(jobs.JOBS, "slowjob", jobs.Job("slowjob", "느린 작업", 60, slow))
    ran, msg = jobs.run("slowjob", force=True, holder="me:1")
    assert ran and msg == "끝"
    assert seen["other"] is False


def test_job_audit_seal_registered(fresh):  # noqa: F811
    assert "audit_seal" in jobs.JOBS
    _log(2)
    ran, msg = jobs.run("audit_seal", force=True)
    assert ran and "봉인" in msg


# ── API 인증 실패 IP 차단 ───────────────────────────────────
def test_api_auth_failures_block_ip(fresh, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "API_FAIL_MAX", 4)
    for _ in range(4):
        key, status, _p = api_keys.check("mmk_wrongwrongwrong", "9.9.9.9", "stock:read")
        assert key is None and status == 401
    key, status, problem = api_keys.check("mmk_wrongwrongwrong", "9.9.9.9", "stock:read")
    assert status == 429 and "막았습니다" in problem
    assert api_keys.check("", "8.8.8.8", "stock:read")[1] == 401            # 다른 IP 는 영향 없음
    n = int(db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'API_AUTH_FAIL' AND ip = '9.9.9.9'"))
    assert n == 2                                                            # 첫 실패 + 막힌 순간만


# ── 입력 오류 전역 처리 ──────────────────────────────────────
def test_unhandled_value_error_is_400_not_500(app):  # noqa: F811
    def boom():
        raise ValueError("숫자를 다시 확인하세요 (1e99999)")

    def boom_en():
        raise ValueError("invalid literal for int() with base 10: 'x'")

    app.add_url_rule("/_t/boom", "t_boom", boom, methods=["GET"])
    app.add_url_rule("/_t/boom_en", "t_boom_en", boom_en, methods=["GET"])
    client = login(app.test_client(), "admin")
    res = client.get("/_t/boom")
    assert res.status_code == 400 and "숫자를 다시 확인하세요" in res.get_data(as_text=True)
    res = client.get("/_t/boom_en")
    body = res.get_data(as_text=True)
    assert res.status_code == 400 and "invalid literal" not in body and "입력한 값을 확인하세요" in body


# ── 백그라운드 작업 ──────────────────────────────────────────
def test_task_runs_and_stores_result_file(fresh, monkeypatch):  # noqa: F811
    from core import tasks
    out = {}

    def work(progress):
        progress.total(4)
        for _ in range(4):
            progress.advance()
        return tasks.Output("끝났습니다", b"hello-bytes", "결과:파일?.txt", "text/plain")

    tid = tasks.start("t", "시험 작업", work, {"id": 7, "name": "홍", "role": "ADMIN", "ip": ""}, inline=False)
    tasks.wait(tid)
    t = tasks.get(tid)
    assert t["status"] == "DONE" and t["done"] == 4 and t["total"] == 4 and t["message"] == "끝났습니다"
    assert "/" not in t["result_name"] and ":" not in t["result_name"]          # 파일 이름 정리
    assert tasks.result_bytes(t) == b"hello-bytes"
    assert tasks.visible(t, {"id": 7, "role": "CLERK"}) and not tasks.visible(t, {"id": 8, "role": "CLERK"})
    assert tasks.visible(t, {"id": 8, "role": "ADMIN"})


def test_task_failure_and_stale(fresh):  # noqa: F811
    from core import tasks

    def boom(progress):
        raise RuntimeError("secret internal sql path")

    tid = tasks.start("t", "실패 작업", boom, None, inline=True)
    t = tasks.get(tid)
    assert t["status"] == "ERROR" and "secret internal" not in t["message"] and str(tid) in t["message"]
    # 하트비트가 끊긴 작업은 중단 표시
    db.execute("INSERT INTO bg_tasks (kind, title, status, created_at, heartbeat_at) VALUES ('t', 'x', 'RUNNING', "
               "'2000-01-01 00:00:00', '2000-01-01 00:00:00')")
    assert tasks.mark_stale() == 1
    assert db.scalar("SELECT status FROM bg_tasks WHERE title = 'x'") == "ERROR"


def test_big_bulk_apply_goes_background(app, monkeypatch):  # noqa: F811
    import io
    import pandas as pd
    from core import tasks
    monkeypatch.setattr(config, "BG_ROWS", 3)
    monkeypatch.setattr(config, "BG_INLINE", True)                    # 그 자리에서 돌려 결과를 바로 확인
    client = login(app.test_client(), "admin")
    df = pd.DataFrame({"거래처명": [f"시험상사{i}" for i in range(6)], "구분": ["공급처"] * 6})
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    res = post(client, "/partners/import", {"file": (io.BytesIO(buf.getvalue()), "p.xlsx")}, content_type="multipart/form-data")
    token = __import__("re").search(r'name="token" value="([0-9a-f]{32})"', res.get_data(as_text=True))
    assert token, res.get_data(as_text=True)[:500]
    res = post(client, "/partners/import/apply", {"token": token.group(1)})
    assert res.status_code == 302 and "/tasks/" in res.headers["Location"]
    tid = int(res.headers["Location"].rsplit("/", 1)[-1])
    t = tasks.get(tid)
    assert t["status"] == "DONE" and "6" in t["message"]
    assert int(db.scalar("SELECT COUNT(*) FROM partners WHERE name LIKE '시험상사%'")) == 6
    page = client.get(res.headers["Location"]).get_data(as_text=True)
    assert "시험상사" not in page and "거래처 일괄 반영" in page
    assert client.get(f"/tasks/{tid}.json").get_json()["status"] == "DONE"
    other = login(app.test_client(), "clerk")
    assert other.get(f"/tasks/{tid}").status_code == 404                # 남의 작업은 못 본다


# ── 월말 미지급·GR/IR 스냅샷 ─────────────────────────────────
def test_ap_snapshot_frozen_at_close_and_backfill(fresh):  # noqa: F811
    from datetime import date, timedelta

    from core import periods, purchasing, services
    from test_advanced import M1, M2, mid, wh
    clerk = {"id": 11, "name": "담당1", "role": "CLERK", "ip": ""}
    m = mid("PKG-001")
    last = date.today().replace(day=1) - timedelta(days=1)
    pr = purchasing.create_pr(wh(), [(m, 10, 1000)], date.today().isoformat(), "보충", clerk)
    purchasing.decide_pr(pr.id, True, "", M1)
    item = int(db.scalar("SELECT id FROM pr_items WHERE pr_id = ?", (pr.id,)))
    po = purchasing.create_po(pr.id, "공급", {item: 1000}, "", "", M2)
    po_no = db.scalar("SELECT po_no FROM purchase_orders WHERE id = ?", (po.id,))
    tx = services.register_transaction(m, "IN", 10, last.isoformat(), 1000, warehouse_id=wh(), po_no=po_no, po_item="10", actor=M1)
    assert tx.ok, tx.message                                             # 월말까지 입고 10,000, 계산서 아직 없음
    ym = last.strftime("%Y-%m")
    while periods.next_closable() <= ym:
        assert periods.close_month(periods.next_closable(), M1).ok
    ap = periods.ap_df(ym)
    row = ap[ap["po_no"] == po_no].iloc[0]
    assert float(row["received"]) == 10000 and float(row["invoiced"]) == 0 and float(row["gr_ir"]) == 10000
    # 계산서를 마감 뒤에 올려도(작성일자가 다음 달) 마감 당시 잔액은 그대로
    with db.transaction() as conn:
        conn.execute("INSERT INTO documents (tx_id, doc_type, issue_date, supply_amount, tax_amount, file_name, stored_name, mime, "
                     "size, sha256, created_at, created_by_id) VALUES (?, 'E_TAX_INVOICE', ?, 10000, 1000, 'x.png', 's1', 'image/png', 1, "
                     "'hh1', ?, ?)", (tx.tx_id, date.today().isoformat(), date.today().isoformat(), clerk["id"]))
    row = periods.ap_df(ym).query("po_no == @po_no").iloc[0]
    assert float(row["gr_ir"]) == 10000
    assert ym in periods.ap_months()
    # 이 기능 전에 마감한 달: 비어 있으면 보충
    db.execute("DELETE FROM ap_snapshots")
    assert ym in periods.ap_missing()
    r = periods.ap_backfill(M1)
    assert r.ok and int(db.scalar("SELECT COUNT(*) FROM ap_snapshots WHERE backfilled = 1")) >= 1
    # 마감 해제하면 그 달 스냅샷도 지운다
    assert periods.reopen("시험", {"id": 1, "name": "관리자", "role": "ADMIN", "ip": ""}).ok
    assert ym not in periods.ap_months() or int(db.scalar("SELECT COUNT(*) FROM ap_snapshots WHERE ym = ?", (ym,))) == 0


def test_periods_page_shows_ap_section_and_export(app):  # noqa: F811
    from datetime import date, timedelta

    from core import periods, purchasing, seed, services
    from test_advanced import M1, M2, mid, wh
    seed.seed()
    clerk = {"id": 11, "name": "담당1", "role": "CLERK", "ip": ""}
    m, last = mid("PKG-001"), date.today().replace(day=1) - timedelta(days=1)
    pr = purchasing.create_pr(wh(), [(m, 4, 500)], date.today().isoformat(), "보충", clerk)
    purchasing.decide_pr(pr.id, True, "", M1)
    item = int(db.scalar("SELECT id FROM pr_items WHERE pr_id = ?", (pr.id,)))
    po = purchasing.create_po(pr.id, "공급처A", {item: 500}, "", "", M2)
    po_no = db.scalar("SELECT po_no FROM purchase_orders WHERE id = ?", (po.id,))
    assert services.register_transaction(m, "IN", 4, last.isoformat(), 500, warehouse_id=wh(), po_no=po_no, po_item="10", actor=M1).ok
    ym = last.strftime("%Y-%m")
    while periods.next_closable() <= ym:
        assert periods.close_month(periods.next_closable(), M1).ok
    client = login(app.test_client(), "admin")
    page = client.get("/periods/").get_data(as_text=True)
    assert "월말 미지급·GR/IR 스냅샷" in page and po_no in page and "공급처A" in page
    res = client.get(f"/periods/?ap={ym}&export=xlsx")
    assert res.status_code == 200 and res.data[:2] == b"PK"
    assert client.get("/tasks/").status_code == 200
