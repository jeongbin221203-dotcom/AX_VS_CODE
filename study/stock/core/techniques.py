"""기법(태그)별 차트 적용. 각 기법은 detect(df) → 차트에 그릴 마커·선·가격대·요약을 돌려준다.

status
  ready   : 정의가 명확해 바로 적용 (아래 rules 에 조건을 그대로 적어 둠 — 단테 고유 규칙이 아니라 일반적인 정의)
  pending : 단테 고유 규칙이라 자막에서 조건을 확인한 뒤 구현 (지금은 적용 안 함)
  na      : 차트로 나타낼 수 없는 주제 (재료·테마, 초보·기초)
"""
import numpy as np
import pandas as pd

from core import indicators as ind
from core import patterns

BUY, SELL, INFO = "buy", "sell", "info"


def _d(ix):
    return ix.strftime("%Y-%m-%d")


def _line(s: pd.Series, bars):
    s = s.iloc[-bars:]
    v = s.to_numpy(float)
    keep = ~np.isnan(v)
    if not keep.any():
        return []
    times = s.index[keep].strftime("%Y-%m-%d")          # 날짜마다 strftime 을 부르면 1만 봉에 수 초 — 한 번에 변환
    vals = np.round(v[keep], 4).tolist()
    return [{"time": t, "value": x} for t, x in zip(times, vals)]


def _marks(df, mask, side, label, why, bars, min_gap=0):
    """mask(True 인 날)마다 마커. why(i) → 근거 문장. min_gap 봉 이내 연속 신호는 첫 번째만."""
    out, last = [], -10**9
    arr = mask.fillna(False).to_numpy()
    start = max(len(df) - bars, 0)
    for i in range(start, len(df)):
        if arr[i] and i - last > min_gap:
            out.append({"date": _d(df.index[i]), "side": side, "label": label, "why": why(i)})
            last = i
    return out


def _result(markers=None, lines=None, levels=None, summary=""):
    return {"markers": markers or [], "lines": lines or [], "levels": levels or [], "summary": summary}


# ---------------------------------------------------------------- 이평선
def trend_ma(df, bars):
    c = df["close"]
    m = {n: ind.sma(c, n) for n in (5, 20, 60, 120)}
    aligned = (m[5] > m[20]) & (m[20] > m[60]) & (m[60] > m[120])
    reversed_ = (m[5] < m[20]) & (m[20] < m[60]) & (m[60] < m[120])
    up_start = aligned & ~aligned.shift().fillna(False).astype(bool)
    down_start = reversed_ & ~reversed_.shift().fillna(False).astype(bool)
    g60 = (m[20].shift() <= m[60].shift()) & (m[20] > m[60])
    d60 = (m[20].shift() >= m[60].shift()) & (m[20] < m[60])
    marks = (_marks(df, up_start, BUY, "정배열", lambda i: "5>20>60>120일선 순서로 정렬(정배열) 시작", bars)
             + _marks(df, down_start, SELL, "역배열", lambda i: "5<20<60<120일선(역배열) 시작", bars)
             + _marks(df, g60, BUY, "20/60 골든", lambda i: "20일선이 60일선을 위로 돌파", bars)
             + _marks(df, d60, SELL, "20/60 데드", lambda i: "20일선이 60일선 아래로 하락", bars))
    colors = {5: "#e0a000", 20: "#d6479b", 60: "#2a9d5c", 120: "#8a5cd6"}
    lines = [{"name": f"{n}일선", "color": colors[n], "data": _line(m[n], bars)} for n in m]
    state = "정배열" if aligned.iloc[-1] else "역배열" if reversed_.iloc[-1] else "혼조(배열 아님)"
    slope = "상승" if m[60].iloc[-1] > m[60].iloc[-6] else "하락"
    return _result(marks, lines, summary=f"현재 이평선 배열: {state} · 60일선 {slope} 중")


# ---------------------------------------------------------------- 지지·저항
def _trendline(df, kind, win=5, lookback=250):
    """최근 두 피벗 고점(또는 저점)을 이은 추세선(고점-고점 / 저점-저점). 오른쪽 win봉이 지난 확정 피벗만 사용."""
    col = df["high" if kind == "high" else "low"].to_numpy(float)
    n = len(df)
    f = max if kind == "high" else min
    piv = [i for i in range(max(n - lookback, win), n - win) if col[i] == f(col[i - win:i + win + 1])]
    if len(piv) < 2:
        return None, 0.0
    a, b = piv[-2], piv[-1]
    slope = (col[b] - col[a]) / (b - a)
    end = n - 1
    return [{"time": _d(df.index[a]), "value": float(col[a])},
            {"time": _d(df.index[end]), "value": float(col[b] + slope * (end - b))}], slope


def support_resistance(df, bars):
    levels = [{"price": l["price"], "title": f"{'저항' if l['kind'] == 'resistance' else '지지'} {l['touches']}회",
               "color": "#d6362f" if l["kind"] == "resistance" else "#2b62d9"}
              for l in patterns.support_resistance(df)]
    lines, tdesc = [], []
    for kind, color, name in (("high", "#d6362f", "고점 추세선"), ("low", "#2b62d9", "저점 추세선")):
        pts, slope = _trendline(df, kind)
        if pts:
            lines.append({"name": name, "color": color, "data": pts, "dash": True})
            tdesc.append(f"{name} {'상승' if slope > 0 else '하락'}")
    last = float(df["close"].iloc[-1])
    near = [l for l in levels if abs(l["price"] / last - 1) <= 0.03]
    s = ("현재가 ±3% 안에 " + ", ".join(f"{l['title']}({l['price']:,.0f})" for l in near)) if near else "현재가 ±3% 안에 주요 가격대 없음"
    if tdesc:
        s += " · " + ", ".join(tdesc)
    return _result(lines=lines, levels=levels, summary=s)


# ---------------------------------------------------------------- 거래량
def volume(df, bars):
    c, o, v = df["close"], df["open"], df["volume"]
    avg = ind.sma(v, 20).shift()
    surge = v >= avg * 2
    dry = (v <= avg * 0.4) & (c.pct_change().abs() <= 0.01)
    marks = (_marks(df, surge & (c >= o), BUY, "거래량↑양봉", lambda i: f"거래량 {v.iloc[i] / avg.iloc[i]:.1f}배(20일 평균 대비) + 양봉", bars, 2)
             + _marks(df, surge & (c < o), SELL, "거래량↑음봉", lambda i: f"거래량 {v.iloc[i] / avg.iloc[i]:.1f}배 + 음봉(매물 출회)", bars, 2)
             + _marks(df, dry, INFO, "거래량 마름", lambda i: "거래량이 20일 평균의 40% 이하, 가격 변동 1% 이내(매물 소화)", bars, 5))
    ratio = v.iloc[-1] / avg.iloc[-1] if avg.iloc[-1] else float("nan")
    return _result(marks, summary=f"오늘 거래량은 20일 평균의 {ratio:.1f}배")


# ---------------------------------------------------------------- 돌파·신고가
def breakout(df, bars):
    c, h, v = df["close"], df["high"], df["volume"]
    box_hi = h.rolling(20, min_periods=20).max().shift()
    box_lo = df["low"].rolling(20, min_periods=20).min().shift()
    avg = ind.sma(v, 20).shift()
    hi52 = c.rolling(250, min_periods=120).max().shift()
    brk = (c > box_hi) & (v >= avg * 1.5)
    new_high = c > hi52
    marks = (_marks(df, brk, BUY, "박스 돌파", lambda i: f"종가 {c.iloc[i]:,.0f}이 직전 20일 고점 {box_hi.iloc[i]:,.0f} 돌파 + 거래량 {v.iloc[i] / avg.iloc[i]:.1f}배", bars, 3)
             + _marks(df, new_high, BUY, "52주 신고가", lambda i: f"종가가 직전 52주 고가 {hi52.iloc[i]:,.0f} 돌파", bars, 3))
    lines = [{"name": "20일 고점", "color": "#d6362f", "data": _line(box_hi, bars), "dash": True},
             {"name": "20일 저점", "color": "#2b62d9", "data": _line(box_lo, bars), "dash": True}]
    gap = (c.iloc[-1] / box_hi.iloc[-1] - 1) * 100
    return _result(marks, lines, summary=f"현재가는 직전 20일 고점 대비 {gap:+.1f}%")


# ---------------------------------------------------------------- 눌림목
def pullback(df, bars):
    c, o, h, v = df["close"], df["open"], df["high"], df["volume"]
    ma20, ma60 = ind.sma(c, 20), ind.sma(c, 60)
    avg = ind.sma(v, 20).shift()
    uptrend = (c > ma60) & (ma20 > ma60) & (ma60 > ma60.shift(5))
    ran_up = (c / ma20).rolling(15, min_periods=15).max().shift() >= 1.08       # 최근 15일 안에 20일선 +8% 이상 상승
    near = (c.shift() >= ma20.shift() * 0.97) & (c.shift() <= ma20.shift() * 1.03)  # 어제 20일선 ±3% 안(눌림)
    quiet = v.shift() <= avg.shift() * 1.0                                         # 눌림 구간 거래량 감소
    trigger = (c > o) & (c > h.shift())                                           # 어제 고가를 넘는 양봉(반등 확인)
    mask = uptrend & ran_up & near & quiet & trigger
    marks = _marks(df, mask, BUY, "눌림목 반등", lambda i: f"상승 추세에서 20일선({ma20.iloc[i - 1]:,.0f}) 부근까지 눌린 뒤 어제 고가 {h.iloc[i - 1]:,.0f}를 넘는 양봉", bars, 5)
    lines = [{"name": "20일선", "color": "#d6479b", "data": _line(ma20, bars)}, {"name": "60일선", "color": "#2a9d5c", "data": _line(ma60, bars)}]
    now = "눌림 구간(20일선 ±3%)" if (0.97 <= c.iloc[-1] / ma20.iloc[-1] <= 1.03 and uptrend.iloc[-1]) else "눌림 구간 아님"
    return _result(marks, lines, summary=f"현재: {now}")


# ---------------------------------------------------------------- 캔들
def candle(df, bars):
    first = _d(df.index[max(len(df) - bars, 0)])
    marks = [{"date": p["date"], "side": p["side"], "label": p["label"], "why": f"{p['label']} 캔들 패턴"}
             for p in patterns.candles(df) if p["date"] >= first and p["key"] != "doji"]
    last = marks[-1] if marks else None
    return _result(marks, summary=f"가장 최근 패턴: {last['date']} {last['label']}" if last else "최근 패턴 없음")


# ---------------------------------------------------------------- 256기법 (5·20·60 / 5·112·224)
def ma256(df, bars):
    """단테 256기법: 장기 이평 역배열에서 단기 이평(5일선)이 중기 이평(20일선)을 골든크로스 하되
    장기 이평(60일선)은 아직 위에 있는 자리. 20일선을 손절선으로 매수 → 60일선을 뚫으면 추세 전환.
    중장기 버전은 20→112, 60→224 (6개월·1년 지지선)."""
    c = df["close"]
    out_m, out_l, notes = [], [], []
    sets = [((5, 20, 60), "단기", 40, {5: "#e0a000", 20: "#d6479b", 60: "#2a9d5c"}),
            ((5, 112, 224), "중장기", 80, {5: "#e0a000", 112: "#2a9d5c", 224: "#555555"})]
    for (a, b, d), tag, look, colors in sets:
        ma = {n: ind.sma(c, n) for n in (a, b, d)}
        rev = ((ma[d] > ma[b]) & (ma[b] > ma[a])).astype(float).rolling(look, min_periods=1).max().astype(bool)  # 최근 역배열 이력
        cross = (ma[a].shift() <= ma[b].shift()) & (ma[a] > ma[b]) & (ma[b] < ma[d]) & rev.shift().fillna(False).astype(bool)
        zone = (ma[a] > ma[b]) & (ma[b] < ma[d])
        zone_recent = zone.astype(float).rolling(look, min_periods=1).max().astype(bool)
        done = (c.shift() <= ma[d].shift()) & (c > ma[d]) & (ma[a] > ma[b]) & zone_recent.shift().fillna(False).astype(bool)
        out_m += _marks(df, cross, BUY, f"256 자리({tag})",
                        lambda i, a=a, b=b, d=d, ma=ma: f"{a}일선이 {b}일선을 위로 돌파, {d}일선({ma[d].iloc[i]:,.0f})은 아직 위 — {b}일선({ma[b].iloc[i]:,.0f})을 손절 기준으로 매수", bars, 10)
        out_m += _marks(df, done, BUY, f"256 완성({tag})",
                        lambda i, d=d, ma=ma: f"종가가 {d}일선({ma[d].iloc[i]:,.0f}) 돌파 — 추세 전환 확인", bars, 10)
        for n in (a, b, d):
            if not any(l["name"] == f"{n}일선" for l in out_l):
                out_l.append({"name": f"{n}일선", "color": colors[n], "data": _line(ma[n], bars)})
        notes.append(f"{tag}: " + ("256 자리 (5>20, 60 위)" if zone.iloc[-1] else "256 자리 아님"))
    return _result(out_m, out_l, summary=" · ".join(notes) + " · 매집봉이 함께 있으면 확률이 더 높다고 설명(매집봉 조건은 미확정)")


# ---------------------------------------------------------------- 이평 때리기 (112·224·448)
def ma_strike(df, bars):
    """단테 이평 때리기: 큰 하락 뒤 이평선이 역배열로 벌어졌다가, 바닥에서 공구리를 만들고 112일선(6개월)을 뚫어 안착하면
    224일선·448일선까지가 '공짜 라인'. 112선은 개미가 못 뚫는 선으로 본다."""
    c = df["close"]
    m = {n: ind.sma(c, n) for n in (112, 224, 448)}
    spread = m[224] / m[112] - 1                                # 224가 112 위로 벌어진 정도(역배열 확장)
    had = (spread >= 0.10).astype(float).rolling(120, min_periods=1).max().astype(bool).shift().fillna(False).astype(bool)
    up112 = (c.shift() <= m[112].shift()) & (c > m[112]) & had
    up224 = (c.shift() <= m[224].shift()) & (c > m[224]) & had
    up448 = (c.shift() <= m[448].shift()) & (c > m[448])
    marks = (_marks(df, up112, BUY, "이평 때리기(112)", lambda i: f"역배열이 벌어진 뒤 종가가 112일선({m[112].iloc[i]:,.0f}) 돌파 — 224일선({m[224].iloc[i]:,.0f})까지 공짜 라인", bars, 10)
             + _marks(df, up224, BUY, "224 돌파", lambda i: f"종가가 224일선({m[224].iloc[i]:,.0f}) 돌파 — 448일선까지 공짜 라인", bars, 10)
             + _marks(df, up448, INFO, "448 돌파", lambda i: f"종가가 448일선({m[448].iloc[i]:,.0f}) 돌파", bars, 10))
    lines = [{"name": "112일선", "color": "#2a9d5c", "data": _line(m[112], bars)},
             {"name": "224일선", "color": "#555555", "data": _line(m[224], bars)},
             {"name": "448일선", "color": "#2b62d9", "data": _line(m[448], bars), "dash": True}]
    last = float(c.iloc[-1])
    parts = [f"{n}일선 {(m[n].iloc[-1] / last - 1) * 100:+.1f}%" for n in (112, 224, 448) if not np.isnan(m[n].iloc[-1])]
    return _result(marks, lines, summary="현재가 대비 " + ", ".join(parts) + " · 급락이 크게 나와 이격이 벌어졌을 때 유리(수렴형은 256)")


# ---------------------------------------------------------------- 밥그릇 (224일선 기준)
def rice_bowl(df, bars, ref=224, months=80):
    """단테 밥그릇: ① 급락(저평가) ② 224일선 아래 4개월(≈80봉) 이상 횡보하며 매집 ③ 224일선을 뚫고 눌러 주며 흔들기 ④ 안착 후 슈팅.
    초보 규칙: 224일선 밑에서 4개월 이상 있던 종목이 224일선 ±10% 안에 들어오면 관심."""
    c, lo = df["close"], df["low"]
    ma = ind.sma(c, ref)
    long_below = (c < ma).astype(float).rolling(months, min_periods=months).mean() >= 0.8
    lb_prev = long_below.shift().fillna(False).astype(bool)
    near = lb_prev & ((c / ma - 1).abs() <= 0.10)
    brk = (c.shift() <= ma.shift()) & (c > ma) & lb_prev
    brk_recent = brk.astype(float).rolling(30, min_periods=1).max().astype(bool).shift().fillna(False).astype(bool)
    shake = brk_recent & (lo <= ma * 1.03) & (c > ma) & ~brk
    marks = (_marks(df, near & ~near.shift().fillna(False).astype(bool), INFO, "224 부근(관심)", lambda i: f"224일선 밑 4개월+ 있던 종목이 224일선({ma.iloc[i]:,.0f}) ±10% 안으로 진입(수렴)", bars, 20)
             + _marks(df, brk, BUY, "밥그릇 돌파", lambda i: f"4개월+ 224일선 아래에 있다가 종가가 224일선({ma.iloc[i]:,.0f}) 돌파", bars, 20)
             + _marks(df, shake, BUY, "3번 자리(눌림)", lambda i: "돌파 후 224일선 부근까지 눌렸다가 위에서 마감 — 흔들기 후 안착 확인", bars, 10))
    lines = [{"name": "224일선", "color": "#555555", "data": _line(ma, bars)},
             {"name": "112일선", "color": "#2a9d5c", "data": _line(ind.sma(c, 112), bars)}]
    st = "4개월+ 224일선 아래 → 224 ±10% 안(관심 구간)" if near.iloc[-1] else \
         "224일선 아래 4개월+ 진행 중" if long_below.iloc[-1] else "밥그릇 대기 상태 아님"
    return _result(marks, lines, summary=f"현재: {st}")


# ---------------------------------------------------------------- 공구리 (직전 언덕 돌파)
def concrete(df, bars, win=5):
    """단테 공구리: 하락하던 주가의 '바로 직전 언덕'(직전 반등 고점)을 뚫어 올리면 그 자리가 지지가 된다(바닥 탈피).
    뚫었다가 다시 이탈하면 오히려 큰 저항 → 빠르게 정리."""
    h, c, lo = df["high"].to_numpy(float), df["close"].to_numpy(float), df["low"].to_numpy(float)
    n = len(df)
    piv = [i for i in range(win, n - win) if h[i] == h[i - win:i + win + 1].max()]  # i+win 봉에 확정됨
    marks, last_break, hill_now = [], None, None
    k = 0
    for t in range(max(n - bars, 30), n):
        while k < len(piv) and piv[k] + win <= t - 1:           # 어제까지 확정된 언덕만 사용(미래 정보 금지)
            k += 1
        hill = None
        for p in piv[max(k - 30, 0):k][::-1]:                  # 최근 언덕부터(오래된 것까지 훑지 않음)
            if lo[p:t].min() <= h[p] * 0.92:                  # 그 뒤로 8%+ 내려온 언덕만
                hill = p
                break
        if hill is None:
            continue
        top = h[hill]
        hill_now = (hill, top)
        if c[t] > top >= c[t - 1] and (last_break is None or t - last_break[0] > 10):
            last_break = (t, top)
            marks.append({"date": _d(df.index[t]), "side": BUY, "label": "공구리(언덕 돌파)",
                          "why": f"직전 언덕({_d(df.index[hill])} 고점 {top:,.0f})을 종가로 돌파 — 지지 형성 후보",
                          "ctx": {"hill": float(top), "trough": float(lo[hill:t].min())}})
        elif last_break and t - last_break[0] <= 5 and c[t] < last_break[1] * 0.98 <= c[t - 1]:
            marks.append({"date": _d(df.index[t]), "side": SELL, "label": "공구리 실패",
                          "why": f"돌파한 언덕({last_break[1]:,.0f}) 아래로 다시 이탈 — 저항으로 바뀔 수 있어 빠른 정리"})
    lv = [{"price": float(hill_now[1]), "title": f"직전 언덕 {_d(df.index[hill_now[0]])}", "color": "#8a5cd6"}] if hill_now else []
    return _result(marks, levels=lv, summary="직전 언덕을 종가로 넘으면 공구리(지지) 후보, 5일 안에 2% 넘게 다시 내려오면 실패")


# ---------------------------------------------------------------- 매집봉
def acc_candles(df):
    """단테 매집봉(자막 확인): '개미 물량·매물대의 매물을 흡수하는 캔들' — 올렸다가 눌러 주며 전고 언덕/장기 이평 매물을 먹는다.
    형태: ① 장대양봉(몸통 5%↑) ② 윗꼬리형(시가 대비 7%↑ 올렸다가 윗꼬리가 봉 길이의 절반↑) ③ 112·224일선을 꼬리로 찌르고 그 아래서 마감.
    모두 거래량이 20일 평균의 2배↑일 때(②·③은 ③ 제외 거래량 조건 유지).
    위치: 224일선 아래(또는 ±5%)이고 바닥이 이미 10봉↑ 지난 자리 — '내려가는 도중(1번 자리)'의 매집봉은 건드리지 않는다.
    반환: (acc 불리언 배열, 종류 문자열 배열)"""
    o, h, lo, c, v = (df[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    close = df["close"]
    ma224, ma112 = ind.sma(close, 224).to_numpy(float), ind.sma(close, 112).to_numpy(float)
    avg_v = ind.sma(df["volume"], 20).shift().to_numpy(float)
    rng = np.maximum(h - lo, 1e-9)
    upper = h - np.maximum(o, c)
    with np.errstate(divide="ignore", invalid="ignore"):
        vol2 = v >= 2 * avg_v
        big = (c / o - 1 >= 0.05) & vol2
        wick = (h / o - 1 >= 0.07) & (upper >= 0.5 * rng) & vol2
        pierce = (((h >= ma112) & (c < ma112) & (o < ma112)) | ((h >= ma224) & (c < ma224) & (o < ma224))) & (h / o - 1 >= 0.05) & vol2
        below = c < ma224 * 1.05
    recent_min = pd.Series(lo).rolling(10, min_periods=10).min().to_numpy()
    prior_min = pd.Series(lo).rolling(50, min_periods=50).min().shift(10).to_numpy()
    stable = recent_min >= prior_min * 0.97                        # 최근 10봉이 직전 50봉 저점을 새로 깨지 않음(바닥 확인)
    ok = (big | wick | pierce) & below & stable
    kind = np.where(pierce, "이평 찌름", np.where(wick, "윗꼬리형", np.where(big, "장대양봉형", "")))
    return np.where(np.isnan(ma224), False, ok), kind


def accumulation(df, bars):
    """매집봉 표시 + '매집봉 돌파'(매집봉 고가를 종가로 뚫는 날 = 단테가 말한 타점)."""
    acc, kind = acc_candles(df)
    h, lo, c = df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float)
    n = len(df)
    marks, last_acc = [], None
    for i in range(max(n - bars, 0), n):
        if acc[i]:
            last_acc = (i, h[i], lo[i])
            marks.append({"date": _d(df.index[i]), "side": INFO, "label": f"매집봉({kind[i]})",
                          "why": f"{kind[i]} 매집봉 — 224일선 아래 바닥권에서 매물을 흡수(고가 {h[i]:,.0f}, 저가 {lo[i]:,.0f})"})
        elif last_acc and 0 < i - last_acc[0] <= 40 and c[i] > last_acc[1] >= c[i - 1]:
            marks.append({"date": _d(df.index[i]), "side": BUY, "label": "매집봉 돌파",
                          "why": f"매집봉({_d(df.index[last_acc[0]])}) 고가 {last_acc[1]:,.0f}를 종가로 돌파 — 매집봉을 뚫는 자리가 타점",
                          "ctx": {"acc_low": float(last_acc[2]), "acc_high": float(last_acc[1])}})
            last_acc = None
    lv = []
    if last_acc and n - last_acc[0] <= 40:
        lv = [{"price": float(last_acc[1]), "title": f"매집봉 고가 {_d(df.index[last_acc[0]])}", "color": "#8a5cd6"}]
    c224 = ind.sma(df["close"], 224)
    lines = [{"name": "224일선", "color": "#555555", "data": _line(c224, bars)}]
    cnt = sum(1 for m in marks if m["side"] == INFO)
    return _result(marks, lines, lv, summary=f"기간 내 매집봉 {cnt}개 · 매집봉이 앞에 있으면 3번 자리 진입의 확신이 높아진다고 설명")


# ---------------------------------------------------------------- 역매공파
def yeok_mae_gong_pa(df, bars):
    """단테 역매공파(자막 해석): 역(역배열 저평가) → 매(매집봉) → 공(공구리: 직전 언덕 돌파) → 파(돌파).
    구현: 공구리 돌파가 나온 날, ① 장기 역배열(224일선이 112일선 위) ② 최근 60봉 안에 매집봉이 있으면 '역매공파'.
    공·파가 각각 무엇인지는 자막에 풀이가 없어 공=공구리, 파=돌파로 해석했다."""
    acc, _ = acc_candles(df)
    acc_recent = pd.Series(acc.astype(float)).rolling(60, min_periods=1).max().to_numpy() > 0
    close = df["close"]
    rev = (ind.sma(close, 224) > ind.sma(close, 112)).to_numpy()
    out = []
    pos = {d: i for i, d in enumerate(df.index.strftime("%Y-%m-%d"))}
    for m in concrete(df, bars)["markers"]:
        i = pos.get(m["date"])
        if m["side"] == BUY and i is not None and acc_recent[i] and rev[i]:
            out.append({"date": m["date"], "side": BUY, "label": "역매공파",
                        "why": "역배열(224일선>112일선) 저평가권 + 최근 60봉 안 매집봉 + " + m["why"], "ctx": m.get("ctx")})
    ma224 = ind.sma(close, 224)
    return _result(out, [{"name": "224일선", "color": "#555555", "data": _line(ma224, bars)},
                         {"name": "112일선", "color": "#2a9d5c", "data": _line(ind.sma(close, 112), bars)}],
                   summary="역배열 저평가 + 매집봉 + 공구리(언덕 돌파)가 모두 나온 날만 표시")


# ---------------------------------------------------------------- 세력선 (7·15·33일선)
def force_lines(df, bars):
    """단테 세력선: 개미는 5·10·20·60일선을 보므로 그 사이의 7·15·33일선에서 세력이 손절 물량을 받아 올린다(자막 확인).
    구현: 정배열(5>10>20>60)에서 저가가 7(15·33)일선에 닿고 종가가 그 선 위에서 마감하면 '7일선 지지' 등으로 표시."""
    c, lo, o = df["close"], df["low"], df["open"]
    m = {n: ind.sma(c, n) for n in (5, 7, 10, 15, 20, 33, 60)}
    aligned = (m[5] > m[10]) & (m[10] > m[20]) & (m[20] > m[60])
    marks = []
    for n in (7, 15, 33):
        touch = aligned & (lo <= m[n] * 1.002) & (c >= m[n]) & (c > o)
        marks += _marks(df, touch, BUY, f"{n}일선 지지", lambda i, n=n, m=m: f"정배열에서 {n}일선({m[n].iloc[i]:,.0f})에 닿고 위에서 양봉 마감 — 세력선 지지", bars, 3)
    colors = {7: "#e0a000", 15: "#d6479b", 33: "#2a9d5c"}
    lines = [{"name": f"{n}일선", "color": colors[n], "data": _line(m[n], bars)} for n in (7, 15, 33)]
    return _result(marks, lines, summary="정배열일 때 7·15·33일선 지지 반등만 표시(자막: 7일선은 고수 영역이라 잘 안 주기도 함)")


# ---------------------------------------------------------------- 손익비·매매원칙
def risk_reward(df, bars):
    """단테 손익비: 확률이 아니라 '목표 수익 ÷ 손절'. 3배 이상(예: 손절 3%·목표 15% = 5:1)이면 승률 20~25%만 나와도
    계좌가 쌓인다. 필요 승률 = 1 ÷ (1 + 손익비)."""
    c = float(df["close"].iloc[-1])
    a = float(ind.atr(df).iloc[-1])
    stop = c - 2 * a
    risk = c - stop
    lv = [{"price": c, "title": "기준가(현재가)", "color": "#8f98ab"},
          {"price": stop, "title": f"손절 -{risk / c * 100:.1f}% (2×ATR)", "color": "#2b62d9"},
          {"price": c + 3 * risk, "title": f"목표 +{3 * risk / c * 100:.1f}% (손익비 3:1, 필요 승률 25%)", "color": "#d6362f"},
          {"price": c + 5 * risk, "title": f"목표 +{5 * risk / c * 100:.1f}% (손익비 5:1, 필요 승률 17%)", "color": "#d6362f"}]
    return _result(levels=lv, summary=f"예시 계산: 손절 {stop:,.0f}(−{risk / c * 100:.1f}%), 손익비 3:1 목표 {c + 3 * risk:,.0f}. "
                                      "손절 5%·목표 2%는 승률 80%가 필요해 불리합니다. 손절폭은 본인 원칙으로 정하세요.")


# ---------------------------------------------------------------- 단타
def short_term(df, bars):
    c, o, h, l, v = df["close"], df["open"], df["high"], df["low"], df["volume"]
    body = (c - o)
    big = (body / o >= 0.05) & (body >= body.abs().rolling(10, min_periods=5).mean().shift() * 2)
    avg = ind.sma(v, 20).shift()
    cand = big & (v >= avg * 2)
    marks = _marks(df, cand, BUY, "장대양봉+거래량", lambda i: f"몸통 +{body.iloc[i] / o.iloc[i] * 100:.1f}% 장대양봉, 거래량 {v.iloc[i] / avg.iloc[i]:.1f}배(단타 후보)", bars, 3)
    # 후보일 저가를 3일 안에 종가로 이탈하면 손절 신호
    arr, stops = cand.fillna(False).to_numpy(), []
    start = max(len(df) - bars, 0)
    for i in range(start, len(df) - 1):
        if arr[i]:
            low_i = l.iloc[i]
            for j in range(i + 1, min(i + 4, len(df))):
                if c.iloc[j] < low_i:
                    stops.append({"date": _d(df.index[j]), "side": SELL, "label": "저가 이탈",
                                  "why": f"장대양봉({_d(df.index[i])}) 저가 {low_i:,.0f}를 종가로 이탈 → 손절 기준"})
                    break
    return _result(marks + stops, summary="장대양봉(몸통 5%↑·평균 몸통 2배↑) + 거래량 2배 → 후보, 3일 안에 그 봉 저가 종가 이탈 시 손절 표시")


REGISTRY = {
    "이평선": {"status": "ready", "fn": trend_ma, "default": True,
              "rules": ["5·20·60·120일선을 그림", "5>20>60>120이 되는 날 '정배열', 반대는 '역배열'", "20일선이 60일선을 교차하는 날 표시"]},
    "지지·저항": {"status": "ready", "fn": support_resistance, "default": True,
               "origin": "dante", "sources": ["K9Feo5ho4NE"],
               "rules": ["지지·저항 = 파동의 꼭지(변곡)를 수평으로 그은 선: 최근 250일 좌우 5봉 피벗 고·저점을 가격대(±1.5%)로 묶어 2번 이상 터치한 상위 4개를 점선으로 표시",
                         "추세선 = 고점-고점(또는 저점-저점) 두 점 이상을 사선으로 이은 선: 최근 두 피벗 고점·저점을 이어 현재까지 연장",
                         "대칭: 이전 파동의 1:1 이상을 뚫어야 추세가 바뀐다고 설명(차트 적용은 아직 안 함)"]},
    "눌림목": {"status": "ready", "fn": pullback, "default": True,
             "rules": ["상승 추세: 종가>60일선, 20일선>60일선, 60일선 상승", "최근 15일 안에 종가가 20일선 대비 +8% 이상 올랐다가", "어제 종가가 20일선 ±3% 안 + 어제 거래량이 20일 평균 이하", "오늘 어제 고가를 넘는 양봉이면 '눌림목 반등'"]},
    "돌파·신고가": {"status": "ready", "fn": breakout, "default": True,
                "rules": ["종가가 직전 20일 고점을 넘고 거래량 1.5배↑이면 '박스 돌파'", "종가가 직전 52주 고가를 넘으면 '52주 신고가'"]},
    "거래량": {"status": "ready", "fn": volume, "default": True,
             "rules": ["거래량이 직전 20일 평균의 2배↑: 양봉이면 매수 쪽, 음봉이면 매도 쪽 표시", "거래량 40% 이하 + 등락 1% 이내는 '거래량 마름'(매물 소화)"]},
    "캔들": {"status": "ready", "fn": candle, "default": False,
           "rules": ["망치형·유성형·상승/하락 장악형·샛별형(몸통·꼬리 비율 규칙)"]},
    "손익비·매매원칙": {"status": "ready", "fn": risk_reward, "default": False,
                  "origin": "dante", "sources": ["Cc7zp3agJL4"],
                  "rules": ["손익비 = 목표 수익 ÷ 손절 (확률이 아님). 3배 이상을 선호: 손절 3%·목표 15%(5:1)이면 승률 20%만 나와도 이익",
                            "반대로 손절 5%·목표 2%는 승률 80%가 필요해 불리", "필요 승률 = 1 ÷ (1 + 손익비)",
                            "화면의 손절 = 현재가 − 2×ATR(14)는 예시 계산 — 실제 손절폭은 본인 원칙으로"]},
    "단타": {"status": "ready", "fn": short_term, "default": False,
           "rules": ["장대양봉: 몸통 +5%↑ 이고 최근 10일 평균 몸통의 2배↑, 거래량 2배↑ → 후보", "후보 다음 3일 안에 그 봉 저가를 종가로 이탈하면 '저가 이탈'(손절)"]},
    "256기법": {"status": "ready", "fn": ma256, "default": True, "origin": "dante",
              "sources": ["D7q0c9TkLrM", "DNdwKoxTrZ4", "KHVYJiH9ruE", "KHiuCH2GEDw"],
              "rules": ["이름 = 20일선(2)·5일선(5)·60일선(6)의 앞글자. 하락 중에는 60>20>5 역배열(캔들은 아래)",
                        "반등하며 5일선이 20일선을 골든크로스 하는데 60일선은 아직 위 → '256 자리'. 20일선을 손절 기준으로 매수",
                        "종가가 60일선을 뚫으면 추세 전환('256 완성')", "중장기 버전: 20→112일선, 60→224일선 (6개월·1년 지지선, 더 안정적)",
                        "최신 표현: 장기 이평 역배열 + 단기 이평 정배열. 매집봉이 있으면 확률이 높아진다고 설명(매집봉 조건은 미확정)",
                        "구현: 최근 40봉(중장기 80봉) 안에 역배열이 있었던 경우만 인정"]},
    "이평 때리기": {"status": "ready", "fn": ma_strike, "default": False, "origin": "dante",
               "sources": ["JQJMyTkl_uo", "KHiuCH2GEDw"],
               "rules": ["장기 이평 3개(112·224·448일선)만 본다. 큰 하락 뒤 이평선이 역배열로 벌어졌다가(224가 112보다 10%↑) 회복",
                         "바닥에서 공구리를 만든 뒤 종가가 112일선(6개월)을 뚫어 안착하면 224일선까지, 224를 넘으면 448일선까지가 '공짜 라인'",
                         "급락이 크게 나와 이격이 벌어졌을 때 유리(수렴형은 256 자리와 거의 같지만 이쪽은 이격 확장형)",
                         "선 색: 112 초록, 224 검정(회색), 448 파랑 점선"]},
    "밥그릇": {"status": "ready", "fn": rice_bowl, "default": False, "origin": "dante",
             "sources": ["4kwgvXP5FdU", "KHiuCH2GEDw"],
             "rules": ["224일선 기준 4단계: ① 급락(저평가) ② 224 아래 4개월+ 횡보하며 매집 ③ 224를 뚫고 눌러 주는 흔들기(3번 자리) ④ 안착 후 슈팅",
                       "초보 규칙: 224일선 밑에서 4개월(80봉) 이상 있던 종목이 224일선 ±10% 안에 들어오면 관심 — 4개월이 안 됐으면 하지 않는다",
                       "기준선을 5·20·60·112·448일선으로 바꾸면 회전 주기가 달라짐(구현은 224 고정)"]},
    "공구리": {"status": "ready", "fn": concrete, "default": False, "origin": "dante", "sources": ["LsIbmnUp-1A"],
             "rules": ["공구리 = 지지를 단단하게 만드는 것(바닥 탈피). 하락하던 주가의 '바로 직전 언덕'(직전 반등 고점)을 뚫어 올리면 공구리",
                       "뚫었다가 다시 이탈하면 실패 — 오히려 큰 저항이 되므로 빠르게 정리", "언덕을 못 뚫고 계속 빠지면 공구리가 아니다",
                       "구현: 좌우 5봉 피벗 고점 중 그 뒤로 8%+ 내려온 가장 최근 것을 언덕으로, 종가 돌파=돌파, 5일 안 2%+ 이탈=실패"]},
    "매집봉": {"status": "ready", "fn": accumulation, "default": False, "origin": "dante",
             "sources": ["IZ_qmX_sKII", "Bwm5xneDGjo", "EzUDMDD-gCA"],
             "rules": ["매집봉 = 개미 물량·매물대의 매물을 흡수하는 캔들. 올렸다가 눌러 주며 전고 언덕/장기 이평 매물을 먹는다(개미를 본전에 탈출시켜 물량 확보)",
                       "형태: 장대양봉(몸통 5%↑) / 윗꼬리형(7%↑ 올렸다가 윗꼬리가 봉 길이 절반↑) / 112·224일선을 꼬리로 찌르고 그 아래서 마감 — 모두 거래량 2배↑",
                       "위치: 224일선 아래(±5%)에서 바닥이 확인된 뒤(밥그릇 2번~3번). 내려가는 중(1번 자리)의 매집봉은 건드리지 않는다",
                       "타점: 매집봉 고가를 종가로 뚫을 때('매집봉 돌파', 40봉 안). 매집봉이 앞에 있으면 3번 자리 진입의 확신이 높다",
                       "구현 한계: 자막에 숫자 기준이 없어 몸통 5%·윗꼬리 7%·거래량 2배는 제가 정한 값입니다"]},
    "역매공파": {"status": "ready", "fn": yeok_mae_gong_pa, "default": False, "origin": "dante",
              "sources": ["D7q0c9TkLrM", "DNdwKoxTrZ4", "Cc7zp3agJL4", "Bwm5xneDGjo"],
              "rules": ["256 자리(역배열에서 단기 정배열 전환) + 매집봉 + 종목의 과거 행실 → 역매공파 (자막 확인)",
                        "구현: 역(224일선>112일선 역배열) → 매(최근 60봉 안 매집봉) → 공(공구리: 직전 언덕 종가 돌파)이 모두 있는 날",
                        "'공·파'의 풀이는 자막에 없어 공=공구리, 파=돌파로 해석했고, 파란 점선·수박 지표(단테 전용)와 '과거 행실'은 반영하지 못했다"]},
    "세력선": {"status": "ready", "fn": force_lines, "default": False, "origin": "dante", "sources": ["7hl0uQKw-dI"],
             "rules": ["개미는 5·10·20·60일선을 보므로 그 사이 7·15·33일선에서 세력이 손절 물량을 받아 올린다(5일선 이탈 → 7일선, 10일선 → 15일선, 20일선 → 33일선)",
                       "정배열(5>10>20>60)에서 저가가 7·15·33일선에 닿고 양봉으로 위에서 마감하면 '○일선 지지'",
                       "30%↑ 급등 뒤 첫 조정을 노리는 '다이빙 기법'은 자막 설명이 모호해 구현하지 않았다"]},
    "이평선": {"status": "ready", "fn": trend_ma, "default": True,
              "rules": ["5·20·60·120일선을 그림", "5>20>60>120이 되는 날 '정배열', 반대는 '역배열'", "20일선이 60일선을 교차하는 날 표시"]},
    "지지·저항": {"status": "ready", "fn": support_resistance, "default": True,
               "origin": "dante", "sources": ["K9Feo5ho4NE"],
               "rules": ["지지·저항 = 파동의 꼭지(변곡)를 수평으로 그은 선: 최근 250일 좌우 5봉 피벗 고·저점을 가격대(±1.5%)로 묶어 2번 이상 터치한 상위 4개를 점선으로 표시",
                         "추세선 = 고점-고점(또는 저점-저점) 두 점 이상을 사선으로 이은 선: 최근 두 피벗 고점·저점을 이어 현재까지 연장",
                         "대칭: 이전 파동의 1:1 이상을 뚫어야 추세가 바뀐다고 설명(차트 적용은 아직 안 함)"]},
    "눌림목": {"status": "ready", "fn": pullback, "default": True,
             "rules": ["상승 추세: 종가>60일선, 20일선>60일선, 60일선 상승", "최근 15일 안에 종가가 20일선 대비 +8% 이상 올랐다가", "어제 종가가 20일선 ±3% 안 + 어제 거래량이 20일 평균 이하", "오늘 어제 고가를 넘는 양봉이면 '눌림목 반등'"]},
    "돌파·신고가": {"status": "ready", "fn": breakout, "default": True,
                "rules": ["종가가 직전 20일 고점을 넘고 거래량 1.5배↑이면 '박스 돌파'", "종가가 직전 52주 고가를 넘으면 '52주 신고가'"]},
    "거래량": {"status": "ready", "fn": volume, "default": True,
             "rules": ["거래량이 직전 20일 평균의 2배↑: 양봉이면 매수 쪽, 음봉이면 매도 쪽 표시", "거래량 40% 이하 + 등락 1% 이내는 '거래량 마름'(매물 소화)"]},
    "캔들": {"status": "ready", "fn": candle, "default": False,
           "rules": ["망치형·유성형·상승/하락 장악형·샛별형(몸통·꼬리 비율 규칙)"]},
    "손익비·매매원칙": {"status": "ready", "fn": risk_reward, "default": False,
                  "origin": "dante", "sources": ["Cc7zp3agJL4"],
                  "rules": ["손익비 = 목표 수익 ÷ 손절 (확률이 아님). 3배 이상을 선호: 손절 3%·목표 15%(5:1)이면 승률 20%만 나와도 이익",
                            "반대로 손절 5%·목표 2%는 승률 80%가 필요해 불리", "필요 승률 = 1 ÷ (1 + 손익비)",
                            "화면의 손절 = 현재가 − 2×ATR(14)는 예시 계산 — 실제 손절폭은 본인 원칙으로"]},
    "단타": {"status": "ready", "fn": short_term, "default": False,
           "rules": ["장대양봉: 몸통 +5%↑ 이고 최근 10일 평균 몸통의 2배↑, 거래량 2배↑ → 후보", "후보 다음 3일 안에 그 봉 저가를 종가로 이탈하면 '저가 이탈'(손절)"]},
    "256기법": {"status": "ready", "fn": ma256, "default": True, "origin": "dante",
              "sources": ["D7q0c9TkLrM", "DNdwKoxTrZ4", "KHVYJiH9ruE", "KHiuCH2GEDw"],
              "rules": ["이름 = 20일선(2)·5일선(5)·60일선(6)의 앞글자. 하락 중에는 60>20>5 역배열(캔들은 아래)",
                        "반등하며 5일선이 20일선을 골든크로스 하는데 60일선은 아직 위 → '256 자리'. 20일선을 손절 기준으로 매수",
                        "종가가 60일선을 뚫으면 추세 전환('256 완성')", "중장기 버전: 20→112일선, 60→224일선 (6개월·1년 지지선, 더 안정적)",
                        "최신 표현: 장기 이평 역배열 + 단기 이평 정배열. 매집봉이 있으면 확률이 높아진다고 설명(매집봉 조건은 미확정)",
                        "구현: 최근 40봉(중장기 80봉) 안에 역배열이 있었던 경우만 인정"]},
    "이평 때리기": {"status": "ready", "fn": ma_strike, "default": False, "origin": "dante",
               "sources": ["JQJMyTkl_uo", "KHiuCH2GEDw"],
               "rules": ["장기 이평 3개(112·224·448일선)만 본다. 큰 하락 뒤 이평선이 역배열로 벌어졌다가(224가 112보다 10%↑) 회복",
                         "바닥에서 공구리를 만든 뒤 종가가 112일선(6개월)을 뚫어 안착하면 224일선까지, 224를 넘으면 448일선까지가 '공짜 라인'",
                         "급락이 크게 나와 이격이 벌어졌을 때 유리(수렴형은 256 자리와 거의 같지만 이쪽은 이격 확장형)",
                         "선 색: 112 초록, 224 검정(회색), 448 파랑 점선"]},
    "밥그릇": {"status": "ready", "fn": rice_bowl, "default": False, "origin": "dante",
             "sources": ["4kwgvXP5FdU", "KHiuCH2GEDw"],
             "rules": ["224일선 기준 4단계: ① 급락(저평가) ② 224 아래 4개월+ 횡보하며 매집 ③ 224를 뚫고 눌러 주는 흔들기(3번 자리) ④ 안착 후 슈팅",
                       "초보 규칙: 224일선 밑에서 4개월(80봉) 이상 있던 종목이 224일선 ±10% 안에 들어오면 관심 — 4개월이 안 됐으면 하지 않는다",
                       "기준선을 5·20·60·112·448일선으로 바꾸면 회전 주기가 달라짐(구현은 224 고정)"]},
    "공구리": {"status": "ready", "fn": concrete, "default": False, "origin": "dante", "sources": ["LsIbmnUp-1A"],
             "rules": ["공구리 = 지지를 단단하게 만드는 것(바닥 탈피). 하락하던 주가의 '바로 직전 언덕'(직전 반등 고점)을 뚫어 올리면 공구리",
                       "뚫었다가 다시 이탈하면 실패 — 오히려 큰 저항이 되므로 빠르게 정리", "언덕을 못 뚫고 계속 빠지면 공구리가 아니다",
                       "구현: 좌우 5봉 피벗 고점 중 그 뒤로 8%+ 내려온 가장 최근 것을 언덕으로, 종가 돌파=돌파, 5일 안 2%+ 이탈=실패"]},
    "재료·테마": {"status": "na", "rules": ["차트가 아니라 뉴스·테마 이야기라 차트에 적용하지 않습니다"]},
    "초보·기초": {"status": "na", "rules": ["학습 주제라 차트에 적용하지 않습니다"]},
}

ORDER = ["256기법", "이평 때리기", "밥그릇", "공구리", "매집봉", "역매공파", "세력선", "이평선", "지지·저항", "눌림목", "돌파·신고가", "거래량", "캔들", "손익비·매매원칙", "단타", "재료·테마", "초보·기초"]


def catalog() -> list[dict]:
    return [{"name": n, "status": REGISTRY[n]["status"], "default": REGISTRY[n].get("default", False),
             "origin": REGISTRY[n].get("origin", "general"), "rules": REGISTRY[n]["rules"]} for n in ORDER]


def apply(name: str, df: pd.DataFrame, bars: int = 750):
    spec = REGISTRY.get(name)
    if not spec or spec["status"] != "ready":
        return None
    if len(df) < 130:
        return _result(summary="데이터가 부족합니다(130봉 이상 필요)")
    out = spec["fn"](df, bars)
    out["lines"] = [l for l in out["lines"] if l["data"]]   # 데이터가 모자라 비는 선(예: 448일선)은 뺀다
    out["name"] = name
    return out


def latest_signal(name: str, df: pd.DataFrame) -> list[dict]:
    """가장 최근 봉에 나온 마커(스캔용)."""
    r = apply(name, df, bars=3)
    last = _d(df.index[-1])
    return [m for m in (r["markers"] if r else []) if m["date"] == last]
