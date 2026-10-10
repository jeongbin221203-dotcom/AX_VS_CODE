"""Rebuild the read-only question catalog from the 14 supplied source PDFs."""
import hashlib
import json
import re
import sqlite3
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
SUBJECTS = ['무역규범', '무역결제', '무역계약', '무역영어']
MARK = re.compile(r'^(\d{1,2})(?:\.(?!\d)|\s+(?=[가-힣]))')
GROUP = re.compile(r'^\[(\d{1,2})\s*[-∼～~]\s*(\d{1,2})\]')


def lines_of(page):
    lines = [l for b in page.get_text('dict')['blocks'] for l in b.get('lines', [])]
    return sorted(lines, key=lambda l: (l['bbox'][1], l['bbox'][0]))


def regions(doc, start, end):
    parts, texts = [], []
    for p in range(start[0], min(end[0] + 1, len(doc))):
        top = start[1] if p == start[0] else 45
        bottom = end[1] if p == end[0] else doc[p].rect.height - 40
        if bottom - top < 8:
            continue
        lines = [l for l in lines_of(doc[p]) if l['bbox'][1] >= top - 1 and l['bbox'][3] < bottom]
        if not lines:
            continue
        # Keep original vector text, underlines and table borders in the render.
        y0 = max(0, min(l['bbox'][1] for l in lines) - 8)
        y1 = min(bottom, max(l['bbox'][3] for l in lines) + 14)
        box = [42, round(y0, 2), round(doc[p].rect.width - 42, 2), round(y1, 2)]
        parts.append({'page': p, 'box': box})
        texts.append(doc[p].get_text(clip=fitz.Rect(box), sort=True).strip())
    return parts, '\n'.join(texts)


def parse_round(n):
    path = ROOT / 'data' / 'sources' / f'{n}_questions.pdf'
    doc = fitz.open(path)
    starts, headings, groups = [], [], []
    for pi, page in enumerate(doc):
        for line in lines_of(page):
            text = ''.join(s['text'] for s in line['spans']).strip()
            pos = (pi, line['bbox'][1])
            normalized = re.sub(r'\s', '', text)
            if normalized in SUBJECTS:
                headings.append((pos, SUBJECTS.index(normalized)))
            group = GROUP.match(text)
            if group:
                groups.append((pos, int(group[1]), int(group[2])))
            m = MARK.match(text)
            if m and line['bbox'][0] <= 63 and int(m[1]) == len(starts) % 30 + 1:
                starts.append((pos, text))
    assert len(starts) == 120, (n, 'question boundaries', len(starts))
    assert len(headings) == 4, (n, headings)
    keys = fitz.open(ROOT / 'data' / 'sources' / f'{n}_answers.pdf')
    words = keys[0].get_text('words')
    choices = [w for w in words if w[4] in '①②③④' and len(w[4]) == 1]
    cols = sorted({round(w[0], 1) for w in choices})
    assert len(cols) == 4 and len(choices) == 120
    answers = []
    for x in cols:
        col = sorted([w for w in choices if round(w[0], 1) == x], key=lambda w: w[1])
        assert len(col) == 30
        # Verify adjacent printed question numbers, not only answer glyph count.
        for qn, w in enumerate(col, 1):
            neighbor = [z for z in words if z[4] == str(qn) and abs(z[1] - w[1]) < 3 and 0 < w[0] - z[0] < 85]
            assert neighbor, (n, x, qn, 'answer alignment')
        answers += ['①②③④'.index(w[4]) + 1 for w in col]
    shared = {}
    for pos, lo, hi in groups:
        section = max((s for p, s in headings if p <= pos), default=0)
        first = starts[section * 30 + lo - 1][0]
        assert pos < first and 1 <= lo <= hi <= 30, (n, pos, lo, hi)
        part, text = regions(doc, pos, first)
        for qn in range(lo, hi + 1):
            shared[section * 30 + qn - 1] = (part, text)
    rows = []
    for i, (pos, title) in enumerate(starts):
        end = starts[i+1][0] if i+1 < len(starts) else (len(doc), 0)
        for event in [p for p, _ in headings] + [p for p, _, _ in groups]:
            if pos < event < end:
                end = event
        body, text = regions(doc, pos, end)
        context, context_text = shared.get(i, ([], ''))
        assert body and all(c in text for c in '①②③④'), (n, i+1, 'missing choices')
        rows.append((n*1000+i, n, i//30, i%30+1, title, text, context_text,
                     json.dumps(body), json.dumps(context), answers[i], pos[0]+1))
    return rows, {'round': n, 'questions': len(rows), 'answers': len(answers),
                  'shared_groups': len(groups), 'pdf_pages': len(doc),
                  'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    output = ROOT / 'data' / 'catalog.sqlite3'
    db = sqlite3.connect(output)
    db.executescript('''
        DROP TABLE IF EXISTS questions;
        CREATE TABLE questions (
          id INTEGER PRIMARY KEY, round INTEGER NOT NULL, subject INTEGER NOT NULL,
          number INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
          context TEXT NOT NULL, parts TEXT NOT NULL, context_parts TEXT NOT NULL,
          correct INTEGER NOT NULL CHECK(correct BETWEEN 1 AND 4), page INTEGER NOT NULL,
          UNIQUE(round,subject,number));
    ''')
    reports = []
    for n in range(59, 66):
        rows, report = parse_round(n)
        db.executemany('INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?,?,?)', rows)
        reports.append(report)
        print(report)
    db.commit()
    assert db.execute('SELECT COUNT(*) FROM questions').fetchone()[0] == 840
    db.close()
    (ROOT / 'data' / 'validation.json').write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
