"""스냅샷 앱(Render)에서 PC 앱의 '차트 비교' 화면을 그대로 쓰기 위한 데이터 API.

compare.js 가 부르는 /api/chart · /api/predict · /api/search 를 snapshot/charts/<코드>.json(일봉·신호 비트·AI 확률)과
snapshot/chartmeta.json(달력·신호 이름·기법 성과·AI 경계)으로 대신 답한다. pandas·AI 모델 없이 순수 파이썬만 쓴다.
계산 방식은 core/service.py(차트)·core/predict.py(예측·AI)와 같다.
"""
import json
import re
from datetime import date as _date
from functools import lru_cache

CODE_RE = re.compile(r"^[A-Za-z0-9]{1,12}$")
MA_PERIODS = (5, 14, 20, 28, 56, 60, 112, 120, 224, 448)


class Store:
    def __init__(self, charts_dir, meta_path):
        self.dir, self.meta_path = charts_dir, meta_path
        self._meta = (None, None)
        self._names = (None, None)

    def meta(self):
        try:
            mt = self.meta_path.stat().st_mtime
        except OSError:
            return None
        if self._meta[0] != mt:
            self._meta = (mt, json.loads(self.meta_path.read_text(encoding="utf-8")))
        return self._meta[1]

    def has(self, code):
        return bool(CODE_RE.match(code)) and (self.dir / f"{code}.json").exists()

    def raw(self, code):
        f = self.dir / f"{code}.json"
        return _load(str(f), f.stat().st_mtime)

    def names(self):
        try:
            mt = max((p.stat().st_mtime for p in self.dir.glob("*.json")), default=0)
        except OSError:
            return []
        if self._names[0] != mt:
            out = []
            for p in self.dir.glob("*.json"):
                try:
                    with open(p, encoding="utf-8") as fh:
                        head = fh.read(200)
                    m = re.search(r'"n"\s*:\s*"([^"]*)"', head)
                    out.append((p.stem, m.group(1) if m else p.stem))
                except OSError:
                    pass
            self._names = (mt, sorted(out, key=lambda x: x[1]))
        return self._names[1]


@lru_cache(maxsize=24)
def _load(path, _mtime):
    return json.loads(open(path, encoding="utf-8").read())


def _group_key(day, tf):
    if tf == "D":
        return day
    y, m, d = int(day[:4]), int(day[5:7]), int(day[8:10])
    if tf == "M":
        return day[:7]
    iso = _date(y, m, d).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def bars(store, code, tf):
    """일봉 → tf 봉. 반환: times, o, h, l, c, v, day→봉 시각 매핑(신호·확률을 봉에 옮길 때)."""
    raw, cal = store.raw(code), store.meta()["cal"]
    days = [cal[i] for i in raw["d"]]
    vol = raw.get("vol") or raw["v"]
    if tf == "D":
        return days, raw["o"], raw["h"], raw["l"], raw["c"], vol, {d: d for d in days}
    times, o, h, l, c, v, mp = [], [], [], [], [], [], {}
    cur = None
    for i, d in enumerate(days):
        k = _group_key(d, tf)
        if k != cur:
            cur = k
            times.append(d)
            o.append(raw["o"][i]); h.append(raw["h"][i]); l.append(raw["l"][i]); c.append(raw["c"][i]); v.append(vol[i])
        else:
            times[-1] = d
            h[-1] = max(h[-1], raw["h"][i]); l[-1] = min(l[-1], raw["l"][i]); c[-1] = raw["c"][i]; v[-1] += vol[i]
        mp[d] = None
    # 날짜 → 그 봉의 마지막 날짜(봉 시각)
    cur, group_days = None, []
    out = {}
    for d in days:
        k = _group_key(d, tf)
        if k != cur:
            for gd in group_days:
                out[gd] = group_days[-1]
            cur, group_days = k, []
        group_days.append(d)
    for gd in group_days:
        out[gd] = group_days[-1]
    return times, o, h, l, c, v, out


def sma(c, n):
    out, s = [], 0.0
    for i, x in enumerate(c):
        s += x
        if i >= n:
            s -= c[i - n]
        out.append(round(s / n, 4) if i >= n - 1 else None)
    return out


def chart_payload(store, code, tf, nbars):
    times, o, h, l, c, v, _ = bars(store, code, tf)
    nbars = max(1, min(nbars, len(times)))
    s = len(times) - nbars
    cut = lambda arr: arr[s:]  # noqa: E731
    candles = [{"time": t, "open": a, "high": b, "low": d, "close": e} for t, a, b, d, e in zip(cut(times), cut(o), cut(h), cut(l), cut(c))]
    volume = [{"time": t, "value": float(x), "up": bool(e >= a)} for t, x, e, a in zip(cut(times), cut(v), cut(c), cut(o))]
    ma = {}
    for n in MA_PERIODS:
        vals = sma(c, n)
        ma[str(n)] = [{"time": t, "value": x} for t, x in zip(times[s:], vals[s:]) if x is not None]
    last = float(c[-1])
    prev = float(c[-2]) if len(c) > 1 else last
    return {"code": code, "tf": tf, "candles": candles, "volume": volume, "ma": ma, "bb": {"upper": [], "mid": [], "lower": []}, "rsi": [],
            "macd": {"macd": [], "signal": [], "hist": []}, "stoch": {"k": [], "d": []}, "signals": [], "patterns": [], "levels": [],
            "last": {"date": times[-1], "close": last, "change": (last / prev - 1) * 100 if prev else 0, "volume": float(v[-1])}}


def _labels_of(meta, mask):
    return [meta["labels"][j] for j in meta["dante"] if mask >> j & 1]


def dante(store, code, tf, nbars):
    meta, raw = store.meta(), store.raw(code)
    times, _o, _h, _l, _c, _v, mp = bars(store, code, tf)
    days = [meta["cal"][i] for i in raw["d"]]
    tech = meta.get("tech", {})
    good = set(meta.get("good", []))
    start_day = days[max(len(days) - nbars * {"D": 1, "W": 5, "M": 21}[tf], 0)]
    markers, seen = [], set()
    for i, mask in raw["g"]:
        if days[i] < start_day:
            continue
        d = mp[days[i]]
        for lab in _labels_of(meta, mask):
            if (d, lab) in seen:
                continue
            seen.add((d, lab))
            t = tech.get(lab)
            markers.append({"date": d, "side": "buy", "label": lab, "why": meta.get("why", {}).get(lab, ""), "good": lab in good,
                            "stat": (f"처음 보는 해 평균 {t['mean'] * 100:+.2f}%·상승 {t['p_up'] * 100:.0f}% ({t['years']}년 플러스)" if t and t.get("mean") is not None else "")})
    markers.sort(key=lambda m: m["date"])
    n = len(days)
    last_mask = {}
    for i, mask in raw["g"]:
        for lab in _labels_of(meta, mask):
            last_mask[lab] = i
    recent = []
    for lab, i in last_mask.items():
        ago = n - 1 - i
        if 0 <= ago <= 20:
            t = tech.get(lab)
            recent.append({"label": lab, "ago": ago, "good": lab in good, "rule": t["rule"] if t else "", "mean": t["mean"] if t else None, "p_up": t["p_up"] if t else None})
    recent.sort(key=lambda r: r["ago"])
    if recent:
        parts = []
        for r in recent:
            s = f"{r['label']} {r['ago']}일 전" if r["ago"] else f"{r['label']} 오늘"
            if r["mean"] is not None:
                s += f"({'✔' if r['good'] else '✖'} {r['mean'] * 100:+.1f}%·{r['p_up'] * 100:.0f}%)"
            parts.append(s)
        summary = "최근 20일 단테 신호: " + ", ".join(parts) + ". ✔ = 처음 보는 해에서 규칙이 통한 기법."
        best = next((r for r in recent if r["good"]), None)
        if best and best["rule"]:
            summary += f" 규칙({best['label']}): {best['rule']}."
    else:
        summary = "최근 20일 안에 단테 기법 신호가 없습니다."
    return {"name": "예측", "kind": "dante", "markers": markers, "recent": recent, "summary": summary,
            "note": "단테 계열 14개 신호(256·이평 때리기·224·밥그릇·3번 자리·공구리·역매공파·매집봉·세력선). 괄호는 매매 계획의 '처음 보는 해' 성과(규칙대로, 비용 포함)."}


def ai_pred(store, code, tf, nbars):
    meta, raw = store.meta(), store.raw(code)
    times, _o, _h, _l, _c, _v, mp = bars(store, code, tf)
    days = [meta["cal"][i] for i in raw["d"]]
    thr = meta.get("thr_now") or (list(meta["thr"].values())[-1] if meta.get("thr") else 0.138)
    p = [None if x < 0 else x / 1000 for x in raw["p"]]
    n = len(days)
    start = max(n - nbars * {"D": 1, "W": 5, "M": 21}[tf], 0)
    seen = {}
    for i in range(start, n):
        if p[i] is not None:
            seen[mp[days[i]]] = p[i]
    prob = [{"time": d, "value": round(v, 4)} for d, v in seen.items()]
    markers = []
    for i in range(start, n):
        if p[i] is not None and p[i] >= thr and (i == 0 or p[i - 1] is None or p[i - 1] < thr):
            d = mp[days[i]]
            if markers and markers[-1]["date"] == d:
                continue
            markers.append({"date": d, "side": "buy", "label": "AI 상위 10%", "why": f"20일 +20% 확률 {p[i] * 100:.1f}% (경계 {thr * 100:.1f}%)"})
    ai_t = next((v for v in meta.get("tech", {}).values() if v.get("ai")), None)
    p_now = p[-1]
    if p_now is None:
        summary = "AI 확률을 계산할 데이터가 부족합니다(250봉 이상 필요)."
    else:
        summary = f"오늘 AI 확률 {p_now * 100:.1f}% — 상위 10% 경계 {thr * 100:.1f}% {'위 ✔' if p_now >= thr else '아래'}."
        if ai_t and ai_t.get("mean") is not None:
            summary += f" AI 상위 10%는 처음 보는 해 평균 {ai_t['mean'] * 100:+.2f}%·상승 {ai_t['p_up'] * 100:.0f}%({ai_t['years']}년 플러스), 규칙: {ai_t['rule']}."
    return {"name": "AI", "kind": "ai", "prob": prob, "thr": thr, "markers": markers, "latest": {"prob": p_now, "top": bool(p_now is not None and p_now >= thr)},
            "rule": ai_t, "summary": summary,
            "note": "선 = 날짜마다 그날까지의 정보로 계산한 '20일 안 +20%' 확률(2021년까지 학습한 모델). 점선 = 상위 10% 경계, 마커 = 경계를 넘어선 날."}


def search(store, q):
    q = q.strip().lower()[:30]
    if not q:
        return []
    out = [{"code": c, "name": n, "market": ""} for c, n in store.names() if q in n.lower() or q in c.lower()]
    out.sort(key=lambda r: (r["name"].lower() != q, r["name"]))
    return out[:15]
