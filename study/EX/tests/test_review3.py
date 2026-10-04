"""학습자·컴활 전문가·엑셀 전문가 점검(2026-10-04)에서 확인된 문제들이 다시 생기지 않는지."""
import io

import pytest

from core import analyze, content, xlsx
from core import exam as ex
from core import formula as fx


@pytest.fixture
def book():
    return fx.Book([fx.Sheet('S', {(1, 1): 70, (2, 1): 85, (3, 1): 92, (4, 1): 60, (5, 1): 78,
                                   (1, 3): 'x', (2, 3): 'y', (3, 3): 'z', (1, 4): 3, (2, 4): 1, (3, 4): 2})])


def v(text, book):
    r = fx.evaluate_text(text, book, 'S', 20, 10)
    return fx.display(r) if isinstance(r, fx.XLErr) else r


@pytest.mark.parametrize('text, want', [          # 모두 Excel 2019 에서 확인한 값
    ('=INT(10/3*3)', 10), ('=MOD(10/3*3,10)', 0), ('=10/3*3=10', True), ('=(0.1+0.2)-0.3=0', False),
    ('=0.1+0.2=0.3', True), ('=ISNA(NA()+(1/0))', True), ('=LARGE(A1:A5,2.1)', 78), ('=ROUND(1234.5,-2.5)', 1200),
    ('=FIXED(-1234.567,-1)', '-1,230'), ('=HOUR(0.999999)', 0), ('=(-8)^(1/3)', -2), ('=TEXT(TRUE,"0")', 'TRUE'),
    ('=VLOOKUP("y",CHOOSE({1,2},C1:C3,D1:D3),2,FALSE)', 1), ('=GCD(12,18)', 6), ('=LCM(4,6)', 12),
    ('=COMBIN(5,2)', 10), ('=AGGREGATE(14,6,A1:A5,2)', 85), ('=QUARTILE.EXC(A1:A5,1)', 65),
    ('=CLEAN("a"&CHAR(7)&"b")', 'ab'), ('=ＳＵＭ(Ａ1:Ａ2)', 155), ('=SUM(A1:A5', 385),
])
def test_engine_matches_excel(book, text, want):
    assert v(text, book) == want


@pytest.mark.parametrize('text', ['=VLOOKUP(1,A1:A5)', '=MAXIFS(A1:A5,C1:C3)', '=IFERROR(1/0)', '=CHOOSE()',
                                  '=EDATE(1,10^12)', '=REPT("a",10^6)', '=RANK.AVG(A2,A1:A5)'])
def test_bad_arguments_do_not_crash(book, text):
    v(text, book)                                  # 예외 없이 엑셀 오류 값 또는 결과


def test_check_messages():
    def chk(pid, a):
        return content.check(content.get(pid), a)
    assert '인수 개수' in chk('lookup-001', '=VLOOKUP(J1,A2:H13)')['error']
    assert '순환 참조' in chk('basic-009', '=MIN(D:D)')['error']
    assert '한/영' in chk('basic-001', '=ㄴㅕㅡ(G2:G16)')['error']
    assert '#NAME?' in chk('lookup-001', '=su')['error']                      # 기록하지 않는 오류
    r = chk('lookup-001', '=VLOOKUP(J1,A2:H13,2)')
    assert not r['ok'] and any('FALSE' in n for n in r['notes'])
    r = chk('array-001', '=SUM((B2:B16=I2)*G2:G16)')
    assert not r['ok'] and any('Ctrl+Shift+Enter' in n for n in r['notes'])
    assert chk('array-001', '{=SUM((B2:B16=I2)*G2:G16)}')['ok']
    assert not chk('array-001', '=SUMIF(B2:B16,I2,G2:G16)')['ok']
    assert chk('cond-016', '{=MIN(IF(C2:C13="2반",F2:F13))}')['ok']
    assert not chk('cond-016', '=MIN(F2:F13)')['ok']
    r = chk('text-002', '=VALUE(RIGHT(A2,4))')
    assert not r['ok'] and any('문자' in n for n in r['notes'])
    assert chk('basic-001', '=SUM(G2:G16')['ok']


def test_required_functions_are_stated():
    for p in content.problems():
        text = (p.get('title', '') + p.get('prompt', '')).upper()
        for n in p.get('require') or []:
            assert n.upper() in text, (p['id'], n)


def test_exam_rejects_other_files_and_accepts_theme_and_format():
    import openpyxl
    bio = io.BytesIO()
    openpyxl.Workbook().save(bio)                  # 다른 파일(시트 이름이 하나도 없음)
    with pytest.raises(xlsx.BadFile):
        ex.grade(ex.get('c2-01'), bio.getvalue())
    assert ex.same_color('T0', 'FFFFFF', {'T0': 'FFFFFF'}) and not ex.same_color('T4', 'FFFFFF', {'T4': '4F81BD'})
    assert ex._same_fmt('#,###"원"', '#,##0"원"', [1500, 23000])          # 0 이 없는 칸이면 같은 모양
    assert not ex._same_fmt('#,###"원"', '#,##0"원"', [0, 1500])


def test_analyze_blank_amounts_and_currency():
    csv = '날짜,지역,제품,금액\n2026-01-03,서울,노트북,"₩2,400,000"\n2026-01-04,부산,모니터,\n2026-02-01,서울,키보드,90000원\n'
    sheets = analyze.read_file(io.BytesIO(csv.encode('utf-8')), 'a.csv') if hasattr(analyze, 'read_file') else None
    assert analyze._csv_value('₩2,400,000') == 2400000 and analyze._csv_value('90000원') == 90000
    assert analyze._csv_value('12.5%') == 0.125 and analyze._csv_value('천원') == '천원'
    assert analyze._agg([], 'sum', 0) == 0 and analyze._agg([], 'avg', 0) is None
    del sheets
