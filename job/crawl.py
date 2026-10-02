"""정기 크롤링을 앱 밖에서 실행한다.

  python crawl.py            # 정해진 시각이 됐으면 한 번 실행 (작업 스케줄러에 1시간마다 등록해 두면 됨)
  python crawl.py --now      # 시각과 상관없이 지금 한 번 실행
  python crawl.py --loop     # 계속 켜 두고 때가 되면 실행 (앱은 JOB_SCHEDULER=0 으로 띄움)

설정(켜기·검색어·사이트·간격)은 화면의 '공고 수집 → 자동 수집'에서 저장한 값을 쓴다.
"""
from __future__ import annotations

import argparse
import json
import logging
import time

import config
from core import crawler, db


def main() -> None:
    ap = argparse.ArgumentParser(description="채용공고 정기 크롤링")
    ap.add_argument("--now", action="store_true", help="시각과 상관없이 지금 실행")
    ap.add_argument("--loop", action="store_true", help="계속 켜 두고 때가 되면 실행")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db.configure(config.DB_PATH)

    if args.loop:
        while True:
            _report(crawler.run_once())
            time.sleep(60)
    result = crawler.run_once(force=args.now)
    if result is None and not args.now and not crawler.load_settings()["enabled"]:
        print("자동 수집이 꺼져 있습니다. 화면에서 켜거나 --now 로 실행하세요.")
    _report(result)


def _report(result) -> None:
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
