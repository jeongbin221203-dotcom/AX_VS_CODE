"""자동 백업. 배치 작업 db_backup이 주기마다(기본 24시간) 돌리고, `flask --app app backup`으로 바로 돌릴 수도 있다.

SQLite
- 온라인 백업 API로 복사한다 → 사용 중에도 한 시점의 일관된 사본이 된다(파일 복사와 달리 쓰는 중인 페이지가 섞이지 않음).
- 복사본을 quick_check로 검사해 통과한 것만 남긴다. 반쯤 쓴 파일은 .part 이름이라 복원 대상으로 보이지 않는다.
PostgreSQL
- pg_dump(MM_PG_DUMP, 없으면 PATH)로 사용자 지정 형식(.dump)을 만들고 pg_restore --list로 읽히는지 확인한다.
  비밀번호는 명령줄에 넣지 않고 PGPASSWORD 환경변수로 넘긴다(다른 사용자가 프로세스 목록에서 보지 못하게).
  DB 서버에서 따로 백업(스냅샷·WAL 보관)을 하고 있으면 MM_BACKUP_HOURS=0으로 끈다.
공통
- 최근 MM_BACKUP_KEEP개만 남긴다.
- 로컬 저장소의 증빙 파일은 백업 폴더 attachments/에 새 파일만 복사한다(증빙은 고치지 않고 이름이 무작위라 덮어쓸 일이 없다).
  s3 저장소는 버킷 버전 관리·복제로 보관한다.
- 백업 폴더가 DB와 같은 디스크면 경고한다(디스크가 고장 나면 둘 다 잃는다).

복원(SQLite): 서버(앱·배치)를 멈추고 → 지금 DB 파일을 다른 이름으로 옮겨 두고 → 백업 파일을 MM_DB_PATH 이름으로 복사 → 다시 시작.
      (-wal, -shm 파일이 남아 있으면 함께 옮겨 둔다.)
복원(PostgreSQL): 빈 DB를 만들고 `pg_restore --dbname=<새 DB> --no-owner <파일>.dump` → 앱의 MM_DATABASE_URL을 새 DB로.
"""

import os
import shutil
import sqlite3
import subprocess
import urllib.parse
from datetime import datetime
from pathlib import Path

import config
from core import db


def enabled() -> bool:
    if config.BACKUP_HOURS <= 0:
        return False
    return bool(config.PG_DUMP) if db.is_pg() else True


def _stem() -> str:
    if db.is_pg():
        return "pg-" + (urllib.parse.urlsplit(config.DATABASE_URL).path.strip("/") or "db")
    return config.DB_PATH.stem


def files() -> list[Path]:
    ext = "dump" if db.is_pg() else "db"
    return sorted(Path(config.BACKUP_DIR).glob(f"{_stem()}-*.{ext}"))


def same_disk_warning() -> str:
    """백업 폴더가 DB 파일과 같은 디스크인지 (SQLite만 판단할 수 있다)."""
    if db.is_pg():
        return ""
    try:
        Path(config.BACKUP_DIR).mkdir(parents=True, exist_ok=True)
        if os.stat(config.BACKUP_DIR).st_dev == os.stat(config.DB_PATH.parent).st_dev:
            return "백업 폴더가 DB와 같은 디스크에 있습니다. 디스크가 고장 나면 둘 다 잃으니 MM_BACKUP_DIR를 다른 디스크·NAS로 지정하세요."
    except OSError:
        return ""
    return ""


def run() -> str:
    dest = Path(config.BACKUP_DIR)
    dest.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    stamp = f"{now:%Y%m%d-%H%M%S}-{now.microsecond // 1000:03d}"
    if db.is_pg():
        if not config.PG_DUMP:
            return "PostgreSQL: pg_dump가 없어 건너뜀 (MM_PG_DUMP로 경로 지정, 또는 DB 서버의 백업 사용)"
        name = _pg_dump(dest, f"{_stem()}-{stamp}.dump")
    else:
        name = _sqlite_backup(dest, f"{_stem()}-{stamp}.db")

    copied = _mirror_attachments(dest / "attachments")
    old = files()[:-config.BACKUP_KEEP]
    for f in old:
        f.unlink(missing_ok=True)
    size = (dest / name).stat().st_size
    warn = same_disk_warning()
    return (f"{name} ({size / 1024 / 1024:,.1f}MB, 검사 통과) · 증빙 새 파일 {copied}건 · "
            f"오래된 백업 {len(old)}개 정리" + (f" · 주의: {warn}" if warn else ""))


def _sqlite_backup(dest: Path, name: str) -> str:
    part = dest / f"{name}.part"
    src = sqlite3.connect(config.DB_PATH, timeout=30)
    out = sqlite3.connect(part)
    try:
        src.backup(out)
        check = out.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        out.close()
        src.close()
    if check != "ok":
        part.unlink(missing_ok=True)
        raise RuntimeError(f"백업 사본 검사 실패: {check}")
    part.replace(dest / name)
    return name


def _pg_env_and_url() -> tuple[dict, str]:
    """비밀번호를 뺀 접속 주소 + PGPASSWORD 환경변수."""
    parts = urllib.parse.urlsplit(config.DATABASE_URL)
    env = dict(os.environ)
    if parts.password:
        env["PGPASSWORD"] = urllib.parse.unquote(parts.password)
    netloc = parts.hostname or ""
    if parts.username:
        netloc = f"{parts.username}@{netloc}"
    if parts.port:
        netloc += f":{parts.port}"
    scheme = "postgresql" if parts.scheme.startswith("postgres") else parts.scheme
    return env, urllib.parse.urlunsplit((scheme, netloc, parts.path, parts.query, ""))


def _pg_dump(dest: Path, name: str) -> str:
    part = dest / f"{name}.part"
    env, url = _pg_env_and_url()
    res = subprocess.run([config.PG_DUMP, "--format=custom", "--no-owner", f"--file={part}", f"--dbname={url}"],
                         env=env, capture_output=True, text=True, timeout=3600)
    if res.returncode != 0:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump 실패: {res.stderr.strip()[:300]}")
    if config.PG_RESTORE:
        check = subprocess.run([config.PG_RESTORE, "--list", str(part)], capture_output=True, text=True, timeout=600)
        if check.returncode != 0 or "transactions" not in check.stdout:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"백업 파일 검사 실패: {check.stderr.strip()[:300]}")
    part.replace(dest / name)
    return name


def _mirror_attachments(target: Path) -> int:
    if config.STORAGE != "local":
        return 0
    source = Path(config.STORAGE_DIR) / "attachments"
    if not source.exists():
        return 0
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for f in source.iterdir():
        if f.is_file() and not f.name.endswith(".part") and not (target / f.name).exists():
            shutil.copy2(f, target / f.name)
            copied += 1
    return copied
