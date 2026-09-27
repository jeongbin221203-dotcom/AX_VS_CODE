"""회사 엑셀 양식 — 업로드 열 매핑 · 내려받기 서식 파일

  excel_forms
    direction   import(일괄 등록) | export(추출)
    entity      import: 거래처/영업기회/영업활동/매출/목표   export: 추출 항목(매출, 채권(미수) …)
    sheet_name  읽거나 채울 시트 (비우면 첫 시트)
    header_row  머리글 행 번호(1부터). 회사 양식은 제목·결재란 때문에 머리글이 아래에 있는 경우가 많다
    data_start_row  (export) 데이터를 채우기 시작할 행
    column_map  JSON [{"column": "회사 머리글", "field": "시스템 항목"}] — 순서가 내보내기 열 순서
    value_map   JSON {"시스템 항목": {"회사 값": "시스템 값"}} 예) 과세구분 {"01": "과세", "02": "영세"}
    fill_down   JSON ["거래처명"] — 병합 셀처럼 비어 있으면 위 값을 이어 쓴다
    stop_words  JSON ["합계", "소계"] — 첫 매핑 열이 이 값이면 그 행은 건너뛴다
    template_key  저장소의 회사 서식 파일(.xlsx)

Revision ID: 0006_excel_forms
Revises: 0005_hr_api
Create Date: 2026-09-27
"""
from ddl import pk, run

revision = "0006_excel_forms"
down_revision = "0005_hr_api"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(f"""
    CREATE TABLE excel_forms (
        id             {pk()},
        name           TEXT NOT NULL UNIQUE,
        direction      TEXT NOT NULL,
        entity         TEXT NOT NULL,
        sheet_name     TEXT,
        header_row     INTEGER NOT NULL DEFAULT 1,
        data_start_row INTEGER,
        column_map     TEXT NOT NULL,
        value_map      TEXT,
        fill_down      TEXT,
        stop_words     TEXT,
        template_key   TEXT,
        template_name  TEXT,
        memo           TEXT,
        active         INTEGER NOT NULL DEFAULT 1,
        created_by     TEXT,
        created_at     TEXT NOT NULL,
        updated_at     TEXT NOT NULL
    )""", "CREATE INDEX idx_forms_dir ON excel_forms(direction, entity)")


def downgrade() -> None:
    run("DROP TABLE excel_forms")
