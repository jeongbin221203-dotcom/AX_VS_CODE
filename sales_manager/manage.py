"""운영 명령

  python manage.py db upgrade            스키마를 최신 버전으로 (배포 단계에서 한 번)
  python manage.py db current            현재 스키마 버전
  python manage.py db history            마이그레이션 목록
  python manage.py worker                작업 워커 + 스케줄러 (서버마다 여러 개 띄워도 된다)
  python manage.py worker --once         대기 작업만 처리하고 끝낸다 (OS 스케줄러용)
  python manage.py backup                DB 백업 (SQLite 온라인 백업 / PostgreSQL pg_dump)
  python manage.py restore <파일>        백업에서 복구 (PostgreSQL: pg_restore --clean, SQLite: 파일 교체)
  python manage.py check                 DB·저장소·스키마 상태 점검 (배포 후 확인용)
  python manage.py demo-init             빈 DB 에 시연용 조직·계정·샘플 데이터 (Render 같은 시연 서버, SALES_DEMO=1 필요)
  python manage.py demo-build            시연 샘플 DB 를 data/demo_template.db 로 미리 만든다 (배포의 빌드 단계, SALES_DEMO=1)
                                         serve.py 가 켜질 때 이 파일로 바로 열고(스키마 갱신도 serve.py 안에서), 오늘 기준 샘플은 뒤에서 만든다 (core/demo_data.py)
                                         Render: 빌드 = pip install … && python manage.py demo-build, 시작 = python serve.py
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
    from core import offline
    from core import sales_db as db
    from core.storage import get_storage
    ok = True
    for row in offline.dependencies():
        if row["위치"] != "사용 안 함":
            print(f"연결      {row['위치']:<6} {row['연결']} ({row['주소']}) — 끊기면: {row['끊기면']}")
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


def cmd_seed_mfg(args) -> int:
    if config.PRODUCTION:
        print("운영(production)에서는 샘플 데이터를 넣지 않습니다.")
        return 2
    from app import create_app
    create_app()
    from core import sample_mfg
    print(sample_mfg.seed(customers=args.customers, months=args.months))
    return 0


def cmd_seed_sample(args) -> int:
    if config.PRODUCTION:
        print("운영(production)에서는 샘플 데이터를 넣지 않습니다.")
        return 2
    from app import create_app
    create_app()
    from core import sample_industry as si
    keys = si.INDUSTRY_KEYS if args.industry == "all" else [args.industry]
    for key, out in si.seed_many(keys, customers=args.customers, months=args.months).items():
        print(key, out)
    return 0


def cmd_demo_init(args) -> int:
    """시연 서버용: 빈 DB 에만 조직·계정(같은 비밀번호)·업종별 샘플 데이터를 넣는다.

    실수로 실제 DB 에 넣지 않도록 SALES_DEMO=1 이 있어야 하고, 거래처가 하나라도 있으면 아무것도 하지 않는다.
    """
    if os.environ.get("SALES_DEMO") != "1":
        print("SALES_DEMO=1 일 때만 실행합니다 (시연 서버 전용).")
        return 2
    password = os.environ.get("SALES_DEMO_PASSWORD", "")
    from app import create_app
    create_app()
    from core import auth, sample_industry
    from core import enterprise as ent
    from core import sales_db as db
    if int(db._scalar("SELECT COUNT(*) FROM customers") or 0):
        print("이미 데이터가 있어 건너뜁니다.")
        return 0
    import time
    started = time.time()
    with database.bulk_load():
        db.set_context("system", None)
        ent.seed_org_demo()
        if password:
            for uid in db._df("SELECT id FROM users WHERE active=1")["id"].tolist():
                auth.set_password(int(uid), password)
        print("기본 샘플:", db.seed_demo_data())
        for key, out in sample_industry.seed_many(customers=args.customers).items():
            print(key, out)
        sample_industry.backdate_customers()            # 기본 샘플 거래처도 첫 거래보다 먼저 등록된 것으로
        sample_industry.realign_targets()               # 목표를 담당자별 평균 매출에 맞춤 (달성률이 수백 %로 튀지 않게)
    print(f"시연 데이터 준비 {time.time() - started:.1f}초 (기준일 {__import__('datetime').date.today()})")
    return 0


def cmd_demo_build(args) -> int:
    if os.environ.get("SALES_DEMO") != "1":
        print("SALES_DEMO=1 일 때만 실행합니다 (시연 서버 전용).")
        return 2
    from core import demo_data
    out = args.out or demo_data.TEMPLATE
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    seconds = demo_data.build(out)
    print(f"시연 샘플 DB 준비 {seconds:.1f}초 → {out}")
    return 0


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
    p = sub.add_parser("seed-mfg", help="제조업 샘플 데이터 추가 (개발·시연용)")
    p.add_argument("--customers", type=int, default=20)
    p.add_argument("--months", type=int, default=12)
    p.set_defaults(func=cmd_seed_mfg)
    p = sub.add_parser("seed-sample", help="업종별 샘플 데이터 추가 (개발·시연용)")
    p.add_argument("--industry", default="all", help="제조|유통|건설|IT/SW|의료|교육|금융|공공|all")
    p.add_argument("--customers", type=int, default=8, help="업종마다 거래처 수")
    p.add_argument("--months", type=int, default=12)
    p.set_defaults(func=cmd_seed_sample)
    p = sub.add_parser("demo-init", help="시연 서버 빌드용 — 빈 DB 에 조직·계정·샘플 데이터")
    p.add_argument("--customers", type=int, default=5, help="업종마다 거래처 수")
    p.set_defaults(func=cmd_demo_init)
    p = sub.add_parser("demo-build", help="시연 서버 빌드용 — 샘플 DB 를 미리 만들어 둔다 (data/demo_template.db)")
    p.add_argument("--out")
    p.set_defaults(func=cmd_demo_build)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
