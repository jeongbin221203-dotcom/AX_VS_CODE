"""기준선: 버전 관리를 들이기 전까지 core/db.py 의 SCHEMA·MIGRATIONS 로 만든 구조 (2026-10-03).
db.init_db 가 이 구조를 먼저 만들므로 여기서는 할 일이 없다. 되돌릴 수 없다."""

revision = "0001"
down_revision = None
message = "기준선 (자재·거래·창고·구매·결재·증빙·거래처·BOM·생산 투입·알림)"


def upgrade(conn):
    pass
