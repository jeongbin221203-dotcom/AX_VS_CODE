"""함수 사전."""
from flask import Blueprint, abort, render_template

from core import content

bp = Blueprint('functions', __name__)


@bp.route('/functions')
def index():
    funcs = content.bank()['functions']
    groups = []
    for key, name, kind, _ in content.CATEGORIES:
        if kind != 'formula':
            continue
        items = sorted([f for f in funcs if f.get('category') == key], key=lambda f: f['name'])
        if items:
            groups.append({'key': key, 'name': name, 'funcs': items})
    return render_template('functions.html', groups=groups, total=len(funcs))


@bp.route('/functions/<name>')
def detail(name):
    f = content.function_doc(name)
    if not f:
        f = next((x for x in content.bank()['functions']
                  if name.upper() in [a.upper() for a in x.get('aliases', [])]), None)
    if not f:
        abort(404)
    names = {f['name'].upper(), *[a.upper() for a in f.get('aliases', [])]}
    related = [p for p in content.problems() if names & {n.upper() for n in p.get('functions', [])}]
    return render_template('function.html', f=f, related=related)
