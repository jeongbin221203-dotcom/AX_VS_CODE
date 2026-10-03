"""Flask 블루프린트 (화면 계층). 요청을 읽어 core에 넘기고 결과를 그릴 뿐, 판정은 core/services가 한다.

  dashboard     대시보드 (KPI · 30일 추이 · 분류별 재고금액 · 미달 목록)
  materials     자재 마스터 목록 · 신규 등록 · 수정/사용중지
  transactions  입고 · 출고 · 실사조정 등록
  stock         재고 현황 조회 · 필터 · 엑셀
  history       거래 이력 조회 · 엑셀 · 삭제
  documents     증빙(세금계산서·전자세금계산서) 이미지 등록 · 조회 · 거래 연결
  periods       월 마감 · 해제
  sap           SAP 전송 현황 · 지금 전송 · 재전송
  data_admin    일괄 업로드 · 백업 · 샘플
  auth          로그인 · 로그아웃 · 최초 설정 · 비밀번호 변경
  purchase      구매요청 → 결재 → 발주 → 입고 · 3자 대조
  approvals     결재함 (금액이 큰 실사 조정)
  reports       수불부 · 재고 평가(이동평균·선입선출) · 재고 대사
  admin         사용자·데이터 범위 · 플랜트·창고 · 배치 · 감사로그
  prefs         내 화면 설정 (사이드바 메뉴 순서·즐겨찾기)
"""
from flask import Flask

from .helpers import load_context


def register_blueprints(app: Flask) -> None:
    from . import (admin, approvals, auth, dashboard, data_admin, documents, history, materials, periods, prefs,
                   purchase, reports, sap, statements, stock, transactions)

    for module in (auth, dashboard, materials, transactions, statements, stock, history, documents, purchase, approvals,
                   reports, periods, sap, data_admin, admin, prefs):
        app.register_blueprint(module.bp)
    app.before_request(load_context)
