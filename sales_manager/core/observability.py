"""운영 관측·통제 — 요청 ID · JSON 로그 · Prometheus 지표 · 준비 상태 · 읽기 전용 점검 모드

  /healthz  살아 있는지(프로세스·DB 응답). 로드밸런서 liveness 용
  /readyz   트래픽을 받아도 되는지: DB · 스키마 최신 · 파일 저장소 읽기/쓰기. 실패하면 503
  /metrics  Prometheus 지표. SALES_METRICS_ALLOW(CIDR, 기본 127.0.0.1·::1) 또는 SALES_METRICS_TOKEN(Bearer)만 허용

  요청 ID   X-Request-ID 를 받거나 새로 만들어 응답 헤더·로그에 남긴다 (프록시·앱·DB 로그를 한 줄로 잇는다)
  JSON 로그 SALES_LOG_FORMAT=json (운영 기본) — 수집기(ELK·Loki)가 바로 읽는다. 조회 조건(쿼리스트링)은 남기지 않는다
  읽기 전용 SALES_READ_ONLY=1 — 점검·DB 전환·재해 복구 중에는 조회만 허용하고 쓰기를 503 으로 막는다(워커도 멈춘다)
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import time
import uuid
from contextvars import ContextVar
from datetime import datetime

from flask import Flask, Response, abort, g, jsonify, request

_REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="-")
_RID_OK = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
READ_ONLY_ALLOWED = {"auth.login", "auth.logout", "auth.oidc_start", "auth.oidc_callback", "auth.notifications_read",
                     "auth.breakglass", "admin.maintenance"}      # 점검 모드를 끄는 화면은 열어 둔다


def read_only() -> bool:
    """점검(읽기 전용) 모드: 환경변수 SALES_READ_ONLY=1 또는 화면·명령으로 켠 회사 설정 maintenance (서버 여러 대 15초 안에 같이)."""
    if os.environ.get("SALES_READ_ONLY", "0") == "1":
        return True
    try:
        from . import company
        return bool((company.get("maintenance") or {}).get("on"))
    except Exception:                                   # noqa: BLE001 - 설정을 못 읽으면 막지 않는다
        return False


def client_ip() -> str:
    """접속한 사람의 IP. 앞단(Cloudflare·Render·사내 L7)이 넣어 주는 헤더를 SALES_CLIENT_IP_HEADER 로 지정하면 그 값을 쓴다
    (예: True-Client-IP, X-Real-IP). 지정하지 않으면 직접 연결된 주소(SALES_PROXY_FIX=1 이면 X-Forwarded-For 로 복원된 값).
    헤더는 앞단이 덮어쓰는 경우에만 지정한다 — 아니면 사용자가 IP 를 꾸밀 수 있다."""
    header = os.environ.get("SALES_CLIENT_IP_HEADER", "").strip()
    if header and request:
        value = (request.headers.get(header) or "").split(",")[0].strip()
        if value:
            return value[:64]
    return request.remote_addr or ""


def request_id() -> str:
    return _REQUEST_ID.get()


# ---------------------------------------------------------------------------
# 로그
# ---------------------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
               "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
               "request_id": getattr(record, "request_id", None) or request_id(), "host": socket.gethostname()}
        for key in ("http", "user_id"):
            if hasattr(record, key):
                out[key] = getattr(record, key)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id()
        return True


def formatter() -> logging.Formatter:
    production = os.environ.get("SALES_ENV") == "production"
    if os.environ.get("SALES_LOG_FORMAT", "json" if production else "text") == "json":
        return JsonFormatter()
    return logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s")


ACCESS = logging.getLogger("sales.access")


# ---------------------------------------------------------------------------
# Prometheus 지표 (한 프로세스에 한 번만 만든다)
# ---------------------------------------------------------------------------
_METRICS: dict = {}


def _metrics() -> dict:
    if _METRICS:
        return _METRICS
    from prometheus_client import CollectorRegistry, Counter, Histogram
    from prometheus_client.core import GaugeMetricFamily
    reg = CollectorRegistry()
    _METRICS["registry"] = reg
    _METRICS["requests"] = Counter("sales_http_requests_total", "HTTP 요청 수", ["method", "endpoint", "status"],
                                   registry=reg)
    _METRICS["latency"] = Histogram("sales_http_request_duration_seconds", "HTTP 응답 시간", ["endpoint"],
                                    buckets=(.025, .05, .1, .25, .5, 1, 2.5, 5, 10), registry=reg)
    _METRICS["errors"] = Counter("sales_http_exceptions_total", "처리되지 않은 예외", ["endpoint"], registry=reg)

    class BusinessCollector:
        """스크레이프할 때 DB 에서 읽는 업무·배치 상태 (서버 여러 대가 같은 값을 보여 준다)."""

        def collect(self):
            from . import database
            from . import sales_db as db
            up = GaugeMetricFamily("sales_db_up", "DB 응답 여부")
            try:
                db._scalar("SELECT 1")
                up.add_metric([], 1)
            except Exception:   # noqa: BLE001
                up.add_metric([], 0)
                yield up
                return
            yield up
            schema = GaugeMetricFamily("sales_schema_current", "스키마가 최신이면 1")
            schema.add_metric([], 1 if database.current_revision() == database.head_revision() else 0)
            yield schema
            jobs = GaugeMetricFamily("sales_jobs", "작업 큐 상태별 건수", labels=["status"])
            for r in db._df("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").itertuples():
                jobs.add_metric([r.status], int(r.n))
            yield jobs
            now = datetime.now()
            oldest = db._one("SELECT MIN(run_after) AS t FROM jobs WHERE status='대기' AND run_after <= ?",
                             [now.strftime("%Y-%m-%d %H:%M:%S")])
            age = GaugeMetricFamily("sales_jobs_oldest_waiting_seconds", "실행 시각이 지났는데 대기 중인 가장 오래된 작업")
            age.add_metric([], _age(oldest and oldest["t"], now))
            yield age
            beat = db._one("SELECT MAX(last_run_at) AS t FROM scheduler_state WHERE name LIKE 'worker:%'")
            hb = GaugeMetricFamily("sales_worker_heartbeat_age_seconds", "가장 최근 워커 신호 이후 경과 (워커가 모두 멈추면 커진다)")
            hb.add_metric([], _age(beat and beat["t"], now, missing=1e9))
            yield hb
            erp = GaugeMetricFamily("sales_erp_documents", "ERP 전송 상태별 매출 건수", labels=["status"])
            for r in db._df("SELECT COALESCE(erp_status, '미전송') AS s, COUNT(*) AS n FROM sales "
                            "WHERE status <> '취소' GROUP BY COALESCE(erp_status, '미전송')").itertuples():
                erp.add_metric([r.s], int(r.n))
            yield erp
            late = GaugeMetricFamily("sales_approval_steps_overdue", "결재 기한이 지난 대기 단계")
            late.add_metric([], int(db._scalar("SELECT COUNT(*) FROM approval_steps WHERE status='대기' AND due_at < ?",
                                               [now.strftime("%Y-%m-%d %H:%M:%S")])))
            yield late
            fails = GaugeMetricFamily("sales_login_failures_15m", "최근 15분 로그인 실패")
            since = datetime.fromtimestamp(time.time() - 900).strftime("%Y-%m-%d %H:%M:%S")
            fails.add_metric([], int(db._scalar("SELECT COUNT(*) FROM audit_log WHERE action IN "
                                                "('로그인실패','API인증실패') AND ts >= ?", [since])))
            yield fails
            ro = GaugeMetricFamily("sales_read_only", "읽기 전용 점검 모드")
            ro.add_metric([], 1 if read_only() else 0)
            yield ro

    reg.register(BusinessCollector())
    return _METRICS


def _age(ts: str | None, now: datetime, missing: float = 0.0) -> float:
    if not ts:
        return missing
    try:
        return max(0.0, (now - datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")).total_seconds())
    except ValueError:
        return missing


def _metrics_allowed() -> bool:
    token = os.environ.get("SALES_METRICS_TOKEN")
    if token and secrets.compare_digest(request.headers.get("Authorization", ""), f"Bearer {token}"):
        return True
    allow = os.environ.get("SALES_METRICS_ALLOW", "127.0.0.1/32,::1/128")
    try:
        ip = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        return False
    return any(ip in ipaddress.ip_network(c.strip(), strict=False) for c in allow.split(",") if c.strip())


# ---------------------------------------------------------------------------
# 준비 상태
# ---------------------------------------------------------------------------
def readiness() -> tuple[bool, dict]:
    from . import database
    from . import sales_db as db
    from .storage import get_storage
    checks: dict[str, str] = {}
    try:
        db._scalar("SELECT 1")
        checks["db"] = "ok"
        current, head = database.current_revision(), database.head_revision()
        checks["schema"] = "ok" if current == head else f"outdated ({current} → {head})"
    except Exception as exc:   # noqa: BLE001
        checks["db"] = f"error: {type(exc).__name__}"
    try:
        store = get_storage()
        key = f"health/{socket.gethostname()}-{os.getpid()}.txt"
        payload = uuid.uuid4().hex.encode()
        store.put(key, payload, "text/plain")
        ok = store.get(key) == payload
        store.delete(key)
        checks["storage"] = "ok" if ok else "error: mismatch"
    except Exception as exc:   # noqa: BLE001
        checks["storage"] = f"error: {type(exc).__name__}"
    checks["read_only"] = "on" if read_only() else "off"
    ready = all(v == "ok" for k, v in checks.items() if k != "read_only")
    return ready, checks


# ---------------------------------------------------------------------------
# Flask 연결
# ---------------------------------------------------------------------------
def init_app(app: Flask) -> None:
    @app.before_request
    def _start():
        incoming = request.headers.get("X-Request-ID", "")
        rid = incoming if _RID_OK.match(incoming) else uuid.uuid4().hex
        g.request_id = rid
        g._rid_token = _REQUEST_ID.set(rid)
        g._t0 = time.perf_counter()
        if read_only() and request.method in WRITE_METHODS and request.endpoint not in READ_ONLY_ALLOWED:
            return _read_only_response()
        return None

    @app.after_request
    def _finish(response: Response):
        endpoint = request.endpoint or "unknown"
        elapsed = time.perf_counter() - getattr(g, "_t0", time.perf_counter())
        response.headers["X-Request-ID"] = getattr(g, "request_id", "-")
        if endpoint not in ("metrics", "static"):
            m = _metrics()
            m["requests"].labels(request.method, endpoint, str(response.status_code)).inc()
            m["latency"].labels(endpoint).observe(elapsed)
            user = getattr(g, "user", None)
            ACCESS.info("%s %s %s %.0fms", request.method, request.path, response.status_code, elapsed * 1000,
                        extra={"http": {"method": request.method, "path": request.path,
                                        "status": response.status_code, "ms": round(elapsed * 1000, 1),
                                        "ip": client_ip(), "endpoint": endpoint},
                               "user_id": user.get("id") if isinstance(user, dict) else None})
        return response

    @app.teardown_request
    def _teardown(exc):
        if exc is not None:
            _metrics()["errors"].labels(request.endpoint or "unknown").inc()
        token = g.pop("_rid_token", None)
        if token is not None:
            _REQUEST_ID.reset(token)

    @app.route("/readyz")
    def readyz():
        ready, checks = readiness()
        return jsonify(status="ready" if ready else "not_ready", checks=checks), (200 if ready else 503)

    @app.route("/metrics")
    def metrics():
        if not _metrics_allowed():
            abort(403, "지표는 모니터링 서버에서만 볼 수 있습니다.")
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
        return Response(generate_latest(_metrics()["registry"]), mimetype=CONTENT_TYPE_LATEST)

    app.jinja_env.globals["READ_ONLY"] = read_only


def _read_only_response():
    message = "시스템 점검 중(읽기 전용)입니다. 조회만 할 수 있고 저장·변경은 점검이 끝난 뒤 다시 시도하세요."
    if request.blueprint == "api":
        res = jsonify(error={"code": "read_only", "message": message})
        res.status_code = 503
        res.headers["Retry-After"] = "600"
        return res
    from flask import render_template
    return render_template("error.html", code=503, message=message, active=None, title="점검 중"), 503
