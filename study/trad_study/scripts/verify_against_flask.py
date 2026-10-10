"""HTML 버전의 데이터가 Flask 버전(../trad)과 같은지 대조한다. 데이터·이미지를 다시 만든 뒤 실행하세요.

  1. 문항 840개의 글(회차·과목·번호·제목·본문·공통자료·쪽)과 공식 정답이 catalog.sqlite3 와 같은가
  2. 이미지가 Flask 서버가 렌더링한 그림과 픽셀까지 같은가 (문항·공통 지문 이미지 전부)
  3. 시험지 PDF 14개가 원본과 바이트까지 같고, validation.json 의 SHA-256 과 맞는가

사용: python scripts/verify_against_flask.py      (Flask 버전의 의존성: PyMuPDF·Flask, 그리고 Pillow)
"""
import hashlib
import io
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
FLASK = ROOT.parent / 'trad'
sys.path.insert(0, str(FLASK))


def load_js(name, prefix):
    text = (ROOT / 'data' / name).read_text(encoding='utf-8')
    assert text.startswith(prefix)
    return json.loads(text[len(prefix):].rstrip().rstrip(';'))


def main():
    import sqlite3
    from app import create_app
    db = sqlite3.connect(FLASK / 'data' / 'catalog.sqlite3')
    db.row_factory = sqlite3.Row
    rows = {r['id']: dict(r) for r in db.execute('SELECT * FROM questions')}
    catalog = load_js('catalog.js', 'window.TRADE_CATALOG=')
    answers = load_js('answers.js', 'window.TRADE_ANSWERS=')
    problems = []

    assert len(catalog) == len(rows) == 840 == len(answers), (len(catalog), len(rows), len(answers))
    for q in catalog:
        r = rows[q['id']]
        for key in ('round', 'subject', 'number', 'title', 'body', 'context', 'page'):
            if q[key] != r[key]:
                problems.append(f'{q["id"]} {key}')
        if answers[str(q['id'])] != r['correct']:
            problems.append(f'{q["id"]} 정답')
        if len(q['images']) != len(json.loads(r['parts'])) or len(q['context_images']) != len(json.loads(r['context_parts'])):
            problems.append(f'{q["id"]} 이미지 개수')
    print(f'1. 문항 {len(catalog)}개·정답 {len(answers)}개 대조 — 불일치 {len(problems)}')

    client = create_app({'TESTING': True}).test_client()
    refs = 0
    before = len(problems)
    for q in catalog:
        for kind, urls in (('body', q['images']), ('context', q['context_images'])):
            for i, url in enumerate(urls):
                flask_img = Image.open(io.BytesIO(client.get(f'/question-image/{q["id"]}/{kind}/{i}.png').data)).convert('RGB')
                html_img = Image.open(ROOT / url).convert('RGB')
                refs += 1
                if flask_img.size != html_img.size or flask_img.tobytes() != html_img.tobytes():
                    problems.append(f'{q["id"]} {kind}[{i}] 그림')
    print(f'2. 이미지 {refs}개 참조를 Flask 렌더링과 픽셀 대조 — 불일치 {len(problems) - before}')

    before = len(problems)
    validation = json.loads((ROOT / 'data' / 'validation.json').read_text(encoding='utf-8'))
    for item in validation:
        n = item['round']
        if hashlib.sha256((ROOT / 'data' / 'sources' / f'{n}_questions.pdf').read_bytes()).hexdigest() != item['sha256']:
            problems.append(f'{n}회 문제 PDF 해시')
    for pdf in sorted((FLASK / 'data' / 'sources').glob('*.pdf')):
        if pdf.read_bytes() != (ROOT / 'data' / 'sources' / pdf.name).read_bytes():
            problems.append(f'{pdf.name} 다름')
    print(f'3. PDF 14개·validation.json 해시 대조 — 불일치 {len(problems) - before}')

    print('결과:', '모두 일치' if not problems else problems[:10])
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()
