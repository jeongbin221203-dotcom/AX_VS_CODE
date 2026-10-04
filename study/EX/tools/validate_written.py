"""필기 문제 검사: python tools/validate_written.py  (형식·중복·보기·정답 위치·보기 길이 치우침)."""
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import written  # noqa: E402


def main():
    errs = []
    seen = {}
    stats = collections.defaultdict(lambda: {'n': 0, 'pos': collections.Counter(), 'longest': 0,
                                             'topics': collections.Counter(), 'c1only': 0})
    for f in sorted((ROOT / 'content' / 'written').glob('*.json')):
        try:
            doc = json.loads(f.read_text(encoding='utf-8'))
        except ValueError as e:
            errs.append(f'{f.name}: JSON 오류 {e}')
            continue
        subj = doc.get('subject')
        if subj not in written.SUBJECTS:
            errs.append(f'{f.name}: subject {subj!r}')
            continue
        for q in doc.get('questions', []):
            qid = q.get('id', '?')
            errs += [f'{f.name} {qid}: {e}' for e in written.validate_question(q, subj)]
            if qid in seen:
                errs.append(f'{qid}: 번호 중복({seen[qid]}, {f.name})')
            seen[qid] = f.name
            text = (q.get('q') or '').strip()
            st = stats[subj]
            st['n'] += 1
            if isinstance(q.get('answer'), int):
                st['pos'][q['answer']] += 1
            opts = q.get('options') or []
            if len(opts) == 4 and isinstance(q.get('answer'), int):
                lens = [len(o) for o in opts]
                if lens[q['answer']] == max(lens) and lens.count(max(lens)) == 1:
                    st['longest'] += 1
            st['topics'][q.get('topic')] += 1
            if q.get('levels') == ['c1']:
                st['c1only'] += 1
            del text
    for subj, st in stats.items():
        n = max(1, st['n'])
        print(f"{written.SUBJECTS[subj]['name']}: {st['n']}문항 · 1급 전용 {st['c1only']} · 정답 위치 "
              f"{dict(sorted(st['pos'].items()))} · 가장 긴 보기가 정답 {round(100 * st['longest'] / n)}%")
        print('   주제:', dict(st['topics']))
        if st['n'] >= 20 and st['longest'] / n > 0.35:
            errs.append(f"{subj}: 가장 긴 보기가 정답인 비율이 {round(100 * st['longest'] / n)}% — 보기 길이를 고르게")
    print(f'오류 {len(errs)} 건')
    for e in errs[:80]:
        print(' -', e)
    return 1 if errs else 0


if __name__ == '__main__':
    sys.exit(main())
