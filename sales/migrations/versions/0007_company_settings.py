"""회사별 설정 — 회사 정보·결재 구간·결재 기한·단계 확률·코드 목록·개인정보 보관기간

  키-값(JSON) 한 줄씩. 값이 없으면 core/company.DEFAULTS 를 쓴다.

Revision ID: 0007_company_settings
Revises: 0006_excel_forms
Create Date: 2026-10-02
"""
from ddl import run

revision = "0007_company_settings"
down_revision = "0006_excel_forms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run("""
    CREATE TABLE company_settings (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_by TEXT,
        updated_at TEXT NOT NULL
    )""")


def downgrade() -> None:
    run("DROP TABLE company_settings")
