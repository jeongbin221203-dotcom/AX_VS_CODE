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
DST = config.BASE / "snapshot" / "signals.json"        # 최신
CHARTS = config.BASE / "snapshot" / "charts"             # 종목별 일봉·신호·AI 확률(휴대폰 차트용)
META = config.BASE / "snapshot" / "chartmeta.json"
CHART_FROM = "2021-01-01"                                # 224일선을 2022년 첫날부터 그릴 수 있게
CHART_AFTER_DAYS = 130                                   # 기준일 뒤 며칠 더(그 뒤 흐름 확인용)
DAYS = config.BASE / "snapshot" / "days"                 # 날짜별 보관(days/YYYY-MM-DD.json) — 다음 주에도 그날 신호를 볼 수 있게
FULL_DAYS = 90                                           # 최근 90거래일은 전체 목록, 그보다 오래된 날은 추천·강/상만(저장소가 불어나지 않게)


def _recommended_codes():
    """날짜 파일들에서 추천(점검 9/10↑)·강/상으로 나온 종목과 마지막으로 나온 날."""
    last = {}
    for f in sorted(DAYS.glob("*.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        d = f.stem
        for k in ("pred", "ai", "high"):
            for z in r.get(k, []):
                if k == "high" or z["score"] >= r["meta"].get("recommend", 0.9):
                    last[z["code"]] = d
    return last


def chart_meta(cal, thr):
    from core import ai, predict
    info = predict._tech_info(predict.plan())
    labels = [ai.SIGNAL_LABELS[j] for j in range(len(ai.SIGNAL_LABELS))]
    return {"cal": cal, "labels": labels, "dante": predict.DANTE_IDX, "good": [lab for lab in labels if info.get(lab, {}).get("good")], "thr": thr}


def charts(log=print) -> int:
    """추천·강/상으로 나온 종목의 일봉(2021~)·단테 신호·AI 확률을 snapshot/charts/<코드>.json 으로. 이미 충분히 긴 파일은 건너뛴다."""
    from datetime import timedelta
    from core import db, predict
    CHARTS.mkdir(parents=True, exist_ok=True)
    last = _recommended_codes()
    if not last:
        return 0
    with db.get_conn() as c:
        cal = [r["date"] for r in c.execute("SELECT DISTINCT date FROM prices WHERE date >= ? ORDER BY date", (CHART_FROM,))]
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
        latest = cal[-1]
        pos = {d: i for i, d in enumerate(cal)}
        dante = sum(1 << j for j in predict.DANTE_IDX)
        n = 0
        for code, d in sorted(last.items()):
            need = min(latest, (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=CHART_AFTER_DAYS)).strftime("%Y-%m-%d"))
            f = CHARTS / f"{code}.json"
            try:
                if json.loads(f.read_text(encoding="utf-8")).get("last", "") >= need:
                    continue
            except (OSError, ValueError):
                pass
            rows = c.execute("SELECT date,open,high,low,close,volume FROM prices WHERE code=? AND date >= ? ORDER BY date", (code, CHART_FROM)).fetchall()
            if len(rows) < 30:
                continue
            chk = {r["date"]: (r["prob"], r["sig0"]) for r in c.execute("SELECT date,prob,sig0 FROM daily_checks WHERE code=?", (code,))}
            idx = [pos[r["date"]] for r in rows]
            rnd = lambda v: int(round(v)) if v >= 1000 else round(v, 2)  # noqa: E731
            p, g = [], []
            for i, r in enumerate(rows):
                pr, sg = chk.get(r["date"], (None, 0))
                p.append(-1 if pr is None else int(round(pr * 1000)))
                if sg and (int(sg) & dante):
                    g.append([i, int(sg) & dante])
            obj = {"n": names.get(code, code), "last": rows[-1]["date"], "d": idx, "o": [rnd(r["open"]) for r in rows], "h": [rnd(r["high"]) for r in rows],
                   "l": [rnd(r["low"]) for r in rows], "c": [rnd(r["close"]) for r in rows], "v": [int(r["volume"] or 0) for r in rows], "p": p, "g": g}
            f.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            n += 1
            if n % 200 == 0:
                log(f"차트 [{n}] {code}")
    thr = {}
    for f in DAYS.glob("*.json"):
        try:
            m = json.loads(f.read_text(encoding="utf-8"))["meta"]
            thr[f.stem] = round(m["thr10"], 4)
        except (OSError, ValueError, KeyError, TypeError):
            pass
    META.write_text(json.dumps(chart_meta(cal, thr), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return n


def refresh() -> bool:
    """snapshot/signals.json 을 최신 훑기 결과로 바꾼다. 바뀐 게 없으면 False."""
    try:
        new = json.loads(SRC.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RuntimeError("data/signals.json 이 없습니다 (python manage.py signals-scan 먼저)")
    if not new.get("meta", {}).get("date"):
        raise RuntimeError("신호 결과가 비어 있어 올리지 않습니다")
    DST.parent.mkdir(parents=True, exist_ok=True)
    DAYS.mkdir(parents=True, exist_ok=True)
    day = DAYS / f"{new['meta']['date']}.json"
    changed = False
    for target in (DST, day):
        try:
            if json.loads(target.read_text(encoding="utf-8")) == new:
                continue
        except (OSError, ValueError):
            pass
        shutil.copyfile(SRC, target)
        changed = True
    return compact_old() or changed


def trim(rep):
    """오래된 날짜용 압축: 추천(점검 9/10↑)·강/상만 남기고 나머지 종목은 뺀다."""
    if rep.get("meta", {}).get("trimmed"):
        return rep
    rec = rep["meta"].get("recommend", 0.9)
    rep["meta"]["trimmed"] = True
    rep["meta"]["n_pred"], rep["meta"]["n_ai"] = len(rep.get("pred", [])), len(rep.get("ai", []))
    for k in ("pred", "ai"):
        rep[k] = [r for r in rep.get(k, []) if r["score"] >= rec]
    return rep


def compact_old() -> bool:
    """최근 FULL_DAYS 일보다 오래된 날짜 파일을 압축본으로 바꾼다."""
    changed = False
    for f in sorted(DAYS.glob("*.json"))[:-FULL_DAYS]:
        try:
            rep = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rep.get("meta", {}).get("trimmed"):
            continue
        f.write_text(json.dumps(trim(rep), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        changed = True
    return changed


def backfill(since="2022-01-03", log=print) -> int:
    """로컬 신호 이력(daily_checks)에서 지난 날짜들을 모두 만들어 snapshot/days 에 넣는다(없는 날짜만). 최근 FULL_DAYS 는 전체, 나머지는 압축."""
    from core import signal_scan as ss
    DAYS.mkdir(parents=True, exist_ok=True)
    with __import__("core.db", fromlist=["db"]).get_conn() as c:
        dates = [r["date"] for r in c.execute("SELECT DISTINCT date FROM daily_checks WHERE date >= ? ORDER BY date", (since,))]
    n = 0
    for i, d in enumerate(dates):
        f = DAYS / f"{d}.json"
        if f.exists():
            continue
        rep = ss.for_date(d)
        for k in ("asked", "prev", "next", "latest"):
            rep["meta"].pop(k, None)
        old = i < len(dates) - FULL_DAYS
        if old:
            rep = trim(rep)
        f.write_text(json.dumps(rep, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        n += 1
        if n % 200 == 0:
            log(f"[{n}] {d}")
    return n


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
        _git("add", "-A", "-f", "--", rel, DAYS.relative_to(top).as_posix(), CHARTS.relative_to(top).as_posix(), META.relative_to(top).as_posix(), env=env, cwd=top)
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
    try:
        log(f"차트 {charts(log)}종목 갱신")
    except Exception as e:                                  # 차트 실패가 신호 게시를 막지 않게
        log(f"차트 갱신 실패: {e}")
    return push(log) if do_push else "복사만"
