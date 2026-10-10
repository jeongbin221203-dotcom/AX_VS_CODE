import os
from pathlib import Path

BASE = Path(__file__).resolve().parent


def _load_env(path):
    """.env(KEY=VALUE 줄)를 환경변수로. 이미 있는 값은 덮어쓰지 않는다. 값은 어디에도 출력하지 않음."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


_load_env(BASE / ".env")
DATA_DIR = Path(os.environ.get("STOCK_DATA_DIR", BASE / "data"))
DB_PATH = Path(os.environ.get("STOCK_DB_PATH", DATA_DIR / "stock.db"))
PORT = int(os.environ.get("STOCK_PORT", "5006"))
SECRET_KEY = os.environ.get("STOCK_SECRET_KEY", "dev-only-change-me")
DEFAULT_YEARS = 5
FETCH_DELAY = 0.3  # 종목 사이 대기(초)
DISCLAIMER = "투자 참고용 학습 도구이며 수익을 보장하지 않습니다. 투자 판단과 책임은 본인에게 있습니다."
SOURCE = os.environ.get("STOCK_SOURCE", "auto")  # auto: 토스 키가 있으면 토스, 실패·없으면 FinanceDataReader
SCHEDULER = os.environ.get("STOCK_SCHEDULER", "1") == "1"  # 장 마감 후 자동 갱신(평일 16:10)
