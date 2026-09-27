"""기준선(v2): 권한·결재·감사·ERP 대기열·증빙까지의 스키마

SQLite 는 전환 전(Streamlit 판) DB 도 데이터 손실 없이 이 버전으로 올린다.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-27
"""
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from schema_v2_upgrade import upgrade_pg, upgrade_sqlite
    bind = op.get_bind()
    raw = bind.connection.driver_connection
    if bind.dialect.name == "sqlite":
        upgrade_sqlite(raw)
    else:
        upgrade_pg(raw)


def downgrade() -> None:
    raise RuntimeError("기준선은 되돌릴 수 없습니다. 백업에서 복구하세요.")
