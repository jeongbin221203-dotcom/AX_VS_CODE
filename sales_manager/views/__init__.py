"""Flask 블루프린트 (화면 계층).

  auth     로그인 · 로그아웃 · 최초 설정
  reports  대시보드 · 매출예측 · 파이프라인 분석
  crm      거래처 · 영업기회 · 영업활동
  catalog  품목·거래처 특가 · 견적
  finance  매출·채권·여신 · 목표 · 결재함
  io       데이터 일괄 등록 · 추출
  api      외부 연동 REST API (/api/v1, Bearer 키)
  admin    조직·사용자 · 감사로그 · 데이터 관리
"""
from flask import Flask

from .helpers import load_context


def register_blueprints(app: Flask) -> None:
    from . import admin, api, auth, catalog, crm, dataio, finance, reports

    for module in (auth, reports, crm, catalog, finance, dataio, admin, api):
        app.register_blueprint(module.bp)
    app.before_request(load_context)
