"""정적 HTML 버전이 쓰는 데이터 파일을 만든다. 원본은 Flask 버전(../trad)의 catalog.sqlite3 와 기출 PDF.

  data/catalog.js            문항 840개(정답 없음, 이미지 주소 포함)  → window.TRADE_CATALOG
  data/answers.js            공식 정답 840개                           → window.TRADE_ANSWERS (채점할 때만 불러옴)
  data/sources/*.pdf         원본 시험지·정답표 14개 (화면의 '원본 시험지 보기' 링크용)
  img/<해시>.webp            문항·공통 지문 이미지 (같은 그림은 한 파일로 합침)
  static/fonts/NotoSansKR-subset.woff2   화면에 쓰는 글자만 남긴 글꼴

사용: python scripts/build_data.py              (결과 파일은 저장소에 함께 올리므로 PDF·문항 DB 가 바뀔 때만 다시 실행)
      python scripts/build_data.py --font-only  (화면 글자가 바뀌어 글꼴만 다시 만들 때)
"""
import hashlib
import io
import json
import re
import shutil
import sqlite3
import sys
from pathlib import Path

import fitz
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT.parent / 'trad'
SUBJECTS = ['무역규범', '무역결제', '무역계약', '무역영어']
SCALE = 1.7                      # Flask 버전과 같은 해상도


def render_images(rows):
    """모든 문항의 본문·공통 지문 영역을 WebP 로 저장하고, 문항마다 이미지 주소 목록을 돌려준다."""
    out = ROOT / 'img'
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    docs, cache, written = {}, {}, {}
    def one(round_, part):
        key = (round_, part['page'], tuple(part['box']))
        if key not in cache:
            doc = docs.setdefault(round_, fitz.open(ROOT / 'data' / 'sources' / f'{round_}_questions.pdf'))
            png = doc[part['page']].get_pixmap(matrix=fitz.Matrix(SCALE, SCALE), clip=fitz.Rect(part['box']), alpha=False).tobytes('png')
            name = hashlib.sha1(png).hexdigest()[:16] + '.webp'
            if name not in written:
                buf = io.BytesIO()
                Image.open(io.BytesIO(png)).convert('RGB').save(buf, 'WEBP', lossless=True, quality=75, method=4)
                (out / name).write_bytes(buf.getvalue())
                written[name] = len(buf.getvalue())
            cache[key] = 'img/' + name
        return cache[key]
    result = {}
    for r in rows:
        result[r['id']] = ([one(r['round'], p) for p in json.loads(r['parts'])],
                           [one(r['round'], p) for p in json.loads(r['context_parts'])])
    for d in docs.values():
        d.close()
    return result, written


def subset_font(chars):
    """가변 글꼴(10MB)에서 쓰는 글자와 굵기 400~700 만 남겨 woff2 로 저장한다."""
    from fontTools import subset
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
    font = TTFont(SRC / 'static' / 'fonts' / 'NotoSansKR.ttf')
    keep = set(chars) | {chr(c) for c in range(0x20, 0x7F)} | set('①②③④⑤⑥⑦⑧⑨⑩·…—–‘’“”→←↗↑↓▾▸●○■□▶✓✎✦×※「」『』【】〈〉《》〜～')
    # 자주 쓰는 한글 2,350자(KS X 1001)도 함께 — 메모에 쓴 글자가 빠져 다른 글꼴로 보이는 일을 줄임
    for hi in range(0xB0, 0xC9):
        for lo in range(0xA1, 0xFF):
            try:
                keep.add(bytes([hi, lo]).decode('euc-kr'))
            except UnicodeDecodeError:
                pass
    opts = subset.Options()
    opts.flavor = 'woff2'
    opts.layout_features = ['*']
    opts.notdef_outline = True
    sub = subset.Subsetter(opts)
    sub.populate(text=''.join(sorted(keep)))
    sub.subset(font)                       # 글자를 먼저 줄이고(약 2,800자) 그다음 굵기 범위를 줄여야 빠르고 안전하다
    font = instancer.instantiateVariableFont(font, {'wght': (400, 700)})
    dest = ROOT / 'static' / 'fonts'
    dest.mkdir(parents=True, exist_ok=True)
    font.flavor = 'woff2'
    font.save(dest / 'NotoSansKR-subset.woff2')
    shutil.copy(SRC / 'static' / 'fonts' / 'OFL.txt', dest / 'OFL.txt')
    return len(keep)


def screen_text(extra=''):
    """글꼴에 남길 글자: 문항 글 + 화면 파일에 쓰인 글."""
    text = extra
    for name in ['index.html', 'static/app.js', 'static/ai.js', 'static/store.js']:
        p = ROOT / name
        if p.exists():
            text += p.read_text(encoding='utf-8')
    return text


def main():
    assert SRC.exists(), f'{SRC} 가 필요합니다 (Flask 버전 폴더).'
    if '--font-only' in sys.argv:
        items = json.loads((ROOT / 'data' / 'catalog.js').read_text(encoding='utf-8')[len('window.TRADE_CATALOG='):].rstrip().rstrip(';'))
        glyphs = subset_font(set(screen_text(''.join(x['title'] + x['body'] + x['context'] for x in items))))
        print(f'글꼴 글자 {glyphs}개 · {(ROOT / "static/fonts/NotoSansKR-subset.woff2").stat().st_size / 1e3:.0f}KB')
        return
    sources = ROOT / 'data' / 'sources'
    sources.mkdir(parents=True, exist_ok=True)
    for pdf in sorted((SRC / 'data' / 'sources').glob('*.pdf')):
        shutil.copy(pdf, sources / pdf.name)
    db = sqlite3.connect(SRC / 'data' / 'catalog.sqlite3')
    db.row_factory = sqlite3.Row
    rows = [dict(r) for r in db.execute('SELECT * FROM questions ORDER BY id')]
    assert len(rows) == 840
    images, written = render_images(rows)
    items = []
    for r in rows:
        body, context = images[r['id']]
        items.append({'id': r['id'], 'round': r['round'], 'subject': r['subject'], 'number': r['number'], 'title': r['title'],
                      'body': r['body'], 'context': r['context'], 'page': r['page'], 'subject_name': SUBJECTS[r['subject']],
                      'images': body, 'context_images': context, 'source': f'data/sources/{r["round"]}_questions.pdf#page={r["page"]}'})
    dump = lambda v: json.dumps(v, ensure_ascii=False, separators=(',', ':'))
    (ROOT / 'data' / 'catalog.js').write_text('window.TRADE_CATALOG=' + dump(items) + ';\n', encoding='utf-8')
    answers = {str(r['id']): r['correct'] for r in rows}
    assert set(answers.values()) == {1, 2, 3, 4}
    (ROOT / 'data' / 'answers.js').write_text('window.TRADE_ANSWERS=' + dump(answers) + ';\n', encoding='utf-8')
    glyphs = subset_font(set(screen_text(''.join(x['title'] + x['body'] + x['context'] for x in items))))
    total = sum(written.values())
    print(f'문항 {len(items)}개 · 이미지 {len(written)}개 {total / 1e6:.1f}MB · 글꼴 글자 {glyphs}개')
    for p in ['data/catalog.js', 'data/answers.js', 'static/fonts/NotoSansKR-subset.woff2']:
        print(f'  {p} {(ROOT / p).stat().st_size / 1e3:.0f}KB')


if __name__ == '__main__':
    main()
