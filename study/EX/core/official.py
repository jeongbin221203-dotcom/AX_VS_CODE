"""대한상공회의소 공식 '컴퓨터활용능력 예제 문제' — 공식 사이트에서 받아 이 PC 의 data/official 에만 둔다(저작권:
대한상공회의소, 저장소에는 올리지 않음). 채점은 공식 '정답' 파일과 비교한다.

비교 채점: '소스'(문제) 파일과 '정답' 파일의 차이 = 수험자가 해야 할 일. 그 차이를 시트별 항목으로 나눠
수험자 파일이 정답과 같아졌는지 본다. 시트별 배점은 공식 문제지의 배점, 시트 안에서는 항목 수로 나눈 추정 점수.
"""
import datetime as dt
import io
import json
import re
import shutil
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from . import compare

BASE = 'https://license.korcham.net/kor/etc/download.jsp'
PAGE = 'https://license.korcham.net/co/examguide02Sub.do?cd=0103&mm=21&num=2941771'
PAGE_2015 = 'https://license.korcham.net/co/examguide04Sub.do?cd=0103&mm=21&no=139861&pg=2'

PACKAGES = {
    '2024': {'cd': '2', 'file': '2024~2026 컴퓨터활용능력 1_2급 예제 문제(20240319 수정 공지) .zip', 'page': PAGE},
    '2015-c1': {'cd': '3', 'file': '1급엑셀 예제.zip', 'page': PAGE_2015},
    '2015-c2': {'cd': '3', 'file': '2급엑셀 예제.zip', 'page': PAGE_2015},
}

# (id, 꾸러미, 급, 제목, 폴더 조건, {역할: 파일 이름 정규식})
SETS = [
    ('c2-A', '2024', 'c2', '2급 엑셀 A형 (2024~2026 예제)', '2급 엑셀 A형',
     {'pdf': r'\.pdf$', 'source': r'소스\.xls[xm]$', 'answer': r'정답\.xlsm$'}),
    ('c2-B', '2024', 'c2', '2급 엑셀 B형 (2024~2026 예제)', '2급 엑셀 B형',
     {'pdf': r'\.pdf$', 'source': r'소스\.xls[xm]$', 'answer': r'정답\.xlsm$'}),
    ('c1-A', '2024', 'c1', '1급 엑셀 A형 (2024~2026 예제)', '1급 엑셀 A형',
     {'pdf': r'\.pdf$', 'source': r'소스\.xls[xm]$', 'answer': r'정답\.xlsm$', 'extra': r'\.(csv|accdb|xlsx|txt)$'}),
    ('c1-B', '2024', 'c1', '1급 엑셀 B형 (2024~2026 예제)', '1급 엑셀 B형',
     {'pdf': r'\.pdf$', 'source': r'소스\.xls[xm]$', 'answer': r'정답\.xlsm$', 'extra': r'\.(csv|accdb|xlsx|txt)$'}),
    ('c2-2015', '2015-c2', 'c2', '2급 엑셀 연습 예제 (2015)', '',
     {'pdf': r'\.pdf$', 'source': r'소스\.xls[xm]$'}),
    ('c1-2015', '2015-c1', 'c1', '1급 엑셀 연습 예제 (2015)', '',
     {'pdf': r'\.pdf$', 'source': r'소스\.xls[xm]$', 'extra': r'\.(accdb|csv)$'}),
]

# ------------------------------------------------------------ 받기 ----------
def _zip_names(z):
    out = {}
    for i in z.infolist():
        name = i.filename
        if not i.flag_bits & 0x800:
            try:
                name = i.filename.encode('cp437').decode('cp949')
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
        out[name] = i
    return out


def _fetch(pkg):
    p = PACKAGES[pkg]
    url = BASE + '?' + urllib.parse.urlencode({'filename': p['file'], 'cd': p['cd']}, encoding='cp949')
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (study app)', 'Referer': p['page']})
    data = urllib.request.urlopen(req, timeout=90).read()
    if data[:2] != b'PK':
        raise OSError('공식 사이트에서 zip 파일을 받지 못했습니다(주소가 바뀌었을 수 있습니다).')
    return data


def download(folder):
    """공식 꾸러미를 받아 세트별 폴더에 문제지·소스·정답·추가 파일을 풀어 둔다. → 받은 세트 id 목록"""
    folder = Path(folder)
    got = []
    cache = {}
    for sid, pkg, level, title, where, roles in SETS:
        if pkg not in cache:
            cache[pkg] = zipfile.ZipFile(io.BytesIO(_fetch(pkg)))
        z = cache[pkg]
        names = _zip_names(z)
        dest = folder / sid
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        files = {}
        for name, info in names.items():
            base = name.rsplit('/', 1)[-1]
            if not base or base.startswith('~') or (where and f'/{where}/' not in f'/{name}'):
                continue
            for role, pat in roles.items():
                if re.search(pat, base):
                    if role == 'extra' and re.search(roles.get('source', '^$'), base):
                        continue
                    (dest / base).write_bytes(z.read(info))
                    if role == 'extra':
                        files.setdefault('extra', []).append(base)
                    elif role not in files:
                        files[role] = base
                    break
        meta = {'id': sid, 'level': level, 'title': title, 'files': files, 'source_page': PACKAGES[pkg]['page'],
                'fetched': dt.datetime.now().isoformat(timespec='seconds')}
        (dest / 'manifest.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
        got.append(sid)
    return got


def sets(folder):
    folder = Path(folder)
    out = []
    for sid, pkg, level, title, *_ in SETS:
        m = folder / sid / 'manifest.json'
        meta = json.loads(m.read_text(encoding='utf-8')) if m.exists() else None
        out.append({'id': sid, 'level': level, 'title': title, 'meta': meta, 'ready': bool(meta),
                    'gradable': bool(meta and meta['files'].get('answer'))})
    return out


def get(folder, sid):
    return next((s for s in sets(folder) if s['id'] == sid), None)


def file_path(folder, sid, name):
    s = get(folder, sid)
    if not s or not s['meta']:
        return None
    allowed = [v for k, v in s['meta']['files'].items() if k != 'extra'] + s['meta']['files'].get('extra', [])
    if name not in allowed:
        return None
    return Path(folder) / sid / name


def grade(meta, folder, user_bytes):
    """공식 정답 파일과 비교 채점."""
    d = Path(folder) / meta['id']
    return compare.grade((d / meta['files']['source']).read_bytes(), (d / meta['files']['answer']).read_bytes(),
                         user_bytes, meta['level'])


def self_check(folder, sid):
    """정답 파일을 정답과 비교하면 만점, 소스 파일이면 0점 근처여야 한다(채점기 점검용)."""
    s = get(folder, sid)
    d = Path(folder) / sid
    m = s['meta']
    return (grade(m, folder, (d / m['files']['answer']).read_bytes())['score'],
            grade(m, folder, (d / m['files']['source']).read_bytes())['score'])
