"""(호환용) SAP 전송만 실행. 새 운영에서는 `python batch.py --loop`를 쓴다."""
import sys

from core import db, jobs

if __name__ == "__main__":
    db.init_db()
    if "--loop" in sys.argv:
        import time
        while True:
            jobs.run("sap_sync")
            time.sleep(15)
    print(jobs.run("sap_sync", force=True))
