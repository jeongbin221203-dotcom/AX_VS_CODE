"""오늘의 신호 스냅샷(data/signals.json → snapshot/signals.json)을 stock 브랜치에 올려 Render 가 자동 배포하게 한다.

저장소 규칙(프로젝트별 브랜치에만 커밋)에 맞춰 임시 인덱스로 study/stock 만 담아 .git/hooks/project_branches.py 로 커밋하고
git push origin stock. 다른 세션이 올려 둔 변경은 건드리지 않는다. 비밀·데이터 파일은 올리지 않는다(snapshot/ 한 파일뿐).
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
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
JB = config.BASE / "snapshot" / "jb"                     # 종목별 JB AI 점수·시총 구간별 매수·매도 표시(PC JB차트 '자동'과 같은 계산)
ACTIONS = config.BASE / "snapshot" / "actions.json"      # 신호 화면 '오늘 할 일'
ACT_DAYS = config.BASE / "snapshot" / "actions_days"     # 지난 날 할 일(날짜.json)
LIVE = config.BASE / "snapshot" / "signals_live.json"    # 오늘 장중 신호(core/live_scan — 장중 30분마다)
FULL_DAYS = 90                                           # 최근 90거래일은 전체 목록, 그보다 오래된 날은 추천·강/상만(저장소가 불어나지 않게)


def _recommended_codes():
    """날짜 파일들에서 추천(점검 9/10↑)·강/상으로 나온 종목과 마지막으로 나온 날 + 장중 신호(추천·강/상)·오늘 할 일(살·2차 매수·보유·매도) 종목."""
    last = {}
    extra = []
    for src in (config.DATA_DIR / "signals_live.json",):
        try:
            r = json.loads(src.read_text(encoding="utf-8"))
            d = r["meta"]["date"]
            for k in ("pred", "ai", "high"):
                for z in r.get(k, []):
                    if k == "high" or z["score"] >= r["meta"].get("recommend", 0.9):
                        extra.append((z["code"], d))
        except (OSError, ValueError, KeyError):
            pass
    try:
        from core import jb_daily
        a = jb_daily.load() or {}                            # 15시 장중 할 일이 더 새것이면 그것
        for g in a.get("groups", {}).values():
            for k in ("buy", "add", "hold", "sell"):
                for z in g.get(k, []):
                    extra.append((z["code"], a["date"]))
    except (OSError, ValueError, KeyError):
        pass
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
    for code, d in extra:
        if d > last.get(code, ""):
            last[code] = d
    return last


def chart_meta(cal, thr):
    from core import ai, drop_risk, predict
    info = predict._tech_info(predict.plan())
    mo = drop_risk.load() or {}
    rrep = drop_risk.report() or {}
    labels = [ai.SIGNAL_LABELS[j] for j in range(len(ai.SIGNAL_LABELS))]
    tech = {k: {"mean": v["mean"], "p_up": v["p_up"], "years": v["years"], "rule": v["rule"], "good": v["good"], "ai": v["ai"]} for k, v in info.items()}
    thr = dict(sorted(thr.items()))
    return {"cal": cal, "labels": labels, "dante": predict.DANTE_IDX, "good": [lab for lab in labels if info.get(lab, {}).get("good")], "why": predict.DANTE_WHY,
            "tech": tech, "thr": thr, "thr_now": (list(thr.values())[-1] if thr else None),
            "risk": {"hi": mo.get("cut_hi"), "mid": mo.get("cut_mid"), "certain": drop_risk.CERTAIN_P, "auc": rrep.get("auc_test"), "levels": rrep.get("levels"), "through": rrep.get("live_through")}}


def _chart_work(code):
    """한 종목의 일봉(CHART_FROM~)과 모든 날짜의 단테 신호·AI 확률 — PC 차트와 같은 계산(거래대금 조건 없이 모든 봉)."""
    import numpy as np
    from core import ai, drop_risk, predict, service
    try:
        df = service.load_prices(code)
        if len(df) < 60:
            return None
        X = ai.compute_features(df, ai.MKT_DF, True)
        dates = df.index.strftime("%Y-%m-%d").to_numpy()
        keep = dates >= CHART_FROM
        P = np.full(len(df), np.nan)
        valid = np.isfinite(X[:, ai.NAMES.index("ret60")])
        model = predict._model()
        if model is not None and valid.any():
            P[valid] = model.predict_proba(X[valid])[:, 1]
        # 하락 위험(20일 안 −10%↓ 확률)·전망·이 종목의 지난 적중·급등 뒤 과열 — 휴대폰에서도 PC 와 같게 보이도록 함께 저장
        risk = None
        try:
            R, hi, mid = drop_risk.probs(X)
            heat = (np.nan_to_num(X[:, ai.NAMES.index("ret60")], nan=0) >= 0.8) | (np.nan_to_num(X[:, ai.NAMES.index("dist224")], nan=0) >= 1.0)
            risk = {"q": [(-1 if not np.isfinite(R[i]) else int(round(R[i] * 1000))) for i in np.flatnonzero(keep)], "hz": [int(k) for k, i in enumerate(np.flatnonzero(keep)) if heat[i]],
                    "fc": drop_risk.forecast(X), "tr": predict._risk_track(df, R, hi, mid),
                    "ov": ({"ret60": float(X[-1, ai.NAMES.index("ret60")]), "dist224": float(X[-1, ai.NAMES.index("dist224")])} if heat[-1] else None)}
        except Exception:
            risk = None
        sig = np.column_stack([X[:, ai.NAMES.index(f"sig{j:02d}")] for j in predict.DANTE_IDX])
        mask = ((sig == 0) & np.isfinite(sig)).astype(np.int64) @ (1 << np.array(predict.DANTE_IDX, dtype=np.int64))
        idx = np.flatnonzero(keep)
        rnd = lambda v: int(round(v)) if v >= 1000 else round(float(v), 2)  # noqa: E731
        sub = df.iloc[idx]
        return {"dates": list(dates[idx]), "o": [rnd(v) for v in sub["open"]], "h": [rnd(v) for v in sub["high"]], "l": [rnd(v) for v in sub["low"]], "c": [rnd(v) for v in sub["close"]],
                "v": [int(v) for v in sub["volume"].fillna(0)], "p": [(-1 if not np.isfinite(P[i]) else int(round(P[i] * 1000))) for i in idx],
                "g": [[k, int(mask[i])] for k, i in enumerate(idx) if mask[i]], "risk": risk}
    except Exception:
        return None


def jb_files(log=print) -> int:
    """휴대폰 JB차트용: 스냅샷 차트가 있는 종목마다 기록된 JB AI 점수(core/jb_daily)와 시총 구간별 규칙의 매수·매도 표시를 snapshot/jb/<코드>.json 으로.
    모델을 다시 돌리지 않고 기록된 점수만 쓴다. '오늘 할 일'(data/jb_actions.json)도 snapshot/actions.json 으로 복사."""
    import numpy as np
    from core import jb_daily, jb_rules, picks, service
    JB.mkdir(parents=True, exist_ok=True)
    st = jb_daily.load_store()
    grp_of = {k: v[0] for k, v in ((picks.load_today() or {}).get("scores") or {}).items()}
    n = 0
    for f in sorted(CHARTS.glob("*.json")):
        code = f.stem
        ds, sc = jb_daily.series(code, st)
        if not ds:
            continue
        df = service.load_prices(code)
        df = df[df.index >= "2019-01-01"]
        if len(df) < 60:
            continue
        dates = df.index.strftime("%Y-%m-%d")
        m = dict(zip(ds, sc))
        score = np.array([m.get(d, np.nan) for d in dates], float)
        g = grp_of.get(code)
        o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        tr, pend, aux = jb_rules.simulate(o, h, l, c, score, g, v=df["volume"].to_numpy(float), mkt_dd=jb_rules.market_dd(dates))
        buy_txt, sell_txt = jb_rules.describe(g)
        mk, done = jb_rules.markers(list(dates), o, c, score, tr, aux, h=h, l=l)
        rets = [t["ret"] for t in done]
        hold = jb_rules.holding(list(dates), o, c, tr, aux)
        keep = dates >= CHART_FROM
        now = float(score[-1]) if np.isfinite(score[-1]) else None
        summary = (f"AI 점수 {now * 100:.0f}점({dates[-1]})" if now is not None else "AI 점수 없음") + f" · {g or '소형주'} 규칙."
        if pend is not None:
            summary += " ▶ 매수 신호: " + jb_rules.action(list(dates), o, c, tr, pend, aux, score, g)["title"] + "."
        elif hold:
            summary += " ▶ " + jb_rules.hold_text(hold, sell_txt)
        if rets:
            r = np.array(rets)
            summary += f" ▶ 이 종목 거래 {len(r)}건: 승률 {(r > 0).mean() * 100:.0f}% · 평균 {r.mean() * 100:+.1f}%(학습에 쓰지 않은 구간)."
        obj = {"grp": g, "d": [d for d, k in zip(dates, keep) if k], "s": [(-1 if not np.isfinite(v) else int(round(v * 1000))) for v, k in zip(score, keep) if k],
               "m": [x for x in mk if x["date"] >= CHART_FROM], "sum": summary, "buy": buy_txt, "sell": sell_txt, "note": jb_rules.STATUS.get(g or "", ""),
               "act": jb_rules.action(list(dates), o, c, tr, pend, aux, score, g)}       # 이름 옆 배지(오늘 할 일) — PC 와 같게
        (JB / f"{code}.json").write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        n += 1
    src = config.DATA_DIR / "jb_actions.json"
    if src.exists():
        shutil.copyfile(src, ACTIONS)
    ACT_DAYS.mkdir(parents=True, exist_ok=True)
    k = 0
    for f in sorted(DAYS.glob("*.json"))[-FULL_DAYS:][::-1]:              # 최근 날부터 · 신호 날짜마다 그날 할 일(+그 뒤 결과) — 없는 날만 새로(처음엔 날짜당 약 10초)
        out = ACT_DAYS / f.name
        if out.exists() and time.time() - out.stat().st_mtime < 7 * 86400:      # 이미 있으면 일주일에 한 번만 다시(그 뒤 결과 갱신)
            continue
        rep = jb_daily.for_date(f.stem)
        if rep:
            out.write_text(json.dumps(rep, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            k += 1
    log(f"지난 날 할 일 {k}일 갱신")
    log(f"JB AI 표시 {n}종목")
    return n


def charts(log=print, workers: int = 0) -> int:
    """추천·강/상으로 나온 종목의 일봉(2021~)·단테 신호·AI 확률을 snapshot/charts/<코드>.json 으로. 이미 충분히 긴 최신 형식 파일은 건너뛴다."""
    from concurrent.futures import ProcessPoolExecutor
    from datetime import timedelta
    from core import backtest as bt, db, ai
    CHARTS.mkdir(parents=True, exist_ok=True)
    last = _recommended_codes()
    if not last:
        return 0
    with db.get_conn() as c:
        cal = [r["date"] for r in c.execute("SELECT DISTINCT date FROM prices WHERE date >= ? ORDER BY date", (CHART_FROM,))]
        names = {r["code"]: r["name"] for r in c.execute("SELECT code,name FROM symbols")}
    latest = cal[-1]
    pos = {d: i for i, d in enumerate(cal)}
    todo = []
    for code, d in sorted(last.items()):
        need = min(latest, (datetime.strptime(d, "%Y-%m-%d") + timedelta(days=CHART_AFTER_DAYS)).strftime("%Y-%m-%d"))
        try:
            old = json.loads((CHARTS / f"{code}.json").read_text(encoding="utf-8"))
            if old.get("v") == 3 and old.get("last", "") >= need:
                continue
        except (OSError, ValueError):
            pass
        todo.append(code)
    n = 0
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 2))
    if todo:
        with ProcessPoolExecutor(max_workers=workers, initializer=ai._init_worker, initargs=(bt.MKT_PATH,)) as ex:
            for code, r in zip(todo, ex.map(_chart_work, todo, chunksize=4)):
                if not r:
                    continue
                obj = {"v": 2, "n": names.get(code, code), "last": r["dates"][-1], "d": [pos[x] for x in r["dates"]], "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"], "v": r["v"], "p": r["p"], "g": r["g"], "risk": r["risk"]}
                obj["vol"] = obj.pop("v")
                obj["v"] = 3
                (CHARTS / f"{code}.json").write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                n += 1
                if n % 100 == 0:
                    log(f"차트 [{n}/{len(todo)}] {code}")
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
        extra = [p.relative_to(top).as_posix() for p in (JB, ACTIONS, ACT_DAYS, LIVE) if p.exists()]       # JB AI 표시·오늘/지난 날 할 일·장중 신호
        _git("add", "-A", "-f", "--", rel, DAYS.relative_to(top).as_posix(), CHARTS.relative_to(top).as_posix(), META.relative_to(top).as_posix(), *extra, env=env, cwd=top)
        if not _git("diff", "--cached", "--name-only", env=env, cwd=top):
            return "변경 없음"
        msg = f"스냅샷: {datetime.now():%Y-%m-%d %H:%M} 오늘의 신호 갱신"
        before = _git("rev-parse", "stock", cwd=top)
        out = subprocess.run(["python", str(helper), "commit", "-m", msg], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=top,
                             env={**os.environ, **env, "PYTHONIOENCODING": "utf-8"})   # 도우미의 한글 출력을 cp949 로 읽다 깨져 오류 내용이 비던 것
        if out.returncode and _git("rev-parse", "stock", cwd=top) == before:
            raise RuntimeError(f"커밋 실패: {(out.stderr or out.stdout).strip()[-300:]}")
        # 도우미는 파일이 아주 많을 때 마지막 정리(git reset, WinError 206 명령줄 길이)에서 오류를 내지만 커밋·브랜치 갱신은 이미 끝난 뒤다 — 브랜치가 움직였으면 성공으로 본다
    finally:
        if os.path.exists(idx):
            os.remove(idx)
    _git("push", "origin", "stock", cwd=top)
    log("stock 브랜치 푸시 완료")
    return "푸시 완료"


def push_jb_live(log=print) -> str:
    """15시 장중 할 일(data/jb_actions_live.json)을 휴대폰 '오늘 할 일'로 올린다 — 마감 뒤 확정 결과가 나오면 jb_files 가 다시 덮어씀."""
    from core import jb_daily
    rep = jb_daily.load()
    if not rep or not rep.get("live"):
        return "장중 할 일 없음"
    ACTIONS.write_text(json.dumps(rep, ensure_ascii=False), encoding="utf-8")
    try:
        charts(log)                                          # 새로 나온 살 종목도 눌러 열 수 있게
    except Exception as e:
        log(f"차트 갱신 실패: {e}")
    return push(log)


def push_live(log=print) -> str:
    """장중 신호(data/signals_live.json)를 스냅샷에 복사해 올린다 — Render 신호 화면도 PC 처럼 '오늘 장중 신호'를 보이게."""
    src = config.DATA_DIR / "signals_live.json"
    if not src.exists():
        return "장중 신호 없음"
    shutil.copyfile(src, LIVE)
    try:                                                    # 장중 신호에 새로 나온 종목도 휴대폰에서 눌러 열 수 있게 차트를 만든다
        charts(log)
    except Exception as e:
        log(f"차트 갱신 실패: {e}")
    return push(log)


def run(log=print, do_push: bool = True) -> str:
    if not refresh():
        log("스냅샷이 이미 최신입니다")
        return "변경 없음"
    log("snapshot/signals.json 갱신")
    try:
        log(f"차트 {charts(log)}종목 갱신")
    except Exception as e:                                  # 차트 실패가 신호 게시를 막지 않게
        log(f"차트 갱신 실패: {e}")
    try:
        jb_files(log)
    except Exception as e:
        log(f"JB AI 표시 실패: {e}")
    return push(log) if do_push else "복사만"
