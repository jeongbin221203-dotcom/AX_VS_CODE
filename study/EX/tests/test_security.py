"""웹 계층 검토(2026-10-04)에서 확인된 문제들이 다시 생기지 않는지."""
import io
import json
import threading
import zipfile

import openpyxl
import pytest

from core import library, xlsx


def _big_xlsx(rows=300_000, cols=4):
    """압축은 작지만 풀면 큰 시트(이전에는 50초·470MB 를 썼다)."""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('xl/workbook.xml', '<workbook/>')
        head = f'<worksheet><dimension ref="A1:D{rows}"/><sheetData>'
        body = ''.join(f'<row r="{r}"><c r="A{r}"><v>1</v></c></row>' for r in range(1, 2000))
        z.writestr('xl/worksheets/sheet1.xml', head + body * 50 + '</sheetData></worksheet>')
    return bio.getvalue()


def test_zip_bomb_rejected_quickly():
    with pytest.raises(xlsx.BadFile):
        xlsx.check_zip(_big_xlsx())


def test_library_index_survives_concurrent_adds(tmp_path):
    wb = openpyxl.Workbook()
    bio = io.BytesIO()
    wb.save(bio)
    data = bio.getvalue()
    errors = []

    def add(i):
        try:
            library.add_pair(tmp_path, f't{i}', 'g', ('p.xlsx', data), ('a.xlsx', data), owner=f'u{i}', limit=30)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=add, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(library.load_index(tmp_path)) == 20
    (tmp_path / 'index.json').write_text('{broken', encoding='utf-8')
    assert library.load_index(tmp_path) == []


def test_chart_json_cannot_close_script(client):
    data = '분류,금액\n</script><img src=x>,1\nb,2\na,3\n'.encode('utf-8')
    r = client.post('/analyze/upload', data={'_csrf': client.csrf, 'file': (io.BytesIO(data), 'x.csv')})
    page = client.get(r.headers['Location']).get_data(as_text=True)
    script = page.split('id="chart-data">', 1)[1].split('</script>', 1)[0]
    assert '<img' not in page.split('</script>', 2)[-1] or '<\\/script>' in script
    json.loads(script)                                    # 차트 자료가 깨지지 않음


def test_bad_inputs_are_not_500(client):
    assert client.get('/me/%ED%95%9C').status_code == 404
    r = client.post('/track', data={'_csrf': '한', 'track': 'c1'})
    assert r.status_code == 400
    h = {'X-CSRF-Token': client.csrf}
    assert client.post('/api/check', json=[1], headers=h).status_code == 400
    assert client.post('/api/star', json='x', headers=h).status_code == 400
    r = client.post('/api/check', json={'pid': 'basic-001', 'answer': '=' + '(' * 900 + '1', 'mode': 'try'}, headers=h)
    assert r.status_code == 200 and 'error' in r.json


def test_restore_rejects_bad_rows_and_results_stay_readable(client):
    h = {'X-CSRF-Token': client.csrf}
    bad = {'version': 1, 'attempts': [{'pid': ['x'], 'category': 'basic', 'ok': 1}],
           'exam_results': [{'exam': 'c2-01', 'score': None, 'total': 100, 'passed': 0, 'detail': '{}'},
                            {'exam': 'c2-01', 'score': 1, 'total': 100, 'passed': 0, 'detail': '{"x": 1}'}],
           'build_results': [{'task': 'sales', 'score': 1, 'total': 11, 'detail': '[]'}]}
    r = client.post('/api/restore', json=bad, headers=h)
    assert r.status_code == 200 and r.json['restored'] == {'attempts': 0, 'stars': 0, 'build_results': 0,
                                                           'exam_results': 0, 'written_attempts': 0,
                                                           'written_results': 0}


def test_csv_long_field_and_control_chars(client):
    long = ('a,b\n' + 'x' * 200_000 + ',1\n').encode('utf-8')
    r = client.post('/analyze/upload', data={'_csrf': client.csrf, 'file': (io.BytesIO(long), 'l.csv')},
                    follow_redirects=True)
    assert r.status_code == 200 and 'CSV' in r.get_data(as_text=True)
    ctrl = '분류,금액\n"a\x01b",1\nb,2\na,3\n'.encode('utf-8')
    r = client.post('/analyze/upload', data={'_csrf': client.csrf, 'file': (io.BytesIO(ctrl), 'c.csv')})
    assert client.get(r.headers['Location'] + '/export').status_code == 200
