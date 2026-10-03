"""2단계 인증(OTP) 제거 — 사용자 요청(2026-10-02). 0009 에서 추가한 users 의 OTP 열을 지운다.

Revision ID: 0010_drop_mfa
Revises: 0009_enterprise_extras
Create Date: 2026-10-02
"""
from ddl import add_columns, big, drop_columns

revision = "0010_drop_mfa"
down_revision = "0009_enterprise_extras"
branch_labels = None
depends_on = None


def upgrade() -> None:
    drop_columns("users", "totp_secret", "totp_enabled_at", "totp_last_counter", "recovery_codes")


def downgrade() -> None:
    add_columns("users", ("totp_secret", "TEXT"), ("totp_enabled_at", "TEXT"), ("totp_last_counter", big()),
                ("recovery_codes", "TEXT"))
