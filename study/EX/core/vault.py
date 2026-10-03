"""개인 자료(교재 실습 파일·공식 예제) 암호화 보관: 저장소에는 암호문만 두고, 배포 서버가 시작할 때 키로 풀어 data/ 에 둔다.

키(EX_VAULT_KEY)는 저장소에 넣지 않는다 — PC 의 data/vault.key 와 Render 환경 변수에만.
"""
import hashlib
import io
import zipfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

FOLDERS = ('library', 'official')


def new_key():
    return Fernet.generate_key().decode()


def pack(data_dir, key):
    """data/library · data/official → 암호화한 zip 바이트"""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, 'w', zipfile.ZIP_DEFLATED) as z:
        for name in FOLDERS:
            root = Path(data_dir) / name
            if not root.exists():
                continue
            for f in sorted(root.rglob('*')):
                if f.is_file():
                    z.write(f, f'{name}/{f.relative_to(root).as_posix()}')
    return Fernet(key.encode()).encrypt(bio.getvalue())


def unpack(blob, key, data_dir):
    """암호문 → data/ 아래에 풀기. 키가 틀리면 ValueError."""
    try:
        raw = Fernet(key.encode()).decrypt(blob)
    except (InvalidToken, ValueError) as e:
        raise ValueError('보관 자료를 풀 수 없습니다(키가 다릅니다).') from e
    data_dir = Path(data_dir).resolve()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for info in z.infolist():
            target = (data_dir / info.filename).resolve()
            if not str(target).startswith(str(data_dir)) or info.filename.split('/')[0] not in FOLDERS:
                continue                                   # 경로 밖으로 나가는 항목은 건너뜀
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
    return len(raw)


def restore_on_start(vault_path, key, data_dir):
    """서버 시작 때: 보관 파일이 바뀌었으면 다시 푼다. → 푼 바이트 수(이미 최신이면 0)"""
    vault_path = Path(vault_path)
    if not key or not vault_path.exists():
        return 0
    blob = vault_path.read_bytes()
    stamp = Path(data_dir) / 'vault.stamp'
    digest = hashlib.sha256(blob).hexdigest()
    if stamp.exists() and stamp.read_text() == digest:
        return 0
    n = unpack(blob, key, data_dir)
    stamp.write_text(digest)
    return n
