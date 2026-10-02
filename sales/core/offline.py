"""인터넷이 끊겨도 쓸 수 있는지 — 설정된 외부 연결이 사내망인지, 끊기면 무엇이 멈추는지.

  화면(CSS·JS·차트·글꼴)은 모두 이 서버에서 내려주므로 인터넷이 없어도 열린다.
  서버가 밖으로 나가는 연결만 아래 표로 점검한다. 사내망(사설 IP)이면 인터넷 장애와 무관하다.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

# (이름, 환경변수, 끊기면 생기는 일, 끊겨도 데이터가 안전한 이유)
CHECKS = [
    ("DB (PostgreSQL)", "SALES_DATABASE_URL", "모든 화면이 멈춤", "사내 DB 서버 사용 권장"),
    ("파일 저장소 (S3)", "SALES_S3_ENDPOINT", "증빙·업로드 열기/올리기 실패 (다른 업무는 동작)",
     "사내 MinIO·NAS(SALES_STORAGE=local) 권장"),
    ("사내 로그인 (OIDC)", "SALES_OIDC_METADATA_URL", "새 로그인 불가 (로그인한 사용자는 계속 사용)",
     "비상 계정(SALES_BREAKGLASS_USERS)으로 로그인"),
    ("ERP (REST)", "SALES_ERP_REST_URL", "ERP 전송 지연", "전송 대기열에 남았다가 연결되면 자동 재전송"),
    ("ERP (SAP OData)", "SALES_SAP_BASE_URL", "ERP 전송 지연", "전송 대기열에 남았다가 연결되면 자동 재전송"),
    ("인사 연동", "SALES_HR_SOURCE", "인사 정보 갱신 지연", "다음 배치에서 다시 시도, 기존 계정은 그대로"),
    ("메일 (SMTP)", "SALES_SMTP_HOST", "메일 알림 지연", "화면 알림함에는 남고, 메일은 재시도"),
    ("웹훅 알림", "SALES_NOTIFY_WEBHOOK_URL", "메신저 알림 지연", "화면 알림함에는 남고, 웹훅은 재시도"),
]


def _host(value: str) -> str:
    if "://" not in value:
        return value.split(":")[0].strip()
    return urlparse(value).hostname or ""


def classify(host: str) -> str:
    if not host:
        return "설정 없음"
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return "확인 불가 (이름 해석 실패)"
    ips = {ipaddress.ip_address(i[4][0]) for i in infos}
    if all(ip.is_private or ip.is_loopback or ip.is_link_local for ip in ips):
        return "사내망"
    return "외부 인터넷"


def dependencies() -> list[dict]:
    rows = []
    storage = os.environ.get("SALES_STORAGE", "local")
    for name, env, impact, safety in CHECKS:
        value = os.environ.get(env, "")
        if env == "SALES_S3_ENDPOINT" and storage == "s3" and not value:
            value = "s3.amazonaws.com"                      # 엔드포인트를 안 주면 AWS
        if env == "SALES_DATABASE_URL" and not value:
            rows.append({"연결": name, "주소": "SQLite 파일 (이 서버)", "위치": "사내망", "끊기면": impact, "대비": safety})
            continue
        if env == "SALES_HR_SOURCE" and value and "://" not in value:
            rows.append({"연결": name, "주소": "파일", "위치": "사내망", "끊기면": impact, "대비": safety})
            continue
        host = _host(value)
        rows.append({"연결": name, "주소": host or "-", "위치": classify(host) if value else "사용 안 함",
                     "끊기면": impact, "대비": safety})
    return rows


def external_count() -> int:
    return sum(1 for r in dependencies() if r["위치"] == "외부 인터넷")
