"""운영 명령

  python manage.py db upgrade            스키마를 최신 버전으로 (배포 단계에서 한 번)
  python manage.py db current            현재 스키마 버전
  python manage.py db history            마이그레이션 목록
  python manage.py worker                작업 워커 + 스케줄러 (서버마다 여러 개 띄워도 된다)
  python manage.py worker --once         대기 작업만 처리하고 끝낸다 (OS 스케줄러용)
  python manage.py backup                DB 백업 (SQLite 온라인 백업 / PostgreSQL pg_dump)
  python manage.py restore <파일>        백업에서 복구 (PostgreSQL: pg_restore --clean, SQLite: 파일 교체)
  python manage.py check                 DB·저장소·스키마 상태 점검 (배포 후 확인용)
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys

import config
from core import database


def cmd_db(args) -> int:
    from alembic import command
    cfg = database.alembic_config()
    if args.action == "upgrade":
        command.upgrade(cfg, args.revision or "head")
        from core import sales_db as db
        with db.get_conn() as conn:
            db.link_owner_ids(conn)
    elif args.action == "current":
        print(database.current_revision() or "(없음)", "/ 최신:", database.head_revision())
    elif args.action == "history":
        command.history(cfg)
    elif args.action == "downgrade":
        command.downgrade(cfg, args.revision or "-1")
    return 0


def cmd_worker(args) -> int:
    from core import jobs
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.once:
        jobs.heartbeat(jobs.worker_id())
        jobs.tick()
        jobs.recover_stale()
        print("처리:", jobs.run_pending(limit=args.limit))
        return 0
    jobs.run_worker(poll_seconds=args.poll)
    return 0


def cmd_backup(args) -> int:
    from core import sales_db as db
    db.set_context("batch", None)
    print(db.backup_database(args.dir or config.BACKUP_DIR))
    return 0


def cmd_restore(args) -> int:
    """복구는 되돌릴 수 없으므로 --yes 를 요구한다. 복구 직전 현재 DB 를 한 번 더 백업한다."""
    if not args.yes:
        print("복구하면 현재 데이터가 백업 시점으로 바뀝니다. 확인했으면 --yes 를 붙이세요.")
        return 2
    from core import sales_db as db
    db.set_context("batch", None)
    safety = db.backup_database(config.BACKUP_DIR)
    print("복구 전 안전 백업:", safety)
    if database.is_pg():
        exe = os.environ.get("SALES_PG_RESTORE") or shutil.which("pg_restore") or "pg_restore"
        database.close_pool()
        result = subprocess.run([exe, "--clean", "--if-exists", "--no-owner", f"--dbname={database.DATABASE_URL}",
                                 args.file], capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stderr)
            return result.returncode
    else:
        shutil.copyfile(args.file, database.DB_PATH)
    print("복구 완료:", args.file)
    return 0


def cmd_check(args) -> int:
    from core import sales_db as db
    from core.storage import get_storage
    ok = True
    try:
        db._scalar("SELECT 1")
        print("DB        OK ", database.describe())
    except Exception as exc:   # noqa: BLE001
        ok = False
        print("DB        실패", exc)
    try:
        current, head = database.current_revision(), database.head_revision()
        print("스키마    ", "OK " if current == head else "확인 필요", current, "/", head)
        ok = ok and current == head
    except Exception as exc:   # noqa: BLE001
        ok = False
        print("스키마    실패", exc)
    try:
        st = get_storage()
        st.put("healthcheck/probe.txt", b"ok")
        assert st.get("healthcheck/probe.txt") == b"ok"
        st.delete("healthcheck/probe.txt")
        print("저장소    OK ", st.describe())
    except Exception as exc:   # noqa: BLE001
        ok = False
        print("저장소    실패", exc)
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="영업관리 운영 명령")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("db")
    p.add_argument("action", choices=["upgrade", "current", "history", "downgrade"])
    p.add_argument("revision", nargs="?")
    p.set_defaults(func=cmd_db)
    p = sub.add_parser("worker")
    p.add_argument("--once", action="store_true")
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--poll", type=float, default=2.0)
    p.set_defaults(func=cmd_worker)
    p = sub.add_parser("backup")
    p.add_argument("--dir")
    p.set_defaults(func=cmd_backup)
    p = sub.add_parser("restore")
    p.add_argument("file")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_restore)
    p = sub.add_parser("check")
    p.set_defaults(func=cmd_check)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
