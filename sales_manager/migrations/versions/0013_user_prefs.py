"""사용자별 화면 설정 (사이드바 메뉴 순서·즐겨찾기, core/prefs.py)

  user_prefs : (user_id, key) → value(JSON)

Revision ID: 0013_user_prefs
Revises: 0012_returns_advances_orders
Create Date: 2026-10-03
"""
from ddl import big, run

revision = "0013_user_prefs"
down_revision = "0012_returns_advances_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(f"""
    CREATE TABLE user_prefs (
        user_id  {big()} NOT NULL,
        key      TEXT NOT NULL,
        value    TEXT NOT NULL,
        PRIMARY KEY (user_id, key)
    )""")


def downgrade() -> None:
    run("DROP TABLE user_prefs")
