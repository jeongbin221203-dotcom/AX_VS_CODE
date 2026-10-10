"""정적 HTML 버전: 자료가 원본(../EX/content)과 같은지, 페이지가 가리키는 파일이 모두 있는지, 외부 주소를 쓰지 않는지."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import build  # noqa: E402

PAGES = ['index.html', 'written.html', 'learn.html', 'functions.html', 'shortcuts.html', 'exam.html', 'build.html', 'analyze.html', 'practice.html']


def load(name):
    text = (HERE / 'data' / f'{name}.js').read_text(encoding='utf-8')
    body = text.split(f'EXDATA.{name} = ', 1)[1].rstrip().rstrip(';')
    return json.loads(body)


def test_data_matches_source():
    """원본 자료를 고치고 build.py 를 다시 안 돌렸다면 여기서 걸린다."""
    from core import content, written
    w = load('written')
    assert [q['id'] for q in w['questions']] == [q['id'] for q in written.questions()]
    assert {q['id']: q['answer'] for q in w['questions']} == {q['id']: q['answer'] for q in written.questions()}
    assert [f['name'] for f in load('functions')['items']] == [f['name'] for f in content.bank()['functions']]
    assert [p['id'] for p in load('problems')['items']] == [p['id'] for p in content.problems()]
    src = json.loads((HERE.parent / 'EX' / 'content' / 'shortcuts.json').read_text(encoding='utf-8'))
    assert load('shortcuts') == src


def test_build_is_idempotent(tmp_path):
    before = {p.name: p.read_bytes() for p in (HERE / 'data').glob('*.js')}
    build.build()
    after = {p.name: p.read_bytes() for p in (HERE / 'data').glob('*.js')}
    assert before == after


def test_python_bundle_is_current():
    """py/bundle.zip 은 ../EX/core·content 에서 만든 것 — 원본을 고치고 build.py 를 안 돌렸다면 걸린다."""
    import zipfile
    z = zipfile.ZipFile(HERE / 'py' / 'bundle.zip')
    for n in build.CORE_FILES:
        assert z.read(f'app/core/{n}.py') == (build.SRC / 'core' / f'{n}.py').read_bytes(), n
    assert z.read('app/core/bridge.py') == (HERE / 'py' / 'bridge.py').read_bytes()
    for p in (build.SRC / 'content' / 'exams').glob('*.json'):
        assert z.read(f'app/content/exams/{p.name}') == p.read_bytes(), p.name
    assert 'lib/openpyxl/__init__.py' in z.namelist()


@pytest.mark.skipif(shutil.which('node') is None, reason='node 없음')
def test_python_engine_end_to_end():
    """Pyodide 로 번들을 풀어 모의고사·실습·분석·수식 채점을 실제로 돌린다(수십 초)."""
    r = subprocess.run(['node', str(HERE / 'tests' / 'py_smoke.js')], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-500:]


def test_pages_reference_existing_files_and_no_external_urls():
    for page in PAGES:
        html = (HERE / page).read_text(encoding='utf-8')
        for ref in re.findall(r'(?:src|href)="([^"#]+)"', html):
            if ref.startswith('data:'):          # 내장 아이콘
                continue
            if ref.endswith(('.html',)):
                assert (HERE / ref).exists(), (page, ref)
            elif not ref.startswith(('http', '//')):
                assert (HERE / ref).exists(), (page, ref)
            else:
                pytest.fail(f'{page}: 외부 주소 {ref}')
    for f in list((HERE / 'js').glob('*.js')) + list((HERE / 'css').glob('*.css')):
        text = f.read_text(encoding='utf-8')
        text = text.replace('https://license.korcham.net/co/examguide02Sub.do?cd=0103&mm=21&num=2941771', '')   # 공식 예제 안내 링크(이동용, 요청 아님)
        text = text.replace('http://www.w3.org/2000/svg', '')      # SVG 이름공간 식별자일 뿐 네트워크 주소가 아님
        assert not re.search(r'https?://', text), f.name


def test_each_page_loads_what_its_script_needs():
    need = {'practice.html': set(), 'exam.html': set(), 'build.html': set(), 'analyze.html': set(), 'index.html': {'written', 'problems', 'functions', 'shortcuts'}, 'written.html': {'written'},
            'learn.html': {'problems', 'functions'}, 'functions.html': {'functions'}, 'shortcuts.html': {'shortcuts'}}
    for page, names in need.items():
        html = (HERE / page).read_text(encoding='utf-8')
        loaded = set(re.findall(r'data/(\w+)\.js', html))
        assert names <= loaded, (page, names - loaded)
        assert html.index('js/common.js') < html.index(f'js/{page.replace(".html", "")}.js')
        if page in ('exam.html', 'build.html', 'analyze.html', 'practice.html', 'learn.html'):      # 파이썬 엔진을 쓰는 화면은 engine.js 를 먼저
            assert html.index('py/engine.js') < html.index(f'js/{page.replace(".html", "")}.js')


@pytest.mark.skipif(shutil.which('node') is None, reason='node 없음')
def test_javascript_syntax():
    for f in sorted((HERE / 'js').glob('*.js')) + [HERE / 'py' / 'engine.js']:
        r = subprocess.run(['node', '--check', str(f)], capture_output=True, text=True)
        assert r.returncode == 0, f'{f.name}: {r.stderr[:300]}'


def test_written_bank_is_playable():
    w = load('written')
    for q in w['questions']:
        assert len(q['options']) == 4 and 0 <= q['answer'] <= 3 and q['explain']
    # 모의고사를 만들 수 있을 만큼 과목마다 20문제 이상(급별)
    for lv, spec in w['levels'].items():
        for s in spec['subjects']:
            assert sum(1 for q in w['questions'] if q['subject'] == s and lv in q['levels']) >= w['perSubject']


@pytest.mark.skipif(shutil.which('node') is None, reason='node 없음')
def test_all_formula_problems_grade_correctly_in_browser_python():
    """수식 문제 177개: 정답·대체 정답은 정답, 흔한 오답(wrong)은 오답 — 브라우저 파이썬(3.12)에서도 서버와 같아야 한다."""
    r = subprocess.run(['node', str(HERE / 'tests' / 'py_formula_all.js')], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=600)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-500:]
