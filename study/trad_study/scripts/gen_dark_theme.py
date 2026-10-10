"""어두운 테마 스타일을 style.css 끝에 만들어 붙인다. 밝은 테마 값은 그대로 두고, 색이 든 규칙마다 light-dark(밝은색, 어두운색)을 덧붙인다.

  python scripts/gen_dark_theme.py [style.css 경로 ...]      (경로를 주지 않으면 이 폴더의 static/style.css)
  python scripts/gen_dark_theme.py --check [경로 ...]        (어두운 구간이 최신인지만 검사, 아니면 종료 코드 1)

- 어두운 색은 밝은 색의 밝기를 뒤집어 계산한다: 배경·테두리(밝은 색)는 어둡게, 글자색(어두운 색)은 밝게. 진한 배경 위의 흰 글자·초록 버튼 같은 값은 그대로 둔다.
- 결과는 /* 어두운 테마 … */ 표시 구간에 쓴다. 다시 실행하면 그 구간만 새로 만든다(직접 고치지 말고 이 파일의 EXTRA·OVERRIDES 를 고칠 것).
- 브라우저가 light-dark() 를 모르면(@supports 로 거름) 어두운 테마만 빠지고 밝은 테마는 그대로 보인다.
- 전환: 기본은 운영체제 설정(color-scheme: light dark), <html data-theme="dark|light"> 로 강제.
"""
import colorsys
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = '/* ==== 어두운 테마 (scripts/gen_dark_theme.py 가 만듦 — 직접 고치지 말 것) ==== */'
END = '/* ==== 어두운 테마 끝 ==== */'

TEXT_PROPS = {'color', 'caret-color', 'text-decoration-color'}
BG_PROPS = {'background', 'background-color'}
# 포커스 링(outline)은 어두운 배경에서도 잘 보이는 지금 색을 그대로 둔다
BORDER_PROPS = {'border', 'border-color', 'column-rule'} | {f'border-{s}' for s in ('top', 'right', 'bottom', 'left')} \
    | {f'border-{s}-color' for s in ('top', 'right', 'bottom', 'left')}

# 자동 계산이 마음에 들지 않는 값만 여기서 고친다: {(선택자, 속성): '어두운 색'}  — 밝은 값은 그대로.
OVERRIDES = {}
# 규칙을 통째로 덧붙이고 싶을 때(어두운 테마에서만 적용)
EXTRA = ''


def parse(css, i=0, end=None):
    """최상위 항목 목록: ('rule', 선택자, 본문) | ('media', 조건, 항목 목록) | ('raw', 원문)"""
    end = len(css) if end is None else end
    items = []
    while i < end:
        while i < end and css[i].isspace():
            i += 1
        if i >= end:
            break
        if css.startswith('/*', i):
            j = css.index('*/', i) + 2
            i = j
            continue
        j = css.index('{', i)
        prelude = css[i:j].strip()
        depth, k = 1, j + 1
        while depth:
            c = css[k]
            depth += (c == '{') - (c == '}')
            k += 1
        body = css[j + 1:k - 1]
        if prelude.startswith('@media'):
            items.append(('media', prelude, parse(body)))
        elif prelude.startswith('@'):
            items.append(('raw', prelude + '{' + body + '}'))
        else:
            items.append(('rule', prelude, body))
        i = k
    return items


def hex_to_rgb(h):
    h = h.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def to_hex(rgb):
    return '#%02x%02x%02x' % tuple(max(0, min(255, round(v * 255))) for v in rgb)


def dark_of(hexcolor, role):
    """밝은 색 → 어두운 테마의 색. 바꾸지 않을 때는 None."""
    h, l, s = colorsys.rgb_to_hls(*hex_to_rgb(hexcolor))
    if role in ('bg', 'border') and s < 0.12:
        h, s = 0.46, 0.14                    # 순백·회색 계열은 이 앱의 초록빛으로 물들여 페이지 배경과 어울리게
    if role in ('bg', 'border'):
        if l < 0.60:
            return None                      # 이미 진한 배경(초록 버튼 등)은 그대로
        return to_hex(colorsys.hls_to_rgb(h, min(0.30, 0.07 + (1 - l) * 1.10), s * 0.75))
    if role == 'text':
        if l > 0.55:
            return None                      # 진한 배경 위의 밝은 글자는 그대로
        return to_hex(colorsys.hls_to_rgb(h, max(0.60, min(0.92, 1 - l * 0.78)), s * 0.85))
    return None


def role_of(prop):
    if prop in TEXT_PROPS:
        return 'text'
    if prop in BG_PROPS:
        return 'bg'
    if prop in BORDER_PROPS:
        return 'border'
    return None


def root_vars(items):
    out = {}
    for it in items:
        if it[0] == 'rule' and it[1] == ':root':
            for m in re.finditer(r'(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,6})\b', it[2]):
                out.setdefault(m.group(1), m.group(2))
    return out


NAMED = {'white': '#ffffff'}            # 이 스타일 시트가 쓰는 색 이름
TOKEN = re.compile(r'#[0-9a-fA-F]{3,8}\b|var\(--[\w-]+\)|(?<![\w#-])white(?![\w(-])')


LONGHAND = {'border': 'border-color', 'background': 'background-color'} | {f'border-{s}': f'border-{s}-color' for s in ('top', 'right', 'bottom', 'left')}


def convert_rule(selector, body, variables):
    """색이 든 선언을 같은 순서로 모두 내보낸다(값이 같더라도) — 건너뛰면 뒤 규칙이 앞 규칙을 덮는 순서가 바뀌기 때문.
    border·background 같은 약식 표기는 색 부분만 개별 속성(border-color 등)으로 내보내 다른 속성을 되돌리지 않게 한다."""
    decls = []
    for part in [p for p in body.split(';') if p.strip()]:
        prop, _, value = part.partition(':')
        prop, value = prop.strip(), value.strip()
        role = role_of(prop)
        if role is None:
            continue
        tokens = TOKEN.findall(value)
        if prop in LONGHAND:
            if len(tokens) != 1:
                continue                          # 색이 없거나(border:0 등) 여러 개면 건드리지 않음
            out_prop, value = LONGHAND[prop], tokens[0]
        else:
            out_prop = prop

        def repl(m):
            tok = m.group(0)
            if tok.startswith('var('):
                light = variables.get(tok[4:-1])
                if light is None:
                    return tok
            elif tok in NAMED:
                light = NAMED[tok]
            else:
                if len(tok) not in (4, 7):          # 알파가 든 8자리 등은 건드리지 않음
                    return tok
                light = tok
            dark = OVERRIDES.get((selector, prop)) or dark_of(light, role)
            return tok if dark is None else f'light-dark({tok},{dark})'

        decls.append(f'{out_prop}:{TOKEN.sub(repl, value)}')
    return decls or None


def emit(items, variables, out):
    for it in items:
        if it[0] == 'rule':
            decls = convert_rule(it[1], it[2], variables)
            if decls:
                out.append(f'{it[1]}{{{";".join(decls)}}}')
        elif it[0] == 'media':
            inner = []
            emit(it[2], variables, inner)
            if inner:
                out.append(f'{it[1]}{{{"".join(inner)}}}')


def build(css_text):
    if START in css_text:
        css_text = css_text[:css_text.index(START)].rstrip() + '\n'
    items = parse(css_text)
    variables = root_vars(items)
    rules = []
    emit(items, variables, rules)
    block = [START, '@supports (color:light-dark(#000,#fff)){',
             ':root{color-scheme:light dark}', ':root[data-theme=light]{color-scheme:light}', ':root[data-theme=dark]{color-scheme:dark}']
    block += rules
    if EXTRA:
        block.append(EXTRA)
    block += ['}', END]
    return css_text.rstrip('\n') + '\n' + '\n'.join(block) + '\n', len(rules)


def check(paths):
    """밝은 CSS 를 고치고 어두운 구간을 다시 만들지 않았으면 실패(1)를 돌려준다."""
    stale = [str(p) for p in paths if build(Path(p).read_text(encoding='utf-8'))[0] != Path(p).read_text(encoding='utf-8')]
    print('어두운 테마가 최신이 아님: ' + ', '.join(stale) if stale else '어두운 테마가 최신입니다.')
    return 1 if stale else 0


def main(paths):
    for p in paths:
        p = Path(p)
        text = p.read_text(encoding='utf-8')
        new, n = build(text)
        p.write_text(new, encoding='utf-8', newline='\n')
        print(f'{p}: 어두운 테마 규칙 {n}개, {len(new) - len(text.split(START)[0]) if START in text else len(new) - len(text)}바이트 추가')


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if a != '--check']
    targets = args or [ROOT / 'static' / 'style.css']
    if '--check' in sys.argv:
        sys.exit(check(targets))
    main(targets)
