import datetime as dt

import pytest

from core import formula as fx


@pytest.fixture
def book():
    cells = {(1, 1): '지역', (1, 2): '금액', (1, 3): '날짜',
             (2, 1): '서울', (2, 2): 100, (2, 3): fx.date_serial(dt.date(2026, 1, 5)),
             (3, 1): '부산', (3, 2): 250, (3, 3): fx.date_serial(dt.date(2026, 2, 1)),
             (4, 1): '서울', (4, 2): 50, (4, 3): fx.date_serial(dt.date(2026, 2, 20)),
             (5, 1): '대구', (5, 3): fx.date_serial(dt.date(2026, 3, 9))}
    return fx.Book([fx.Sheet('S', cells), fx.Sheet('데이터', {(1, 1): 7, (2, 1): 3})], today=dt.date(2026, 10, 1))


CASES = [
    ('=SUM(B2:B5)', 400),
    ('=SUMIF(A2:A5,"서울",B2:B5)', 150),
    ('=SUMIFS(B2:B5,A2:A5,"<>서울")', 250),
    ('=COUNTIFS(A2:A5,"서*",B2:B5,">=60")', 1),
    ('=COUNTIF(A2:A5,"?울")', 2),
    ('=AVERAGEIF(A2:A5,"서울",B2:B5)', 75),
    ('=VLOOKUP("부산",A2:B5,2,FALSE)', 250),
    ('=INDEX(B2:B5,MATCH("부산",A2:A5,0))', 250),
    ('=XLOOKUP("서울",A2:A5,B2:B5,"없음",0,-1)', 50),
    ('=XLOOKUP("인천",A2:A5,B2:B5,"없음")', '없음'),
    ('{=SUM((A2:A5="서울")*B2:B5)}', 150),
    ('=SUMPRODUCT((A2:A5="서울")*B2:B5)', 150),
    ('{=SUM(IF(A2:A5="서울",1))}', 2),
    ('=IFERROR(VLOOKUP("x",A2:B5,2,0),"없음")', '없음'),
    ('=DSUM(A1:B5,"금액",{"지역";"서울"})', 150),
    ('=TEXT(1234.5,"#,##0")', '1,235'),
    ('=TEXT(1234,"#,##0원")', '1,234원'),
    ('=TEXT(DATE(2026,10,3),"yyyy-mm-dd (aaa)")', '2026-10-03 (토)'),
    ('=TEXT(0.256,"0.0%")', '25.6%'),
    ('=-2^2', 4),
    ('=2^3^2', 64),
    ('=ROUND(2.675,2)', 2.68),
    ('=ROUNDDOWN(-3.79,1)', -3.7),
    ('=ROUND(1234,-2)', 1200),
    ('=MOD(-7,3)', 2),
    ('=DATEDIF(DATE(2020,3,15),DATE(2026,10,3),"Y")', 6),
    ('=WEEKDAY(DATE(2026,10,3),2)', 6),
    ('=YEAR(EOMONTH(DATE(2026,2,10),0))*100+DAY(EOMONTH(DATE(2026,2,10),0))', 202628),
    ('=NETWORKDAYS(DATE(2026,10,1),DATE(2026,10,9))', 7),
    ('=TODAY()-DATE(2026,9,30)', 1),
    ('=CHOOSE(MID("900101-2234567",8,1),"남","여")', '여'),
    ('=RANK.EQ(B3,B2:B5)', 1),
    ('=LARGE(B2:B5,2)', 100),
    ('=COUNTBLANK(B2:B5)', 1),
    ('=COUNTA(A1:B5)', 9),
    ('=데이터!A1+데이터!A2', 10),
    ('=SUM(데이터!A:A)', 10),
    ('=SUMIFS(B2:B5,C2:C5,">="&DATE(2026,2,1),C2:C5,"<"&DATE(2026,3,1))', 300),
    ('=IF(AND(B2>=100,A2="서울"),"우수","보통")', '우수'),
    ('=IFS(B3>200,"A",B3>100,"B",TRUE,"C")', 'A'),
    ('=SWITCH(A3,"서울",1,"부산",2,0)', 2),
    ('=SUBSTITUTE("2026-10-03","-","/")', '2026/10/03'),
    ('=REPLACE("900101-1234567",9,6,"******")', '900101-1******'),
    ('=TEXTJOIN(",",TRUE,A2:A5)', '서울,부산,서울,대구'),
    ('=PROPER("hello world")', 'Hello World'),
    ('=TRIM("  a   b ")', 'a b'),
    ('=VALUE("1,234")', 1234),
    ('=1/0', fx.DIV0),
    ('=NOPE(1)', fx.NAME),
    ('=VLOOKUP("인천",A2:B5,2,FALSE)', fx.NA),
]


@pytest.mark.parametrize('text,want', CASES)
def test_cases(book, text, want):
    got = fx.evaluate_text(text, book, 'S', 2, 6)
    assert fx.same_value(got, want), f'{text}: {got!r}'


def test_dynamic_arrays(book):
    got = fx.evaluate_text('=FILTER(A2:B5,B2:B5>60)', book, 'S')
    assert got.rows == [['서울', 100], ['부산', 250]]
    assert fx.evaluate_text('=UNIQUE(A2:A5)', book, 'S').rows == [['서울'], ['부산'], ['대구']]
    assert fx.evaluate_text('=SORT(B2:B4,1,-1)', book, 'S').rows == [[250], [100], [50]]


def test_shift_respects_dollars():
    ast = fx.parse('=B2/SUM($B$2:$B$5)+C$1+$D3')
    assert fx.unparse(fx.shift(ast, 2, 1)) == 'C4/SUM($B$2:$B$5)+D$1+$D5'


def test_parse_errors():
    for bad in ['=1+', '=', '="abc', '=SUM(B2:B5 B3']:
        with pytest.raises(fx.FormulaError):
            fx.parse(bad)
    assert fx.unparse(fx.parse('=SUM(B2:B5')) == 'SUM(B2:B5)'          # 엑셀처럼 끝 괄호를 채움
    assert fx.unparse(fx.parse('=IF(A1>1,"a",IF(A1>0,"b","c"')) == 'IF(A1>1,"a",IF(A1>0,"b","c"))'


def test_fullwidth_and_lowercase(book):
    assert fx.evaluate_text('＝sum（b2:b5）', book, 'S') == 400


def test_formula_cells_and_cycles():
    s = fx.Sheet('S', {(1, 1): 2, (1, 2): fx.Formula('=A1*3'), (2, 1): fx.Formula('=A2+1')})
    book = fx.Book([s])
    assert s.get(1, 2) == 6
    assert isinstance(s.get(2, 1), fx.XLErr)


def test_display_general_format():
    assert fx.display(2008) == '2008'
    assert fx.display(0.1 + 0.2) == '0.3'


def test_overflow_is_num_error(book):
    assert fx.evaluate_text('=10^400', book, 'S') == fx.NUM
    assert fx.evaluate_text('=PRODUCT(10^200,10^200)', book, 'S') == fx.NUM
    assert fx.evaluate_text('=2^10', book, 'S') == 1024
    assert fx.evaluate_text('=123456789*123456789', book, 'S') == 15241578750190500  # 엑셀처럼 15자리


def test_text_format_huge_number(book):
    assert 'E+' in fx.evaluate_text('=TEXT(10^20,"#,##0")', book, 'S')


@pytest.fixture
def book2():
    cells = {(1, 1): '지역', (1, 2): '금액', (2, 1): '서울', (2, 2): 100, (3, 1): '부산', (3, 2): 250,
             (4, 1): '서울', (4, 2): 50, (5, 1): '대구', (5, 2): 300,
             (1, 5): '조건', (2, 5): fx.Formula('=B2>AVERAGE($B$2:$B$5)'),
             (1, 7): None, (2, 7): fx.Formula('=LEFT(A2,1)="서"'), (1, 9): 'B3',
             (6, 2): fx.Formula('=SUBTOTAL(9,B2:B5)'), (7, 2): fx.Formula('=SUBTOTAL(9,B2:B6)')}
    return fx.Book([fx.Sheet('S', cells), fx.Sheet('표 2', {(1, 1): 7})],
                   names={'금액열': 'S!$B$2:$B$5', '세율': '=0.1'})


@pytest.mark.parametrize('text,want', [
    ('=DSUM(A1:B5,"금액",E1:E2)', 550),            # 계산 조건: 평균보다 큰 금액
    ('=DCOUNT(A1:B5,2,G1:G2)', 2),                 # 머리글 빈 계산 조건
    ('=OFFSET(A1,2,1)', 250),
    ('=SUM(OFFSET(B2,0,0,3,1))', 400),
    ('=SUM(OFFSET(A1,1,1,COUNTA(A2:A5)))', 700),
    ('=INDIRECT("B"&3)', 250),
    ('=SUM(INDIRECT("B2:B5"))', 700),
    ('=INDIRECT(I1)', 250),
    ('=INDIRECT("\'표 2\'!A1")', 7),
    ('=INDIRECT("R3C2",FALSE)', 250),
    ('=SUM(금액열)*세율', 70),
    ('=ROW(OFFSET(A1,3,0))', 4),
    ('=INDEX(금액열,2)', 250),
    ('=없는이름+1', fx.NAME),
    ('=OFFSET(A1,-5,0)', fx.REF),
    ('=B7', 700),                                  # SUBTOTAL 은 다른 SUBTOTAL 결과를 빼고 더한다
    ('=SUBTOTAL(1,B2:B5)', 175),
])
def test_references_names_dfunc(book2, text, want):
    got = fx.evaluate_text(text, book2, 'S', 9, 9)
    assert fx.same_value(got, want), f'{text}: {got!r}'


def test_korean_function_name_parses():
    assert 'FN비고' in fx.functions_used(fx.parse('=fn비고(D4,E4)'))


def test_excel_escaped_number_formats():
    d = fx.parse_date_text('2026-10-03')
    assert fx.format_value(d, 'yyyy"년"\ m"월"\ d"일"') == '2026년 10월 3일'
    assert fx.format_value(d, 'yyyy\.mm\.dd\(aaa\)') == '2026.10.03(토)'
    assert fx.format_value(1234, '#,##0\원') == '1,234원'


# 2026-10-03 실제 Excel(COM)로 계산해 확인한 값
@pytest.fixture
def book3():
    cells = {(1, 1): 70, (2, 1): 85, (3, 1): 92, (4, 1): 60, (5, 1): 78, (1, 3): 70, (2, 3): 80, (3, 3): 90,
             (1, 5): 'abc', (2, 5): '5', (3, 5): True}
    return fx.Book([fx.Sheet('S', cells)])


@pytest.mark.parametrize('text,want', [
    ('=PMT(0.05/12,36,10000000)', -299708.971046656), ('=FV(0.03/12,24,-100000)', 2470281.77047911),
    ('=PV(0.05,10,-1000)', 7721.73492918482), ('=NPV(0.1,-10000,3000,4200,6800)', 1188.44341233522),
    ('=NPER(0.01,-100,1000)', 10.5886444594232), ('=RATE(36,-299708.931,10000000)', 0.00416665923373203),
    ('=WEEKNUM(DATE(2026,12,31),2)', 53), ('=DAYS360(DATE(2026,2,28),DATE(2026,3,31))', 30),
    ('=MROUND(2.5,0.5)', 2.5), ('=INDEX(FREQUENCY(A1:A5,C1:C3),1)', 2), ('=YEARFRAC(DATE(2026,1,15),DATE(2027,3,1),1)', 1.12328767123288),
    ('=PERCENTRANK.INC(A1:A5,80)', 0.571), ('=GEOMEAN(A1:A5)', 76.1570528491007),
    ('=SUM(E2)', 0), ('=SUM(E3)', 0), ('=COUNT(E2)', 0), ('=AVERAGE(A1,E1)', 70), ('=SUMIF(A1:A5,">70",C1)', 170),
    ('=VLOOKUP(85,A1:A5,1,)', 85), ('=TEXT(1234567,"#,##0,")', '1,235'), ('=TEXT(1.5,"[h]:mm")', '36:00'),
    ('=TEXT(TIME(13,5,0),"h:mm AM/PM")', '1:05 PM'), ('=TEXT(1,"#.##")', '1.'), ('=TEXT(12345678,"0.00E+00")', '1.23E+07'),
    ('=WEEKDAY(DATE(2026,10,3),12)', 5), ('=VALUE("10:30")', 0.4375), ('=COUNTIF(A1:A5,">20%")', 5),
    ('=TIMEVALUE("6:45 PM")', 0.78125), ('=ADDRESS(2,3,4)', 'C2'),
])
def test_matches_real_excel(book3, text, want):
    got = fx.evaluate_text(text, book3, 'S', 20, 10)
    assert fx.same_value(got, want, 1e-9), f'{text}: {got!r}'
