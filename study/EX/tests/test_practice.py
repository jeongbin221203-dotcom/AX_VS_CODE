"""실기 실습: 두 파일 비교 채점·교재 폴더 가져오기·공식 예제 받기(네트워크 대신 가짜 zip).

출판사·대한상공회의소 파일은 저장소에 넣을 수 없어, 우리 모의고사의 문제 파일과 Excel 로 만든 정답 파일을
'실습/정답' 짝으로 쓴다.
"""
import io
import zipfile
from pathlib import Path

import openpyxl
import pytest

from core import compare, exam, library, official

FIX = Path(__file__).parent / 'fixtures'


@pytest.fixture(scope='module')
def pair():
    e = exam.get('c2-01')
    return exam.problem_workbook(e), (FIX / 'c2-01_answer_excel.xlsx').read_bytes()


def test_compare_answer_full_problem_zero(pair):
    src, ans = pair
    assert compare.grade(src, ans, ans)['score'] == 100
    assert compare.grade(src, ans, src)['score'] == 0


def test_compare_partial_and_messages(pair):
    src, ans = pair
    wb = openpyxl.load_workbook(io.BytesIO(ans))
    ws = wb['계산작업']
    for r in range(4, 12):
        ws[f'F{r}'] = '우수'                                  # 수식 대신 값
    wb['기본작업-3'].conditional_formatting = type(wb['기본작업-3'].conditional_formatting)()
    bio = io.BytesIO()
    wb.save(bio)
    res = compare.grade(src, ans, bio.getvalue())
    assert 0 < res['score'] < 100
    items = {(s['name'], i['label']): i for s in res['sheets'] for i in s['items']}
    f = next(v for (sh, lab), v in items.items() if sh == '계산작업' and lab.startswith(('수식 결과', '셀 내용')) and 'F4' in lab)
    assert not f['ok'] and '수식이 아니라 값' in f['msgs'][0] and '정답 수식' in f['hint']
    cf = next(v for (sh, lab), v in items.items() if sh == '기본작업-3' and '조건부 서식' in lab)
    assert not cf['ok'] and cf['hint'].startswith('규칙: =')


def _make_folder(root, src, ans):
    (root / '교재' / '실습').mkdir(parents=True)
    (root / '교재' / '정답').mkdir(parents=True)
    (root / '교재' / '실습' / '제01회 모의.xlsx').write_bytes(src)
    (root / '교재' / '정답' / '제1회 모의(정답).xlsx').write_bytes(ans)
    (root / '교재' / '실습' / '자료.txt').write_text('a,b', encoding='utf-8')
    (root / '교재' / '실습' / '짝없음.xlsx').write_bytes(src)


def test_library_scan_and_import(tmp_path, pair):
    src, ans = pair
    _make_folder(tmp_path / 'in', src, ans)
    found = library.scan(tmp_path / 'in')
    assert len(found) == 1 and found[0]['category'] == '모의고사' and [e.name for e in found[0]['extras']] == ['자료.txt']
    assert library.import_folder(tmp_path / 'in', tmp_path / 'lib') == 1
    it = library.load_index(tmp_path / 'lib')[0]
    assert library.grade(tmp_path / 'lib', it, ans)['score'] == 100


def _fake_zip(src, ans):
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, 'w') as z:
        for kind in ('A', 'B'):
            base = f'예제/2급/2급 엑셀 {kind}형/'
            z.writestr(base + f'2급 {kind}형 문제.pdf', b'%PDF-1.4')
            z.writestr(base + f'2급 {kind}형 소스.xlsx', src)
            z.writestr(base + f'2급 {kind}형 정답.xlsm', ans)
    return bio.getvalue()


def test_official_download_and_grade(tmp_path, pair, monkeypatch):
    src, ans = pair
    data = _fake_zip(src, ans)
    monkeypatch.setattr(official, 'SETS', [s for s in official.SETS if s[0] in ('c2-A', 'c2-B')])
    monkeypatch.setattr(official, '_fetch', lambda pkg: data)
    assert official.download(tmp_path) == ['c2-A', 'c2-B']
    s = official.get(tmp_path, 'c2-A')
    assert s['gradable'] and s['meta']['files']['pdf'] == '2급 A형 문제.pdf'
    assert official.self_check(tmp_path, 'c2-A') == (100, 0)


def test_practice_pages(client, app, tmp_path, pair):
    src, ans = pair
    _make_folder(tmp_path / 'in', src, ans)
    assert client.get('/practice/').status_code == 200
    r = client.post('/practice/library/import', data={'_csrf': client.csrf, 'folder': str(tmp_path / 'in')})
    assert '1%EC%8C%8D' in r.headers['Location'] or '1쌍' in r.headers['Location']
    it = library.load_index(Path(app.config['DATA_DIR']) / 'library')[0]
    page = client.get(f"/practice/lib/{it['id']}").get_data(as_text=True)
    assert '채점 항목' in page and '조건부 서식' in page and '피벗 테이블 보고서를 작성하시오' in page
    r = client.post(f"/practice/lib/{it['id']}/submit",
                    data={'_csrf': client.csrf, 'file': (io.BytesIO(ans), '내답안.xlsx')})
    page = client.get(r.headers['Location']).get_data(as_text=True)
    assert '100' in page
    assert client.get(f"/practice/lib/{it['id']}/file/{it['practice']}").status_code == 200
    assert client.get(f"/practice/lib/{it['id']}/file/..%2Fapp.py").status_code == 404
    r = client.post('/practice/library/import', data={'_csrf': client.csrf, 'folder': str(tmp_path / 'none')},
                    follow_redirects=True)
    assert '찾을 수 없습니다' in r.get_data(as_text=True)


def test_describe_generates_exam_style_text(pair):
    from core import describe
    src, ans = pair
    res = describe.describe(src, ans)
    text = '\n'.join(t['text'] + ' ' + ' '.join(t.get('items', [])) for s in res for t in s['tasks'])
    assert "행에 '지점', 열에 '제품분류', 값에 '금액'의 합계를 배치하시오" in text
    assert "'지점'이 '서울' 이고 '판매량'이 300 이상인 행 전체" in text
    assert "'부서'를 기준으로" in text and '부분합' in text
    assert "'평균' 계열의 차트 종류를 '꺾은선형'으로 변경하시오" in text
    assert '▶ IF, AVERAGE 함수 사용' in text or '▶ AVERAGE, IF 함수 사용' in text
    assert "이름을 '저자'로 정의하시오" in text


def test_korean_particles():
    from core.describe import j
    assert j('지점', '이/가') == '지점이' and j('부서', '이/가') == '부서가'
    assert j("'꺾은선형'", '으로/로') == "'꺾은선형'으로" and j("'수량'", '으로/로') == "'수량'으로"
    assert j("'아래쪽'", '으로/로') == "'아래쪽'으로" and j("'파일'", '으로/로') == "'파일'로"


def test_public_server_blocks_copyrighted_imports(tmp_path):
    from app import create_app
    app = create_app({'DATA_DIR': str(tmp_path), 'DATABASE': str(tmp_path / 'ex.db'), 'SECRET_KEY': 't',
                      'PUBLIC': True, 'TESTING_NO_CSRF': True})
    c = app.test_client()
    assert c.post('/practice/official/fetch').status_code == 403
    assert c.post('/practice/library/import', data={'folder': str(tmp_path)}).status_code == 403
    page = c.get('/practice/').get_data(as_text=True)
    assert '공개 서버에서는' in page and '공개 시연 서버' in page
    assert c.get('/healthz').json == {'ok': True}


def _csrf(c):
    c.get('/practice/')
    with c.session_transaction() as s:
        return s['csrf']


def test_add_pair_local(client, app, pair):
    src, ans = pair
    r = client.post('/practice/library/add', data={
        '_csrf': client.csrf, 'title': '내 모의 1회', 'group': '2급 실기',
        'practice': (io.BytesIO(src), '모의1회.xlsx'), 'answer': (io.BytesIO(ans), '모의1회(정답).xlsx'),
        'extras': (io.BytesIO(b'a,b'), '자료.txt')})
    assert r.status_code == 302 and '/practice/lib/' in r.headers['Location']
    page = client.get(r.headers['Location']).get_data(as_text=True)
    assert '내 모의 1회' in page and '자료.txt' in page and '피벗 테이블 보고서를 작성하시오' in page
    bad = client.post('/practice/library/add', data={'_csrf': client.csrf, 'practice': (io.BytesIO(b'x'), 'a.txt'),
                                                     'answer': (io.BytesIO(ans), 'b.xlsx')}, follow_redirects=True)
    assert '엑셀 파일' in bad.get_data(as_text=True)


def test_add_pair_public_is_private_to_browser(tmp_path, pair):
    from app import create_app
    src, ans = pair
    app = create_app({'DATA_DIR': str(tmp_path), 'DATABASE': str(tmp_path / 'ex.db'), 'SECRET_KEY': 't', 'PUBLIC': True})
    me, other = app.test_client(), app.test_client()
    tok = _csrf(me)
    r = me.post('/practice/library/add', data={'_csrf': tok, 'title': '비공개 확인',
                                               'practice': (io.BytesIO(src), 'p.xlsx'), 'answer': (io.BytesIO(ans), 'a.xlsx')})
    url = r.headers['Location']
    assert me.get(url).status_code == 200 and '비공개 확인' in me.get('/practice/').get_data(as_text=True)
    _csrf(other)
    assert other.get(url).status_code == 404
    assert '비공개 확인' not in other.get('/practice/').get_data(as_text=True)
    assert other.get(url + '/file/p.xlsx').status_code == 404


def test_vault_roundtrip_and_wrong_key(tmp_path):
    from core import vault
    src = tmp_path / 'src'
    (src / 'library' / 'a1').mkdir(parents=True)
    (src / 'library' / 'a1' / 'x.xlsx').write_bytes(b'hello')
    (src / 'official' / 'c2-A').mkdir(parents=True)
    (src / 'official' / 'c2-A' / 'manifest.json').write_text('{}')
    (src / 'ex.db').write_bytes(b'not packed')
    key = vault.new_key()
    blob = vault.pack(src, key)
    assert b'hello' not in blob
    out = tmp_path / 'out'
    vault.unpack(blob, key, out)
    assert (out / 'library' / 'a1' / 'x.xlsx').read_bytes() == b'hello' and not (out / 'ex.db').exists()
    with pytest.raises(ValueError):
        vault.unpack(blob, vault.new_key(), out)
    vp = tmp_path / 'vault.bin'
    vp.write_bytes(blob)
    assert vault.restore_on_start(vp, key, tmp_path / 'r') > 0
    assert vault.restore_on_start(vp, key, tmp_path / 'r') == 0          # 같은 보관 파일이면 다시 풀지 않음


def test_owner_link_reveals_private_library_on_public_server(tmp_path, pair):
    from app import create_app
    src, ans = pair
    _make_folder(tmp_path / 'in', src, ans)
    library.import_folder(tmp_path / 'in', tmp_path / 'data' / 'library')
    app = create_app({'DATA_DIR': str(tmp_path / 'data'), 'DATABASE': str(tmp_path / 'ex.db'), 'SECRET_KEY': 't',
                      'PUBLIC': True, 'OWNER_TOKEN': 'a' * 40})
    iid = library.load_index(tmp_path / 'data' / 'library')[0]['id']
    c = app.test_client()
    assert c.get(f'/practice/lib/{iid}').status_code == 404
    assert c.get('/me/' + 'b' * 40).status_code == 404
    r = c.get('/me/' + 'a' * 40)
    assert r.status_code == 302
    assert c.get(f'/practice/lib/{iid}').status_code == 200
    assert '제01회 모의' in c.get('/practice/').get_data(as_text=True)
    assert app.test_client().get(f'/practice/lib/{iid}').status_code == 404       # 다른 기기는 여전히 못 봄
