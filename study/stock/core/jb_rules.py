"""JB차트 AI 매수·매도 규칙 — 시총 구간(대형·중형·소형·잡주)마다 따로 고른 규칙(2026-10-19, tools/research/jb_tier.py).

공통 매수: AI 점수가 95점을 처음 넘은 날(강한 신호, 같은 종목 10봉 안 반복 신호 제외) + 그날 볼린저 %B ≤ 0.5(20일, 2 표준편차의 가운데 아래)
          → 다음 거래일 시가 매수. 한 종목에 한 번에 한 거래.
매도(구간마다 다름):
  대형주 = 매수에 '60일 동안 16.5% 넘게 하락' + '시장(전 종목 평균 지수)이 52주 고점보다 5.4% 넘게 아래' 추가, 매도 = 매수 후 40거래일째 종가
           (대형주 연구: 하위 3분의 1 종목 +2.3% → +5.9%, 전체 +16.8%(승률 76%) → +21.3%(83%), 두 기간 모두 개선)
  중형주 = 매수에 '20일 평균 거래대금 약 200억 원 미만' 추가, 매도 = AI 점수(5봉 평균) 30점 아래 또는 수익 +20% 이상에서 전날 볼린저 상단(지정가)
           (중형주 연구: 거래대금이 큰 중형주에서 손실이 몰렸음 — 하위 3분의 1 종목 평균 −1.1% → +4.5%, 전체 +12.2% → +15.2%, 두 기간 모두 개선)
  소형주 = AI 점수(5봉 평균)가 40점 아래로 내려온 날 종가, 또는 수익 +20% 이상인 상태에서 전날 볼린저 상단에 닿으면 그 가격(지정가)
  잡주 = 아직 시험 전 — 소형주 규칙을 임시로 쓴다(STATUS 참고)
시험(학습에 쓰지 않은 구간, 비용 0.3%): 대형 2017~21 +14.8%·승률 70% / 2022~ +19.2%·84%, 중형 +13.6%·79% / +12.1%·76%, 소형 +17.8%·83% / +13.4%·76%.
두 기간을 함께 보고 고른 규칙이라 실제보다 좋게 나올 수 있다(2021년까지로만 고른 규칙도 2022년 이후 지금 규칙보다 나았음 — 연구 노트 참고).
"""
import numpy as np

COST = 0.003
TOP, WEAK, GAP = 0.95, 0.90, 10
RULES = {
    "대형주": {"entry": "strong_b50", "ret60_max": -0.165, "mkt_dd_max": -0.054, "exit": ("hold", 40)},   # 2026-10-19 대형주 연구(jb_mid.py 대형주)
    "중형주": {"entry": "strong_b50", "liq_max": 10.3, "exit": ("ai_or_bb", 0.30, 0.20)},     # 2026-10-19 중형주 연구(tools/research/jb_mid.py)
    "소형주": {"entry": "strong_b50", "exit": ("ai_or_bb", 0.40, 0.20)},
    "잡주": {"entry": "strong_b50", "exit": ("ai_or_bb", 0.40, 0.20)},
}
STATUS = {"잡주": "잡주는 아직 시험 전이라 소형주 규칙을 임시로 씁니다."}
CAP = 120           # 매도 조건이 오지 않아도 이 봉 수가 지나면 종가에 매도


def rule_for(group: str | None) -> dict:
    return RULES.get(group or "", RULES["소형주"])


def describe(group: str | None) -> tuple[str, str]:
    """(매수 설명, 매도 설명) — 화면 문구."""
    r = rule_for(group)
    buy = ("AI 점수가 95점을 처음 넘고 그날 종가가 볼린저 가운데선(20일 평균) 아래"
           + (f" · 20일 평균 거래대금 {10 ** r['liq_max'] / 1e8:,.0f}억 원 미만" if r.get("liq_max") else "")
           + (f" · 60일 동안 {-r['ret60_max'] * 100:.1f}% 넘게 하락" if r.get("ret60_max") is not None else "")
           + (f" · 시장이 52주 고점보다 {-r['mkt_dd_max'] * 100:.1f}% 넘게 아래" if r.get("mkt_dd_max") is not None else "")
           + " → 다음 날 시가 매수")
    x = r["exit"]
    if x[0] == "hold":
        sell = f"매수 후 {x[1]}거래일째 종가에 매도"
    else:
        sell = f"AI 점수(5봉 평균)가 {x[1] * 100:.0f}점 아래로 내려오면 그날 종가, 또는 수익 +{x[2] * 100:.0f}% 이상에서 볼린저 상단(전날 값)에 닿으면 그 가격에 매도"
    return buy, sell


def bands(c: np.ndarray, n: int = 20):
    """볼린저(20, 2): 가운데·위·아래(모집단 표준편차, 화면 JS 와 같은 계산)."""
    m = np.full(len(c), np.nan); sd = np.full(len(c), np.nan)
    if len(c) >= n:
        w = np.lib.stride_tricks.sliding_window_view(c, n)
        m[n - 1:] = w.mean(axis=1); sd[n - 1:] = w.std(axis=1)
    return m, m + 2 * sd, m - 2 * sd


def _crossings(s: np.ndarray, ok: np.ndarray):
    """연구(jb_exit.signals)와 같은 신호: 95·90점 상향 돌파, 10봉 안 반복은 건너뜀(약→강 올라가는 경우는 인정)."""
    out, last, lt = [], -10**9, ""
    for i in range(1, len(s)):
        strong, weak = s[i] >= TOP and s[i - 1] < TOP, s[i] >= WEAK and s[i - 1] < WEAK
        if not (strong or weak) or not ok[i]:
            continue
        t = "강" if s[i] >= TOP else "약"
        if i - last < GAP and not (strong and lt == "약"):
            continue
        last, lt = i, t
        out.append((i, t))
    return out


def simulate(o, h, l, c, score, group=None, ok=None, v=None, mkt_dd=None):
    """규칙대로 거래를 다시 계산. 돌려줌: trades[(신호 봉, 매수 봉, 매도 봉|None, 매도가|None, 수익|None, 이유)], 오늘 신호(매수 대기) 봉 또는 None.
    매도 봉이 None 이면 아직 보유 중(마지막 거래). ok: 점수를 믿을 수 있는 봉(학습에 쓰지 않은 구간 등) — 신호는 그 봉에서만."""
    r = rule_for(group)
    n = len(c)
    s = np.nan_to_num(np.asarray(score, float), nan=0.0)
    ok = np.isfinite(np.asarray(score, float)) if ok is None else np.asarray(ok, bool)
    mid, up, lo = bands(np.asarray(c, float))
    sm5 = np.convolve(s, np.ones(5) / 5, mode="full")[:n]
    liq = None
    if r.get("liq_max") and v is not None:                  # 20일 평균 거래대금(로그) — ai 특징 'liq' 와 같은 계산
        val = np.asarray(c, float) * np.asarray(v, float)
        m20 = np.full(n, np.nan)
        if n >= 20:
            m20[19:] = np.lib.stride_tricks.sliding_window_view(val, 20).mean(axis=1)
        liq = np.log10(m20 + 1)
    trades, busy, pending = [], -1, None
    for i, t in _crossings(s, ok):
        if r["entry"] == "strong_b50":
            if t != "강" or not np.isfinite(up[i]) or up[i] <= lo[i] or (c[i] - lo[i]) > 0.5 * (up[i] - lo[i]):
                continue
        if liq is not None and not (np.isfinite(liq[i]) and liq[i] < r["liq_max"]):
            continue
        if r.get("ret60_max") is not None and not (i >= 60 and c[i] / c[i - 60] - 1 < r["ret60_max"]):
            continue
        if r.get("mkt_dd_max") is not None and mkt_dd is not None and not (np.isfinite(mkt_dd[i]) and mkt_dd[i] < r["mkt_dd_max"]):
            continue
        if i <= busy:
            continue
        b = i + 1
        if b >= n:
            pending = i                 # 오늘 신호 — 다음 거래일 시가 매수
            continue
        entry = o[b]
        x = r["exit"]
        sell, px, why = None, None, ""
        if x[0] == "hold":
            if b + x[1] < n:
                sell, px, why = b + x[1], c[b + x[1]], f"매수 후 {x[1]}거래일째 종가"
        else:
            for j in range(b + 1, min(b + CAP, n)):
                u = up[j - 1]
                if np.isfinite(u) and h[j] >= u and max(o[j], u) >= entry * (1 + x[2]):
                    sell, px, why = j, max(o[j], u), f"수익 +{x[2] * 100:.0f}% 이상에서 볼린저 상단 {u:,.0f}원 도달"
                    break
                if j - b >= 5 and sm5[j] < x[1]:
                    sell, px, why = j, c[j], f"AI 점수(5봉 평균) {sm5[j] * 100:.0f}점 — {x[1] * 100:.0f}점 아래"
                    break
            if sell is None and b + CAP < n:
                sell, px, why = b + CAP, c[b + CAP], f"{CAP}거래일 동안 매도 조건이 없어 종가 매도"
        ret = None if sell is None else float(px / entry - 1 - COST)
        trades.append((i, b, sell, None if px is None else float(px), ret, why))
        busy = n if sell is None else sell
    return trades, pending, {"mid": mid, "up": up, "lo": lo, "sm5": sm5}


def market_dd(dates) -> np.ndarray:
    """날짜별 시장(전 종목 평균 지수) 52주 고점 대비 — 대형주 규칙의 시장 조건용. 지수 파일이 없으면 모두 NaN(조건 없이)."""
    try:
        from core import predict
        m = predict._mkt()
        return m["mkt_dd250"].reindex([str(d)[:10] for d in dates]).to_numpy(float)
    except Exception:
        return np.full(len(dates), np.nan)
