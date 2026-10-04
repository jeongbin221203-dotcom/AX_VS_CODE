"""4차 점검(필기·1급·새 문제)에서 확인된 문제들이 다시 생기지 않는지."""
import io
import re

import openpyxl

from core import content, written
from core import exam as ex
from core import formula as fx


def test_formula_fixes():
    b = fx.Book([fx.Sheet('S', {(r, 2): r * 10 for r in range(1, 11)})])
    assert fx.evaluate_text('=SUM(OFFSET(B10,0,0,-3,1))', b, 'S', 1, 5) == 270        # 음수 높이 = 위로
    assert fx.evaluate_text('=SUM(SIGN({-2,0,5}))', b, 'S', 1, 5) == 0
    assert fx.format_value(0.395833, 'h시간 m분') == '9시간 30분'                      # 글자 사이에 있어도 분
    assert fx.has_reference(fx.parse('=ROW()-1'))


def test_check_accepts_common_forms():
    ok = lambda pid, a: content.check(content.get(pid), a)['ok']   # noqa: E731
    assert ok('basic-101', '=ROWS($H$2:H2)') and ok('basic-101', '=ROW()-1')
    assert ok('array-101', '{=FREQUENCY($D$2:$D$13,$J$2:$J$5)}')                     # 범위에 한 번에 넣는 배열 수식
    assert ok('lookup-103', '=SUM(OFFSET(B2,COUNT(B2:B13)-1,0,-3,1))')
    assert not ok('date-104', '=MAX(0,(C2-B2)*24-9)*$K$1*1.5')                       # 반올림 지시
    r = content.check(content.get('lookup-102'), '=SUM(G2:INDEX(G:G,I2+1))')
    assert '아직 계산하지 못합니다' in r['error']


def test_exam_values_must_be_numbers():
    e = ex.get('c1-02')
    wb = openpyxl.load_workbook(io.BytesIO(ex.problem_workbook(e)))
    ws = wb['기타작업-3']
    for c, v in zip('ABCDEF', [1006, '윤서아', '전기차', '6', '74000', 444000]):    # 대여일수·일요금이 글자
        ws[f'{c}9'] = v
    bio = io.BytesIO()
    wb.save(bio)
    ctx = ex.Ctx(e, bio.getvalue(), 'x.xlsx')
    chk = next(c for t in e['tasks'] for c in t['checks'] if c['label'].startswith('② 등록 결과'))
    ok, msgs = ex.check_values(ctx, '기타작업-3', chk)
    assert not ok and '텍스트' in msgs[0]


def test_written_mock_paper_and_submit(client):
    ids = written.build_mock('c2', seed=3)
    assert written.valid_paper('c2', ids) and not written.valid_paper('c2', ids[:-1] + ids[:1])
    page = client.get('/written/mock/c2?q=' + ','.join(ids)).get_data(as_text=True)
    assert re.search(r'name="qids" value="([^"]+)"', page).group(1) == ','.join(ids)        # 이어 풀기
    comp = [written.get(i)['topic'] for i in ids if i.startswith('pc-')]
    assert comp.count('운영체제') >= comp.count('최신 기술')                                  # 문제 은행 비율
    h = {'X-CSRF-Token': client.csrf}
    assert client.post('/written/api/answer', json={'id': ids[0], 'picked': True}, headers=h).status_code == 400
    r = client.post('/written/mock/c2/submit', data={'_csrf': client.csrf, 'qids': ','.join([ids[0]] * 5 + ['db-0001']),
                                                     'seconds': '-99999'})
    res = client.get(r.headers['Location']).get_data(as_text=True)
    assert '-' not in re.search(r'평균 [\d.]+점', res).group(0)
    data = client.get('/api/backup').json['data']
    assert len(data['written_attempts']) == 1 and data['written_results'][0]['seconds'] == 0
    assert client.get('/written/practice?subject=database&level=c2').status_code == 302
