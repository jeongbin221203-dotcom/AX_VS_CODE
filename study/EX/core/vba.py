"""xlsm 속 VBA 코드 읽기 — vbaProject.bin(OLE) 의 모듈 스트림을 MS-OVBA 압축 풀기로 꺼낸다.

양식 단추는 VML 그림(xl/drawings/vmlDrawing*.vml)의 Button 객체에서 글자와 연결된 매크로 이름을 읽는다.
"""
import io
import re
import struct
import zipfile

import olefile


def decompress(data):
    """MS-OVBA 2.4.1 압축 해제."""
    if not data or data[0] != 1:
        raise ValueError('VBA 압축 서명이 아닙니다')
    out = bytearray()
    pos = 1
    while pos < len(data):
        header = struct.unpack_from('<H', data, pos)[0]
        size = (header & 0x0FFF) + 3
        compressed = header & 0x8000
        chunk_end = min(pos + size, len(data))
        pos += 2
        start = len(out)
        if not compressed:
            out += data[pos:pos + 4096]
            pos += 4096
            continue
        while pos < chunk_end:
            flags = data[pos]
            pos += 1
            for bit in range(8):
                if pos >= chunk_end:
                    break
                if not flags & (1 << bit):
                    out.append(data[pos])
                    pos += 1
                    continue
                token = struct.unpack_from('<H', data, pos)[0]
                pos += 2
                diff = len(out) - start
                bits = max((diff - 1).bit_length(), 4)
                length_mask = 0xFFFF >> bits
                offset = (token >> (16 - bits)) + 1
                length = (token & length_mask) + 3
                for _ in range(length):
                    out.append(out[-offset])
    return bytes(out)


def _modules(dir_stream):
    """dir 스트림 → [(모듈 스트림 이름, 소스 시작 위치)]"""
    d = decompress(dir_stream)
    mods, pos, name, cur = [], 0, None, None
    while pos + 6 <= len(d):
        rid, size = struct.unpack_from('<HI', d, pos)
        pos += 6
        if rid == 0x0009:          # PROJECTVERSION: 크기 4 + 2바이트 더
            pos += 6
            continue
        body = d[pos:pos + size]
        pos += size
        if rid == 0x001A:          # MODULESTREAMNAME
            cur = body.decode('cp949', 'replace')
            name = cur
        elif rid == 0x0031 and name:   # MODULEOFFSET
            mods.append((name, struct.unpack('<I', body)[0]))
            name = None
    return mods


def sources(xlsm_bytes):
    """{모듈 이름: 소스 문자열}. VBA 가 없으면 빈 사전."""
    try:
        z = zipfile.ZipFile(io.BytesIO(xlsm_bytes))
        names = [n for n in z.namelist() if n.lower().endswith('vbaproject.bin')]
        if not names:
            return {}
        ole = olefile.OleFileIO(io.BytesIO(z.read(names[0])))
    except (zipfile.BadZipFile, OSError):
        return {}
    root = 'VBA'
    if not ole.exists(f'{root}/dir'):
        return {}
    out = {}
    try:
        mods = _modules(ole.openstream(f'{root}/dir').read())
    except (ValueError, IndexError, struct.error):
        mods = []
    for mod, offset in mods:
        path = f'{root}/{mod}'
        if not ole.exists(path):
            continue
        raw = ole.openstream(path).read()
        try:
            out[mod] = decompress(raw[offset:]).decode('cp949', 'replace')
        except (ValueError, IndexError):
            continue
    if not out:
        # dir 해석이 안 될 때(ActiveX 참조 등): 모듈 스트림에서 압축 소스 시작 위치를 찾는다
        for parts in ole.listdir():
            if len(parts) != 2 or parts[0] != root or parts[1] in ('dir', '_VBA_PROJECT') or parts[1].startswith('__SRP'):
                continue
            src = _scan_source(ole.openstream('/'.join(parts)).read())
            if src is not None:
                out[parts[1]] = src
    return out


_SCAN_MAX = 512 * 1024


def _scan_source(raw):
    """모듈 스트림 안에서 'Attribute VB_Name' 으로 풀리는 압축 덩어리를 찾는다(뒤에서부터 — 소스는 끝에 있다)."""
    raw = raw[-_SCAN_MAX:]                       # 엉터리 큰 스트림은 끝부분만(시간 폭증 방지)
    for i in range(len(raw) - 3, -1, -1):
        if raw[i] != 1 or (raw[i + 2] & 0x70) != 0x30:      # 서명 1 + 덩어리 머리 0b011
            continue
        try:
            text = decompress(raw[i:])
        except (ValueError, IndexError, struct.error):
            continue
        if text.startswith(b'Attribute VB_'):
            return text.decode('cp949', 'replace')
    return None


PROC_RE = re.compile(r'^\s*(?:Public\s+|Private\s+)?(Sub|Function)\s+([^\s(]+)\s*\(', re.I | re.M)


def procedures(srcs):
    """{프로시저 이름(소문자): 본문}"""
    out = {}
    for text in srcs.values():
        matches = list(PROC_RE.finditer(text))
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            out[m.group(2).lower()] = text[m.start():end]
    return out


def button_cells(xlsm_bytes):
    """단추 → [(단추 글자, 매크로 이름, 왼쪽 위 셀의 행, 열)] (1부터). 양식 컨트롤은 VML 의 Anchor, 도형은 drawing 의 from."""
    out = []
    try:
        z = zipfile.ZipFile(io.BytesIO(xlsm_bytes))
    except zipfile.BadZipFile:
        return out
    for n in z.namelist():
        if re.search(r'drawings/vmlDrawing\d*\.vml$', n):
            text = z.read(n).decode('utf-8', 'replace')
            for shape in re.findall(r'<v:shape\b.*?</v:shape>', text, re.S):
                if 'ObjectType="Button"' not in shape:
                    continue
                label = re.sub(r'<[^>]+>', '', ''.join(re.findall(r'<v:textbox\b.*?</v:textbox>', shape, re.S))).strip()
                macro = re.search(r'<x:FmlaMacro>(.*?)</x:FmlaMacro>', shape, re.S)
                macro = re.sub(r'^\[\d+\]!', '', macro.group(1).strip() if macro else '').split('!')[-1]
                a = re.search(r'<x:Anchor>\s*(\d+)\s*,\s*-?\d+\s*,\s*(\d+)', shape)
                if a:
                    out.append((re.sub(r'\s+', ' ', label), macro, int(a.group(2)) + 1, int(a.group(1)) + 1))
        elif re.search(r'drawings/drawing\d*\.xml$', n):
            text = z.read(n).decode('utf-8', 'replace')
            for sp in re.findall(r'<xdr:twoCellAnchor\b.*?</xdr:twoCellAnchor>', text, re.S):
                m = re.search(r'<xdr:sp\b[^>]*\bmacro="([^"]*)"', sp)
                f = re.search(r'<xdr:from>\s*<xdr:col>(\d+)</xdr:col>.*?<xdr:row>(\d+)</xdr:row>', sp, re.S)
                if m and m.group(1) and f:
                    label = ''.join(re.findall(r'<a:t>([^<]*)</a:t>', sp))
                    macro = re.sub(r'^\[\d+\]!', '', m.group(1)).split('!')[-1]
                    out.append((re.sub(r'\s+', ' ', label).strip(), macro, int(f.group(2)) + 1, int(f.group(1)) + 1))
    return out


def buttons(xlsm_bytes):
    """양식 단추 → [(단추 글자, 연결된 매크로 이름)]"""
    out = []
    try:
        z = zipfile.ZipFile(io.BytesIO(xlsm_bytes))
    except zipfile.BadZipFile:
        return out
    for n in z.namelist():
        if not re.search(r'drawings/vmlDrawing\d*\.vml$', n):
            continue
        text = z.read(n).decode('utf-8', 'replace')
        for shape in re.findall(r'<v:shape\b.*?</v:shape>', text, re.S):
            if 'ObjectType="Button"' not in shape:
                continue
            label = re.sub(r'<[^>]+>', '', ''.join(re.findall(r'<v:textbox\b.*?</v:textbox>', shape, re.S))).strip()
            macro = re.search(r'<x:FmlaMacro>(.*?)</x:FmlaMacro>', shape, re.S)
            macro = macro.group(1).strip() if macro else ''
            macro = re.sub(r'^\[\d+\]!', '', macro).split('!')[-1]
            out.append((re.sub(r'\s+', ' ', label), macro))
    # 도형(사각형: 빗면 등)에 매크로를 지정한 경우 — drawingN.xml 의 xdr:sp macro="[0]!이름"
    for n in z.namelist():
        if not re.search(r'drawings/drawing\d*\.xml$', n):
            continue
        text = z.read(n).decode('utf-8', 'replace')
        for sp in re.findall(r'<xdr:sp\b.*?</xdr:sp>', text, re.S):
            m = re.match(r'<xdr:sp\b[^>]*\bmacro="([^"]*)"', sp)
            if not m or not m.group(1):
                continue
            label = ''.join(re.findall(r'<a:t>([^<]*)</a:t>', sp))
            macro = re.sub(r'^\[\d+\]!', '', m.group(1)).split('!')[-1]
            out.append((re.sub(r'\s+', ' ', label).strip(), macro))
    return out
