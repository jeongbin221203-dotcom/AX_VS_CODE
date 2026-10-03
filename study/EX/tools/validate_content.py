"""문제 은행 검사: 형식, 정답 계산, 다른 정답·오답 예시 채점, 함수 사전 연결."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import content, formula  # noqa: E402


def main():
    b = content.bank()
    errs = []
    for pid in b['order']:
        errs += content.validate_problem(b['problems'][pid])
    names = {n.upper() for f in b['functions'] for n in [f['name'], *f.get('aliases', [])]}
    for f in b['functions']:
        if f['name'].upper() not in formula.FUNCS:
            errs.append(f"함수 사전 {f['name']}: 계산기가 지원하지 않음")
    for pid in b['order']:
        for n in b['problems'][pid].get('functions', []):
            if names and n.upper() not in names:
                errs.append(f'{pid}: 함수 사전에 {n} 없음')
    by_cat = {}
    for pid in b['order']:
        p = b['problems'][pid]
        by_cat[p['category']] = by_cat.get(p['category'], 0) + 1
    print('문제', len(b['order']), '개 |', ', '.join(f'{k} {v}' for k, v in by_cat.items()), '| 함수 사전', len(b['functions']))
    for e in errs:
        print(' -', e)
    print('오류', len(errs), '건')
    return 1 if errs else 0


if __name__ == '__main__':
    sys.exit(main())
