from core import content


def test_bank_validates():
    b = content.bank()
    errs = []
    for pid in b['order']:
        errs += content.validate_problem(b['problems'][pid])
    assert errs == []
    assert len(b['order']) >= 100


def test_every_category_has_problems():
    have = {p['category'] for p in content.problems()}
    assert {k for k, *_ in content.CATEGORIES} <= have


def test_fill_detects_missing_dollar():
    p = content.get('basic-003')
    assert content.check(p, '=G2/SUM($G$2:$G$16)')['ok']
    res = content.check(p, '=G2/SUM(G2:G16)')
    assert not res['ok']
    assert any('$' in n for n in res['notes'])


def test_value_only_answer_rejected():
    p = content.get('basic-001')
    total = content.expected(p)[0][2]
    res = content.check(p, f'={total}')
    assert not res['ok']


def test_require_function():
    p = content.get('basic-001')
    res = content.check(p, '=G2+G3+G4+G5+G6+G7+G8+G9+G10+G11+G12+G13+G14+G15+G16')
    assert not res['ok'] and any('SUM' in n for n in res['notes'])


def test_try_mode_does_not_reveal():
    res = content.check(content.get('basic-001'), '=SUM(G2:G16)', reveal=False)
    assert 'ok' not in res and 'expected' not in res and res['cells'][0]['addr'] == 'J2'


def test_function_docs_cover_problem_functions():
    for p in content.problems():
        for n in p.get('functions', []):
            assert content.function_doc(n), (p['id'], n)


def test_grid_marks_targets():
    g = content.grid(content.get('basic-003'))
    flat = [c for row in g['rows'] for c in row]
    assert sum(c['target'] for c in flat) == 15
    assert any(c['text'] == '2026-01-05' for c in flat)
