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

PAGES = ['index.html', 'written.html', 'learn.html', 'functions.html', 'shortcuts.html']


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
        assert not re.search(r'https?://', text), f.name


def test_each_page_loads_what_its_script_needs():
    need = {'index.html': {'written', 'problems', 'functions', 'shortcuts'}, 'written.html': {'written'},
            'learn.html': {'problems', 'functions'}, 'functions.html': {'functions'}, 'shortcuts.html': {'shortcuts'}}
    for page, names in need.items():
        html = (HERE / page).read_text(encoding='utf-8')
        loaded = set(re.findall(r'data/(\w+)\.js', html))
        assert names <= loaded, (page, names - loaded)
        assert html.index('js/common.js') < html.index(f'js/{page.replace(".html", "")}.js')


@pytest.mark.skipif(shutil.which('node') is None, reason='node 없음')
def test_javascript_syntax():
    for f in sorted((HERE / 'js').glob('*.js')):
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
