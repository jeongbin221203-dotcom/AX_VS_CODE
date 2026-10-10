"""시험지 PDF 의 글자 층에서 빠진 띄어쓰기를 글자 위치(간격)로 되살린다.

문제 문장 줄은 PDF 에 공백 문자가 들어 있지 않아('대외무역법령상무역거래의대상…') 글만 뽑으면 붙어 나온다.
그러나 글자 위치를 보면 글자끼리는 간격이 0 이하이고 띄어 쓴 곳은 5pt 이상 벌어져 있다.
공백 문자가 이미 있는 곳 61,418쌍은 모두 1.6pt 이상이었으므로 기준(GAP)을 1.5pt 로 둔다.

기존 글(줄바꿈·이미 있는 공백)은 그대로 두고, 빠진 곳에 공백 하나만 끼워 넣는다. 공백 외 글자는 바뀌지 않는다.
"""
import collections

import fitz

GAP = 1.5          # pt


def line_gaps(doc, parts):
    """PDF 줄마다 {공백을 뺀 글자들: [글자별로 '앞 글자와 GAP 보다 벌어졌는가']}"""
    lines = collections.defaultdict(list)
    for p in parts:
        for b in doc[p['page']].get_text('rawdict', clip=fitz.Rect(p['box']))['blocks']:
            if b['type'] != 0:
                continue
            for l in b['lines']:
                chars, flags, prev = [], [], None
                for s in l['spans']:
                    for c in s['chars']:
                        if c['c'].isspace():
                            continue
                        chars.append(c['c'])
                        flags.append(prev is not None and c['bbox'][0] - prev['bbox'][2] > GAP)
                        prev = c
                if chars:
                    lines[''.join(chars)].append(flags)
    return lines


def _flags_for(core, lines):
    """글 한 줄의 글자들에 맞는 간격 표. 표의 가로줄처럼 PDF 줄 여러 개가 글 한 줄로 합쳐진 경우는 나누어 맞춘다."""
    if core in lines:
        return lines[core][0]
    by_first = collections.defaultdict(list)
    for k in lines:
        by_first[k[0]].append(k)
    for k in by_first:
        by_first[k].sort(key=len, reverse=True)
    memo = {}

    def go(i):
        if i == len(core):
            return []
        if i in memo:
            return memo[i]
        result = None
        for k in by_first.get(core[i], []):
            if core.startswith(k, i):
                rest = go(i + len(k))
                if rest is not None:
                    flags = list(lines[k][0])
                    flags[0] = i > 0                       # 새 PDF 줄(표의 칸)이 시작되면 띄움
                    result = flags + rest
                    break
        memo[i] = result
        return result

    return go(0)


def respace(text, lines):
    """text 의 빠진 띄어쓰기를 채운다. (새 글, 짝을 찾지 못해 그대로 둔 줄 수)"""
    out_lines, unmatched = [], 0
    for line in text.split('\n'):
        core = ''.join(ch for ch in line if not ch.isspace())
        if not core:
            out_lines.append(line)
            continue
        flags = _flags_for(core, lines)
        if flags is None:
            out_lines.append(line)
            unmatched += 1
            continue
        out, k = [], 0
        for ch in line:
            if ch.isspace():
                out.append(ch)
                continue
            if flags[k] and out and not out[-1].isspace():
                out.append(' ')
            out.append(ch)
            k += 1
        out_lines.append(''.join(out))
    return '\n'.join(out_lines), unmatched
