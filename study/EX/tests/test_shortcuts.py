"""엑셀 단축키 모음: 자료 형식, 화면, 메뉴."""
import re

from views import shortcuts


def test_shortcut_data_valid():
    gs = shortcuts.groups()
    assert shortcuts.validate(gs) == []
    assert len(gs) >= 6 and sum(len(g['items']) for g in gs) >= 100
    assert any(it.get('hot') for g in gs for it in g['items'])


def test_validate_catches_problems():
    bad = [{'key': 'a', 'name': 'A', 'items': [{'k': ['Ctrl', 'C'], 'd': '복사'}, {'k': ['Ctrl', 'C'], 'd': '또'},
                                               {'k': [], 'd': '빈 키'}, {'k': ['F1'], 'd': ''}]}]
    msgs = ' '.join(shortcuts.validate(bad))
    assert '두 번' in msgs and '키가 비었습니다' in msgs and '설명이 없습니다' in msgs


def test_page_and_menu(client):
    r = client.get('/shortcuts')
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'id="sc-search"' in html and '<kbd>Ctrl</kbd>' in html
    assert html.count('data-sc-group') == len(shortcuts.groups())
    assert 'href="/shortcuts"' in client.get('/').get_data(as_text=True)
    # 순차 키(Alt, H, O)는 쉼표로, 동시 키(Ctrl+C)는 +로 구분해 보여 준다
    assert re.search(r'<kbd>Alt</kbd><span class="sc-join" aria-hidden="true">,</span>', html)
    assert re.search(r'<kbd>Ctrl</kbd><span class="sc-join" aria-hidden="true">\+</span>', html)


def test_known_shortcuts_present():
    flat = {(tuple(it['k']), it['d']) for g in shortcuts.groups() for it in g['items']}
    keys = {k for k, _ in flat}
    for must in (('Ctrl', '1'), ('Alt', '='), ('Ctrl', 'Shift', 'Enter'), ('F4',), ('Alt', 'Enter'), ('Ctrl', 'Enter'),
                 ('Shift', 'F11'), ('Alt', 'F11'), ('Alt', 'F8'), ('Ctrl', ';')):
        assert must in keys, must
