"""python manage.py update [--top N] [--codes 005930 000660]"""
import argparse

from core import collector, db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["update", "toss-check", "yt-list", "yt-meta", "yt-captions", "yt-stats", "yt-clean", "backtest", "ai-train", "entry-train", "avoid-train", "surge-train", "combined-train", "strategy-train", "risk-train", "boom-train", "jump-train", "precursor-train", "pattern-train", "quietvol-train", "timing-train", "presignal-train", "bestday-train", "plan-train", "signals-scan", "publish-snapshot"])
    ap.add_argument("--top", type=int, default=0, help="시가총액 상위 N개")
    ap.add_argument("--codes", nargs="*", default=[])
    ap.add_argument("--all", action="store_true", help="전체 종목(관심종목 먼저)")
    ap.add_argument("--full", action="store_true", help="상장 이후 전체 구간(종목마다 한 번, 이어 받기 가능)")
    ap.add_argument("--limit", type=int, default=0, help="yt-* 처리 개수 제한(0=전부)")
    ap.add_argument("--kinds", nargs="*", default=["video", "live", "short"])
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--fresh", action="store_true", help="strategy-train: 표본 캐시를 다시 만듦")
    a = ap.parse_args()
    if a.cmd == "publish-snapshot":
        from core import publish
        if a.codes[:1] == ["backfill"]:
            print("추가", publish.backfill(log=lambda m: print(m, flush=True)), "일")
        print(publish.run(log=lambda m: print(m, flush=True)))
        return
    if a.cmd == "signals-scan":
        from core import signal_scan
        rep = signal_scan.run(limit=a.limit, log=lambda m: print(m, flush=True))
        for line in rep["text"]:
            print("-", line)
        return
    if a.cmd == "plan-train":
        from core import plan_study
        rep = plan_study.run(limit=a.limit, log=lambda m: print(m, flush=True))
        for line in rep["text"]:
            print("-", line)
        return
    if a.cmd == "bestday-train":
        from core import bestday_study
        rep = bestday_study.run(limit=a.limit, log=lambda m: print(m, flush=True), fresh=a.fresh)
        for line in rep["text"]:
            print("-", line)
        return
    if a.cmd == "presignal-train":
        from core import presignal_study
        rep = presignal_study.run(limit=a.limit, log=lambda m: print(m, flush=True))
        for line in rep["text"]:
            print("-", line)
        return
    if a.cmd == "timing-train":
        from core import timing_study
        rep = timing_study.run(limit=a.limit, log=lambda m: print(m, flush=True))
        for line in rep["text"]:
            print("-", line)
        return
    if a.cmd == "quietvol-train":
        from core import quietvol_study
        rep = quietvol_study.run(limit=a.limit, log=lambda m: print(m, flush=True))
        for line in rep["text"]:
            print("-", line)
        return
    if a.cmd == "pattern-train":
        from core import pattern_study
        rep = pattern_study.run(limit=a.limit, log=lambda m: print(m, flush=True))
        for line in rep["text"]:
            print("-", line)
        return
    if a.cmd == "precursor-train":
        from core import precursor_study
        rep = precursor_study.run(limit=a.limit, log=lambda m: print(m, flush=True))
        for line in rep["text"]:
            print("-", line)
        return
    if a.cmd == "jump-train":
        from core import jump_study
        rep = jump_study.run(limit=a.limit, log=lambda m: print(m, flush=True))
        for line in rep["text"]:
            print("-", line)
        return
    if a.cmd == "boom-train":
        from core import boom_study
        rep = boom_study.run(limit=a.limit, log=lambda m: print(m, flush=True))
        for line in rep["text"]:
            print("-", line)
        return
    if a.cmd == "risk-train":
        from core import risk_study
        rep = risk_study.run(limit=a.limit, log=lambda m: print(m, flush=True))
        for line in rep["text"]:
            print("-", line)
        return
    if a.cmd == "strategy-train":
        from core import strategy_study
        rep = strategy_study.run(limit=a.limit, log=lambda m: print(m, flush=True), fresh=a.fresh)
        for line in rep["text"]:
            print("-", line)
        return
    if a.cmd == "combined-train":
        from core import combined_study
        rep = combined_study.run(limit=a.limit, log=lambda m: print(m, flush=True))
        for line in rep["text"]:
            print("-", line)
        return
    if a.cmd == "surge-train":
        from core import surge_study
        rep = surge_study.run(limit=a.limit, log=lambda m: print(m, flush=True))
        for line in rep["text"]:
            print("-", line)
        return
    if a.cmd == "avoid-train":
        from core import avoid_study
        rep = avoid_study.run(limit=a.limit, log=lambda m: print(m, flush=True))
        for line in rep["text"]:
            print("-", line)
        return
    if a.cmd == "entry-train":
        from core import entry_study
        rep = entry_study.run(limit=a.limit, log=lambda m: print(m, flush=True))
        for line in rep["text"]:
            print("-", line)
        return
    if a.cmd == "ai-train":
        from core import ai
        rep = ai.run(limit=a.limit, log=lambda m: print(m, flush=True))
        for line in rep["text"]:
            print("-", line)
        return
    if a.cmd == "backtest":
        from core import backtest
        rep = backtest.run(limit=a.limit, log=lambda m: print(m, flush=True))
        m = rep["meta"]
        print(f"종목 {m['stocks']} · 신호 {m['signals']} · 기준 표본 {m['baseline_samples']} · {m['params']['elapsed_sec']}초 → data/backtest.json")
        return
    if a.cmd.startswith("yt-"):
        from core import yt
        lg = lambda m: print(m, flush=True)
        if a.cmd == "yt-list":
            print(yt.sync_list())
        elif a.cmd == "yt-meta":
            print(yt.sync_meta(limit=a.limit, kinds=tuple(a.kinds), log=lg))
        elif a.cmd == "yt-clean":
            from core import yt_clean
            print(yt_clean.clean_all(log=lg, only_missing=False))
        elif a.cmd == "yt-captions":
            print(yt.sync_captions(limit=a.limit, log=lg))
        for r in yt.stats():
            print(r)
        return
    if a.cmd == "toss-check":
        from core import toss
        try:
            print(toss.check())
        except toss.TossError as e:
            print("실패:", e)
        return
    db.init_db()
    print("종목 목록:", collector.update_symbols("KRX"))
    codes = list(a.codes)
    if a.all:
        from core import scheduler
        codes += scheduler.all_codes()
    if a.top:
        with db.get_conn() as c:
            codes += [r["code"] for r in c.execute("SELECT code FROM symbols ORDER BY marcap DESC LIMIT ?", (a.top,))]
    codes = list(dict.fromkeys(codes))
    res = collector.update_many(codes, a.years, lambda i, n, c, m: print(f"[{i}/{n}] {c} {m}", flush=True), full=a.full)
    print(res)


if __name__ == "__main__":
    main()
