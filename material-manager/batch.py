"""배치 실행기. 모든 서버에서 켜 두어도 작업마다 한 서버만 실행한다(core/jobs.py의 잠금).

    python batch.py --loop            # 15초마다 주기가 된 작업을 실행 (운영: 서비스로 등록)
    python batch.py                   # 주기가 된 작업을 한 번만
    python batch.py run sap_sync      # 특정 작업을 지금 실행
    python batch.py list              # 작업 목록과 마지막 실행 결과
"""
import argparse
import time

from core import db, jobs


def main() -> None:
    parser = argparse.ArgumentParser(description="자재관리 배치 실행기")
    parser.add_argument("command", nargs="?", default="due", choices=["due", "run", "list"])
    parser.add_argument("job", nargs="?")
    parser.add_argument("--loop", action="store_true", help="계속 실행")
    parser.add_argument("--tick", type=int, default=15, help="루프 간격(초)")
    args = parser.parse_args()
    db.init_db()

    if args.command == "list":
        print(jobs.status_df().to_string(index=False))
        return
    if args.command == "run":
        if args.job not in jobs.JOBS:
            parser.error(f"작업 이름: {', '.join(jobs.JOBS)}")
        print(jobs.run(args.job, force=True))
        return
    while True:
        for name, msg in jobs.run_due().items():
            print(time.strftime("%Y-%m-%d %H:%M:%S"), name, msg, flush=True)
        if not args.loop:
            break
        time.sleep(args.tick)


if __name__ == "__main__":
    main()
