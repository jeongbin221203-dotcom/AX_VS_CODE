"""감시 지표 (Prometheus 텍스트 형식, GET /metrics). 추가 라이브러리 없이 만든다.

- 요청: 화면(endpoint)·결과(status)별 건수와 응답 시간 분포(히스토그램) — 이 서버 프로세스가 뜬 뒤부터 누적.
- 업무: 질문할 때마다 DB에서 센다 — SAP 전송 대기·실패, 결재 대기, 알림 실패, 재공품 금액, 안전재고 미달,
  배치 작업마다 마지막 성공 뒤 지난 시간·마지막 결과, DB 연결.
- 접근: MM_METRICS_TOKEN 이 있으면 'Authorization: Bearer <토큰>' 이 맞아야, 없으면 이 서버 자신(127.0.0.1)만.
  → 로그인 없이 열리지만 밖에서는 못 본다(업무 수치가 들어 있으므로).
감시 규칙 예시는 deploy/prometheus/ (scrape 설정 · 알림 규칙).
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from datetime import datetime

import config
from core import db

BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
_lock = threading.Lock()
_count: dict[tuple, int] = defaultdict(int)
_hist: dict[str, list] = {}
_started = time.time()


def observe(endpoint: str, method: str, status: int, seconds: float) -> None:
    ep = endpoint or "other"
    with _lock:
        _count[(ep, method, str(status))] += 1
        h = _hist.setdefault(ep, [0] * (len(BUCKETS) + 1) + [0.0])     # 버킷들 + +Inf + 합계
        for i, b in enumerate(BUCKETS):
            if seconds <= b:
                h[i] += 1
        h[len(BUCKETS)] += 1
        h[-1] += seconds


def _esc(v) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _line(name: str, value, **labels) -> str:
    lab = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items())
    return f"{name}{{{lab}}} {value}" if lab else f"{name} {value}"


def _business() -> list[str]:
    out = []
    try:
        with db.get_conn() as conn:
            q = lambda sql, p=(): conn.execute(sql, p).fetchall()             # noqa: E731
            out.append("# HELP mm_db_up DB 연결 (1=정상)\n# TYPE mm_db_up gauge\nmm_db_up 1")
            out.append("# HELP mm_sap_outbox ERP·SAP 전송 대기열 건수 (상태별)\n# TYPE mm_sap_outbox gauge")
            out += [_line("mm_sap_outbox", n, status=s) for s, n in q("SELECT status, COUNT(*) FROM sap_outbox GROUP BY status")]
            out.append("# HELP mm_notifications 결재 알림 건수 (보낼 길·상태별)\n# TYPE mm_notifications gauge")
            out += [_line("mm_notifications", n, channel=c or "email", status=s) for c, s, n in
                    q("SELECT channel, status, COUNT(*) FROM notifications GROUP BY channel, status")]
            pend = q("SELECT (SELECT COUNT(*) FROM approval_requests WHERE status = 'PENDING'), "
                     "(SELECT COUNT(*) FROM purchase_requests WHERE status = 'PENDING'), "
                     "(SELECT COUNT(*) FROM purchase_orders WHERE status = 'PENDING_APPROVAL')")[0]
            out.append("# HELP mm_approvals_pending 결재 대기 건수 (종류별)\n# TYPE mm_approvals_pending gauge")
            out += [_line("mm_approvals_pending", pend[0], kind="adjustment"), _line("mm_approvals_pending", pend[1], kind="pr"),
                    _line("mm_approvals_pending", pend[2], kind="po")]
            wip = q("SELECT COALESCE(SUM(l.issued_cost), 0), COUNT(DISTINCT p.id) FROM productions p "
                    "LEFT JOIN production_lines l ON l.production_id = p.id WHERE p.status = 'RELEASED'")[0]
            out.append("# HELP mm_wip_value_won 재공품 금액 (원)\n# TYPE mm_wip_value_won gauge\n" + _line("mm_wip_value_won", float(wip[0])))
            out.append("# HELP mm_work_orders_open 진행 중 작업지시\n# TYPE mm_work_orders_open gauge\n" + _line("mm_work_orders_open", wip[1]))
            short = q(f"SELECT COUNT(*) FROM (SELECT m.id FROM materials m LEFT JOIN transactions t ON t.material_id = m.id "
                      f"WHERE m.active = 1 AND m.safety_stock > 0 GROUP BY m.id, m.safety_stock "
                      f"HAVING {db.STOCK_EXPR} < m.safety_stock) s")[0][0]
            out.append("# HELP mm_safety_shortage_items 안전재고 미달 자재 종수\n# TYPE mm_safety_shortage_items gauge\n"
                       + _line("mm_safety_shortage_items", short))
            out.append("# HELP mm_job_last_success_age_seconds 배치 작업 마지막 성공 뒤 지난 초\n"
                       "# TYPE mm_job_last_success_age_seconds gauge")
            now = datetime.now()
            for name, finished, status in q("SELECT name, last_finished, last_status FROM job_locks"):
                if finished and status == "OK":
                    try:
                        age = (now - datetime.fromisoformat(str(finished)[:19])).total_seconds()
                        out.append(_line("mm_job_last_success_age_seconds", round(age), job=name))
                    except ValueError:
                        pass
            out.append("# HELP mm_job_last_ok 배치 작업 마지막 결과 (1=성공, 0=실패)\n# TYPE mm_job_last_ok gauge")
            out += [_line("mm_job_last_ok", 1 if s == "OK" else 0, job=n) for n, _, s in
                    q("SELECT name, last_finished, last_status FROM job_locks WHERE last_status <> ''")]
    except Exception:                                   # DB가 죽어도 지표는 나와야 감시가 알린다
        out.append("# HELP mm_db_up DB 연결 (1=정상)\n# TYPE mm_db_up gauge\nmm_db_up 0")
    return out


def render() -> str:
    lines = ["# HELP mm_up 앱 응답 (1)\n# TYPE mm_up gauge\nmm_up 1",
             "# HELP mm_process_start_time_seconds 프로세스 시작 시각\n# TYPE mm_process_start_time_seconds gauge\n"
             + _line("mm_process_start_time_seconds", int(_started)),
             _line("mm_info", 1, version=config.APP_VERSION, db="postgresql" if db.is_pg() else "sqlite",
                   erp=config.SAP_MODE, demo="1" if config.DEMO else "0")]
    lines.insert(2, "# HELP mm_info 구성\n# TYPE mm_info gauge")
    with _lock:
        lines.append("# HELP mm_http_requests_total 요청 수 (화면·방식·결과)\n# TYPE mm_http_requests_total counter")
        lines += [_line("mm_http_requests_total", n, endpoint=ep, method=m, status=s) for (ep, m, s), n in sorted(_count.items())]
        lines.append("# HELP mm_http_request_seconds 응답 시간\n# TYPE mm_http_request_seconds histogram")
        for ep, h in sorted(_hist.items()):
            for i, b in enumerate(BUCKETS):
                lines.append(_line("mm_http_request_seconds_bucket", h[i], endpoint=ep, le=b))
            lines.append(_line("mm_http_request_seconds_bucket", h[len(BUCKETS)], endpoint=ep, le="+Inf"))
            lines.append(_line("mm_http_request_seconds_sum", round(h[-1], 4), endpoint=ep))
            lines.append(_line("mm_http_request_seconds_count", h[len(BUCKETS)], endpoint=ep))
    lines += _business()
    return "\n".join(lines) + "\n"
