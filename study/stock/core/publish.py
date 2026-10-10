"""오늘의 신호 스냅샷(data/signals.json → snapshot/signals.json)을 stock 브랜치에 올려 Render 가 자동 배포하게 한다.

저장소 규칙(프로젝트별 브랜치에만 커밋)에 맞춰 임시 인덱스로 study/stock 만 담아 .git/hooks/project_branches.py 로 커밋하고
git push origin stock. 다른 세션이 올려 둔 변경은 건드리지 않는다. 비밀·데이터 파일은 올리지 않는다(snapshot/ 한 파일뿐).
"""
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import config

SRC = config.DATA_DIR / "signals.json"
DST = config.BASE / "snapshot" / "signals.json"


def refresh() -> bool:
    """snapshot/signals.json 을 최신 훑기 결과로 바꾼다. 바뀐 게 없으면 False."""
    try:
        new = json.loads(SRC.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError("data/signals.json 이 없습니다 (python manage.py signals-scan 먼저)")
    if not new.get("meta", {}).get("date"):
        raise RuntimeError("신호 결과가 비어 있어 올리지 않습니다")
    DST.parent.mkdir(parents=True, exist_ok=True)
    try:
        old = json.loads(DST.read_text(encoding="utf-8"))
        if old == new:
            return False
    except (OSError, ValueError):
        pass
    shutil.copyfile(SRC, DST)
    return True


def _git(*args, env=None, cwd=None):
    r = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", cwd=cwd, env={**os.environ, **(env or {})})
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def push(log=print) -> str:
    top = Path(_git("rev-parse", "--show-toplevel", cwd=config.BASE))
    rel = DST.relative_to(top).as_posix()
    helper = top / ".git" / "hooks" / "project_branches.py"
    if not helper.exists():
        raise RuntimeError("커밋 도우미(.git/hooks/project_branches.py)가 없습니다")
    fd, idx = tempfile.mkstemp(prefix="stock-index-")
    os.close(fd)
    os.remove(idx)
    env = {"GIT_INDEX_FILE": idx}
    try:
        _git("read-tree", "HEAD", env=env, cwd=top)
        _git("add", "-f", "--", rel, env=env, cwd=top)
        if not _git("diff", "--cached", "--name-only", env=env, cwd=top):
            return "변경 없음"
        msg = f"스냅샷: {datetime.now():%Y-%m-%d %H:%M} 오늘의 신호 갱신"
        out = subprocess.run(["python", str(helper), "commit", "-m", msg], capture_output=True, text=True, cwd=top, env={**os.environ, **env})
        if out.returncode:
            raise RuntimeError(f"커밋 실패: {(out.stderr or out.stdout).strip()[:300]}")
    finally:
        if os.path.exists(idx):
            os.remove(idx)
    _git("push", "origin", "stock", cwd=top)
    log("stock 브랜치 푸시 완료")
    return "푸시 완료"


def run(log=print, do_push: bool = True) -> str:
    if not refresh():
        log("스냅샷이 이미 최신입니다")
        return "변경 없음"
    log("snapshot/signals.json 갱신")
    return push(log) if do_push else "복사만"
