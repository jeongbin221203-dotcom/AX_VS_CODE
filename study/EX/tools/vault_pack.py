"""PC 의 개인 자료(data/library · data/official)를 암호화해 content/private/vault.bin 으로 만든다.

    python tools/vault_pack.py          # 키가 없으면 data/vault.key 를 새로 만든다

배포 서버에는 같은 키를 EX_VAULT_KEY 환경 변수로 넣는다(키는 저장소에 올리지 않음).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import vault  # noqa: E402


def main():
    data = ROOT / 'data'
    key_file = data / 'vault.key'
    if not key_file.exists():
        key_file.write_text(vault.new_key(), encoding='ascii')
        print('새 키를 만들었습니다:', key_file)
    key = key_file.read_text(encoding='ascii').strip()
    blob = vault.pack(data, key)
    out = ROOT / 'content' / 'private' / 'vault.bin'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)
    print(f'{out} ({len(blob) / 1024 / 1024:.1f}MB), 암호화됨')


if __name__ == '__main__':
    main()
