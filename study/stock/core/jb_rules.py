"""JB차트 AI 매수·매도 규칙 — 시총 구간(대형·중형·소형·잡주)마다 따로 고른 규칙(2026-10-19, tools/research/jb_tier.py).

공통 매수: AI 점수가 95점을 처음 넘은 날(강한 신호, 같은 종목 10봉 안 반복 신호 제외) + 그날 볼린저 %B ≤ 0.5(20일, 2 표준편차의 가운데 아래)
          → 다음 거래일 시가 매수. 한 종목에 한 번에 한 거래.
매도(구간마다 다름):
  대형주 = 매수에 '60일 동안 16.5% 넘게 하락' + '시장(전 종목 평균 지수)이 52주 고점보다 5.4% 넘게 아래' 추가, 매도 = 매수 후 40거래일째 종가
           (대형주 연구: 하위 3분의 1 종목 +2.3% → +5.9%, 전체 +16.8%(승률 76%) → +21.3%(83%), 두 기간 모두 개선)
  중형주 = 매수에 '20일 평균 거래대금 약 200억 원 미만' 추가, 매도 = AI 점수(5봉 평균) 30점 아래 또는 수익 +20% 이상에서 전날 볼린저 상단(지정가)
           (중형주 연구: 거래대금이 큰 중형주에서 손실이 몰렸음 — 하위 3분의 1 종목 평균 −1.1% → +4.5%, 전체 +12.2% → +15.2%, 두 기간 모두 개선)
  소형주 = AI 점수(5봉 평균)가 40점 아래로 내려온 날 종가, 또는 수익 +20% 이상인 상태에서 전날 볼린저 상단에 닿으면 그 가격(지정가)
  잡주 = 소형주와 같음(2026-10-19 잡주 340종목 시험 — 따로 고른 규칙은 2022년 이후 +10.6%로 소형주 규칙 +15.7%보다 나빠 그대로, tools/research/jb_junk.py)
시험(학습에 쓰지 않은 구간, 비용 0.3%): 대형 2017~21 +14.8%·승률 70% / 2022~ +19.2%·84%, 중형 +13.6%·79% / +12.1%·76%, 소형 +17.8%·83% / +13.4%·76%.
두 기간을 함께 보고 고른 규칙이라 실제보다 좋게 나올 수 있다(2021년까지로만 고른 규칙도 2022년 이후 지금 규칙보다 나았음 — 연구 노트 참고).
"""
import numpy as np

COST = 0.003
TOP, WEAK, GAP = 0.95, 0.90, 10
RULES = {
    "대형주": {"entry": "strong_b50", "ret60_max": -0.165, "mkt_dd_max": -0.054, "exit": ("hold", 40),   # 2026-10-19 대형주 연구(jb_mid.py 대형주)
              "order": {"buy_k": 0.05, "buy_wait": 10, "buy_fallback": "skip", "ladder": [0.10, 0.20], "add_limit": False, "hold_ext": 20}},   # 지정가 저가 매수(jb_order.py) · 고가 매도: 40일째 5일선 위면 최대 20봉 더(jb_sellhigh.py)
    "중형주": {"entry": "strong_b50", "liq_max": 10.3, "exit": ("ai_or_bb", 0.30, 0.30),     # 고가 매도: 상단 지정가 매도 기준 수익 +20 → +30%(jb_sellhigh.py)     # 2026-10-19 중형주 연구(tools/research/jb_mid.py)
              "order": {"buy_k": 0.03, "buy_wait": 3, "buy_fallback": "skip", "ladder": [0.10, 0.20], "add_limit": False}},
    "소형주": {"entry": "strong_b50", "exit": ("ai_or_bb", 0.40, 0.30),
              "order": {"buy_k": 0.03, "buy_wait": 3, "buy_fallback": "skip", "ladder": [0.07, 0.14], "add_limit": False}},
    "잡주": {"entry": "strong_b50", "exit": ("ai_or_bb", 0.40, 0.30),
            "order": {"buy_k": 0.05, "buy_wait": 5, "buy_fallback": "skip", "ladder": [0.05, 0.10], "add_limit": False}},
}
# 2026-10-19 저가 매수(사용자 "저가 매수·고가 매도는 주식의 기본 — 무조건 적용", tools/research/jb_order.py): 신호 다음 날부터 '신호일 종가보다 k% 아래' 지정가로 며칠 기다려
# 닿으면 그 가격에 1차 매수(안 닿으면 사지 않음), 추가 매수는 3번에 나눠(1차 매수가 기준 두 단계, 종가가 닿으면 다음 날 시가). 2021년까지로 고르고 2022년 이후 확인:
# 거래당 평균 대형 +23.8→+24.8% · 중형 +15.4→+17.0% · 소형 +17.0→+19.5% · 잡주 +16.0→+18.7%, 손실 거래 10→4 · 23→13 · 152→81 · 139→80건,
# 평균 단가가 실제 저점에 더 가까움. 대신 지정가에 안 닿은 신호는 건너뛰어 거래 수가 약 50~60%로 줄어듦. 지정가 매도(목표가·회복·상단)는 모든 구간에서 평균을 낮춰 넣지 않음.
STATUS = {"잡주": "잡주는 소형주와 같은 규칙입니다 — 잡주만 따로 고른 규칙은 고를 때 안 본 2022년 이후에서 더 나빠 그대로 둠(잡주 340종목 시험: 2017~21년 +25%, 2022년 이후 +16%)."}
CAP = 120           # 매도 조건이 오지 않아도 이 봉 수가 지나면 종가에 매도
SPLIT = 0.10        # 나눠 사기(2026-10-19, tools/research/jb_loss.py): 신호 다음 날 시가에 절반, 매도 전 처음으로 종가가 1차 매수가보다 10% 이상 아래면
                    # 다음 날 시가에 나머지 절반. 수익률 = 평균 단가 기준. 모든 구간에서 평균 +3~3.5%p, 손실 거래 평균 손실 감소(대형 −8.0% → −4.4%)


def rule_for(group: str | None) -> dict:
    return RULES.get(group or "", RULES["소형주"])


def describe(group: str | None) -> tuple[str, str]:
    """(매수 설명, 매도 설명) — 화면 문구."""
    r = rule_for(group)
    buy = ("AI 점수가 95점을 처음 넘고 그날 종가가 볼린저 가운데선(20일 평균) 아래"
           + (f" · 20일 평균 거래대금 {10 ** r['liq_max'] / 1e8:,.0f}억 원 미만" if r.get("liq_max") else "")
           + (f" · 60일 동안 {-r['ret60_max'] * 100:.1f}% 넘게 하락" if r.get("ret60_max") is not None else "")
           + (f" · 시장이 52주 고점보다 {-r['mkt_dd_max'] * 100:.1f}% 넘게 아래" if r.get("mkt_dd_max") is not None else "")
           + (f" → 다음 날부터 {r['order']['buy_wait']}일 동안 신호일 종가보다 {r['order']['buy_k'] * 100:.0f}% 낮은 지정가 매수(안 닿으면 사지 않음)"
              f", 1차 매수가보다 {' · '.join(f'{d * 100:.0f}%' for d in r['order']['ladder'])} 아래로 마감하면 다음 날 시가에 추가 매수(3번에 나눠)"
              if r.get("order", {}).get("buy_k") else " → 다음 날 시가 매수"))
    x = r["exit"]
    if x[0] == "hold":
        ext = (r.get("order") or {}).get("hold_ext")
        sell = f"매수 후 {x[1]}거래일째 종가에 매도" + (f"(그날 5일선 위면 최대 {ext}거래일 더 들고 있다가 5일선 아래로 마감하는 날)" if ext else "")
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


def regime(c, n_ma: int = 224, k: int = 20, flat: float = 0.01) -> np.ndarray:
    """봉마다 추세 상태: 'up'(224일선이 20봉 사이 +1% 넘게 오름 · 종가가 224일선 위), 'down'(224일선이 −1% 넘게 내림 · 종가가 그 아래), 나머지 'side'(횡보).
    224일선이 아직 없으면 'side'."""
    c = np.asarray(c, float)
    n = len(c)
    ma = np.full(n, np.nan)
    if n >= n_ma:
        ma[n_ma - 1:] = np.lib.stride_tricks.sliding_window_view(c, n_ma).mean(axis=1)
    out = np.full(n, "side", dtype=object)
    for i in range(n_ma - 1 + k, n):
        sl = ma[i] / ma[i - k] - 1
        if sl > flat and c[i] > ma[i]:
            out[i] = "up"
        elif sl < -flat and c[i] < ma[i]:
            out[i] = "down"
    return out


def simulate(o, h, l, c, score, group=None, ok=None, v=None, mkt_dd=None, rule=None, pl=None, ph=None):
    """규칙대로 거래를 다시 계산. 돌려줌: trades[(신호 봉, 매수 봉, 매도 봉|None, 매도가|None, 수익|None, 이유)], 오늘 신호(매수 대기) 봉 또는 None.
    매도 봉이 None 이면 아직 보유 중(마지막 거래). ok: 점수를 믿을 수 있는 봉(학습에 쓰지 않은 구간 등) — 신호는 그 봉에서만."""
    base = rule or rule_for(group)
    n = len(c)
    reg = regime(c) if base.get("regimes") else None          # 추세 상태마다 다른 규칙(있으면): base 위에 덮어씀
    s = np.nan_to_num(np.asarray(score, float), nan=0.0)
    ok = np.isfinite(np.asarray(score, float)) if ok is None else np.asarray(ok, bool)
    mid, up, lo = bands(np.asarray(c, float))
    sm5 = np.convolve(s, np.ones(5) / 5, mode="full")[:n]
    liq = None
    if v is not None:                                       # 20일 평균 거래대금(로그) — ai 특징 'liq' 와 같은 계산
        val = np.asarray(c, float) * np.asarray(v, float)
        m20 = np.full(n, np.nan)
        if n >= 20:
            m20[19:] = np.lib.stride_tricks.sliding_window_view(val, 20).mean(axis=1)
        liq = np.log10(m20 + 1)
    trades, busy, pending = [], -1, None
    adds, add_pending = {}, []                               # 나눠 사기: 1차 매수 봉 → (2차 매수 봉, 가격), 오늘 마감에 2차 매수 신호가 난 1차 매수 봉
    aux_fills = {}                                          # 지정가 주문 방식: 1차 매수 봉 → [(봉, 가격, 비중), …](채워진 것만)
    for i, t in _crossings(s, ok):
        r = {**base, **base["regimes"].get(reg[i], {})} if reg is not None else base
        if r.get("skip"):
            continue
        if r["entry"] == "strong_b50":
            if t != "강" or not np.isfinite(up[i]) or up[i] <= lo[i] or (c[i] - lo[i]) > 0.5 * (up[i] - lo[i]):
                continue
        if r.get("liq_max") and liq is not None and not (np.isfinite(liq[i]) and liq[i] < r["liq_max"]):
            continue
        if r.get("ret60_max") is not None and not (i >= 60 and c[i] / c[i - 60] - 1 < r["ret60_max"]):
            continue
        if r.get("mkt_dd_max") is not None and mkt_dd is not None and not (np.isfinite(mkt_dd[i]) and mkt_dd[i] < r["mkt_dd_max"]):
            continue
        if i <= busy:
            continue
        if r.get("order"):              # 지정가 주문 방식(저가 매수·고가 매도·손실 회복 매도) — _trade_order
            res = _trade_order(o, h, l, c, up, sm5, i, n, r)
            if res is None:             # 아직 지정가가 안 닿았고 기다리는 중(오늘이 기다리는 기간 안)
                pending = i
                continue
            if res == "skip":
                continue
            b, entry, sell, px, why, fills, ret, padd = res
            if len(fills) > 1:
                adds[b] = (fills[1][0], fills[1][1])
            aux_fills[b] = fills
            if padd:
                add_pending.append(b)
            trades.append((i, b, sell, None if px is None else float(px), ret, why))
            busy = n if sell is None else sell
            continue
        if r.get("entry_close"):        # 장 마감 전(15시) 신호 → 그날 종가에 매수(시험용 옵션, tools/research/jb_close.py)
            b, entry = i, c[i]
        elif r.get("entry_wait") and pl is not None:     # 저점 기다리기(시험용, tools/research/jb_extreme.py): 신호 뒤 W봉 안에 '저점 확률' pl 이 기준 이상인 첫날 종가, 없으면 W봉째 종가
            W, b = r["entry_wait"], None
            for j in range(i, min(i + W, n - 1) + 1):
                if np.isfinite(pl[j]) and pl[j] >= r["entry_thr"]:
                    b = j
                    break
            if b is None:
                if i + W >= n:
                    pending = i
                    continue
                b = i + W
            entry = c[b]
        else:
            b = i + 1
            if b >= n:
                pending = i             # 오늘 신호 — 다음 거래일 시가 매수
                continue
            entry = o[b]
        if r.get("protect") or r.get("trail") or r.get("tp_half"):      # 이익 지키기·고점 대비 하락·절반 익절이 있는 규칙
            sell, px, why, add, ret, padd = _exit_plus(o, h, c, up, sm5, b, n, entry, r)
            if add:
                adds[b] = add
            if padd:
                add_pending.append(b)
            trades.append((i, b, sell, None if px is None else float(px), ret, why))
            busy = n if sell is None else sell
            continue
        x = r["exit"]
        sell, px, why = None, None, ""
        if x[0] == "hold":
            for j in range(b + 1, min(b + x[1], n)):
                if ph is not None and r.get("exit_thr") and np.isfinite(ph[j]) and ph[j] >= r["exit_thr"] and c[j] >= entry * (1 + r.get("exit_gain", 0)):
                    sell, px, why = j, c[j], f"고점 확률 {ph[j] * 100:.0f}% · 수익 구간 → 종가 매도"
                    break
                if r.get("stop") and c[j] <= entry * (1 - r["stop"]):
                    sell, px, why = j, c[j], f"1차 매수가보다 {r['stop'] * 100:.0f}% 아래 마감(손절)"
                    break
            if sell is None and b + x[1] < n:
                sell, px, why = b + x[1], c[b + x[1]], f"매수 후 {x[1]}거래일째 종가"
        else:
            for j in range(b + 1, min(b + CAP, n)):
                if ph is not None and r.get("exit_thr") and np.isfinite(ph[j]) and ph[j] >= r["exit_thr"] and c[j] >= entry * (1 + r.get("exit_gain", 0)):
                    sell, px, why = j, c[j], f"고점 확률 {ph[j] * 100:.0f}% · 수익 구간 → 종가 매도"
                    break
                if r.get("stop") and c[j] <= entry * (1 - r["stop"]):      # 손절(1차 매수가 기준) — 있을 때만
                    sell, px, why = j, c[j], f"1차 매수가보다 {r['stop'] * 100:.0f}% 아래 마감(손절)"
                    break
                u = up[j - 1]
                if np.isfinite(u) and h[j] >= u and max(o[j], u) >= entry * (1 + x[2]):
                    sell, px, why = j, max(o[j], u), f"수익 +{x[2] * 100:.0f}% 이상에서 볼린저 상단 {u:,.0f}원 도달"
                    break
                if j - b >= 5 and sm5[j] < x[1]:
                    sell, px, why = j, c[j], f"AI 점수(5봉 평균) {sm5[j] * 100:.0f}점 — {x[1] * 100:.0f}점 아래"
                    break
            if sell is None and b + CAP < n:
                sell, px, why = b + CAP, c[b + CAP], f"{CAP}거래일 동안 매도 조건이 없어 종가 매도"
        avg = entry
        if SPLIT:
            last = sell if sell is not None else n - 1
            for x_ in range(b, last + (0 if sell is not None else 1)):
                if c[x_] <= entry * (1 - SPLIT):
                    if x_ + 1 <= last and x_ + 1 < n:
                        adds[b] = (x_ + 1, float(o[x_ + 1]))
                        avg = (entry + o[x_ + 1]) / 2
                    elif sell is None:
                        add_pending.append(b)           # 오늘 마감에 −10% → 다음 거래일 시가에 2차 매수
                    break
        ret = None if sell is None else float(px / avg - 1 - COST)
        trades.append((i, b, sell, None if px is None else float(px), ret, why))
        busy = n if sell is None else sell
    return trades, pending, {"mid": mid, "up": up, "lo": lo, "sm5": sm5, "adds": adds, "add_pending": add_pending, "fills": aux_fills}


def _trade_order(o, h, l, c, up, sm5, i, n, r):
    """지정가 주문 방식으로 한 거래를 따라간다(2026-10-19, 사용자 "저가 매수·고가 매도가 주식의 기본 — 무조건 적용").
    r["order"]:
      buy_k·buy_wait·buy_fallback: 신호일 종가보다 buy_k 아래 지정가로 다음 날부터 buy_wait 일 기다림(장중 저가가 닿으면 그 가격, 시가가 더 낮으면 시가),
                                    끝까지 안 닿으면 'close' = 마지막 날 종가 매수 / 'skip' = 사지 않음. buy_k=0 이면 다음 날 시가(예전과 같음).
      ladder: 추가 매수 하락 폭 목록(1차 매수가 기준, 예 [0.10] = 지금의 나눠 사기, [0.07, 0.14] = 3번에 나눠). 모든 몫은 같은 비중.
      add_limit: True 면 추가 매수도 지정가(장중 저가가 닿는 순간), False 면 종가가 닿은 다음 날 시가(예전).
      tp: 평균 단가 +tp 에 지정가 매도 · band_gain(보유형 규칙): 수익 band_gain 이상에서 전날 볼린저 상단 지정가 매도
      recover: 추가 매수로 평균 단가가 내려간 뒤 평균 단가 +recover 에 지정가 매도(손실 거래를 작은 수익으로 끝냄)
    같은 봉에서 추가 매수가 체결되면 그 봉에서는 지정가 매도를 하지 않는다(고가가 저가보다 먼저였는지 알 수 없어서).
    돌려줌: None(아직 지정가를 기다리는 중) · "skip" · (매수 봉, 1차 가격, 매도 봉|None, 매도가, 이유, 체결[(봉, 가격, 비중)], 수익, 오늘 추가 매수 대기)"""
    od = r["order"]
    k, W = od.get("buy_k", 0.0), od.get("buy_wait", 1)
    if k <= 0:
        b = i + 1
        if b >= n:
            return None
        entry = float(o[b])
    else:
        L, b = c[i] * (1 - k), None
        for j in range(i + 1, min(i + W, n - 1) + 1):
            if o[j] <= L:
                b, entry = j, float(o[j])
                break
            if l[j] <= L:
                b, entry = j, float(L)
                break
        if b is None:
            if i + W > n - 1:
                return None
            if od.get("buy_fallback", "close") == "skip":
                return "skip"
            b, entry = i + W, float(c[i + W])
    drops = list(od.get("ladder", [SPLIT] if SPLIT else []))
    w = 1.0 / (1 + len(drops))
    fills = [(b, entry, w)]
    nxt, trig = 0, None
    if drops and not od.get("add_limit") and c[b] <= entry * (1 - drops[0]):
        trig = b                                        # 매수한 날 종가부터 −10% 면 다음 날 시가에 추가(예전 계산과 같게)
    x = r["exit"]
    cap = x[1] if x[0] == "hold" else CAP

    def avg():
        return sum(f[1] * f[2] for f in fills) / sum(f[2] for f in fills)

    sell, px, why = None, None, ""
    ride, armed = od.get("ride"), None              # 고가 매도: 매도 조건이 와도 바로 팔지 않고 꺾이는 날(상단 아래·5일선 아래 마감) 종가에
    ext = od.get("hold_ext", 0)                     # 보유형: 기간이 끝나도 5일선 위에서 오르면 최대 ext 봉 더 들고 있음
    ma5 = lambda j: c[max(0, j - 4):j + 1].mean()   # noqa: E731
    for j in range(b + 1, n):
        if armed is not None:
            if (ride == "band" and np.isfinite(up[j]) and c[j] < up[j]) or (ride == "ma5" and c[j] < ma5(j)):
                sell, px, why = j, c[j], f"수익 구간에서 볼린저 상단 도달 뒤 {'상단' if ride == 'band' else '5일선'} 아래로 마감 — 고점을 지나 종가 매도"
                break
            continue
        added = False
        if nxt < len(drops):
            P = entry * (1 - drops[nxt])
            if od.get("add_limit"):
                if l[j] <= P:
                    fills.append((j, float(min(o[j], P)), w)); nxt += 1; added = True     # 장중 체결 — 이 봉의 지정가 매도는 막음
            elif trig is not None:
                fills.append((j, float(o[j]), w)); nxt += 1; trig = None              # 시가 체결 — 그 뒤 장중 매도는 가능
        a = avg()
        if x[0] == "hold" and j >= b + cap:
            if ext and j < b + cap + ext and c[j] >= ma5(j):
                continue                              # 아직 5일선 위 — 더 들고 있음
            sell, px, why = j, c[j], f"매수 후 {cap}거래일째 종가" if j == b + cap else f"매수 후 {j - b}거래일 — 5일선 아래로 마감해 종가"
            break
        if not added:
            if od.get("recover") is not None and len(fills) > 1 and h[j] >= a * (1 + od["recover"]):
                sell, px, why = j, max(o[j], a * (1 + od["recover"])), f"추가 매수 뒤 평균 단가 +{od['recover'] * 100:.0f}% 회복 — 지정가 매도"
                break
            if od.get("tp") and h[j] >= a * (1 + od["tp"]):
                sell, px, why = j, max(o[j], a * (1 + od["tp"])), f"평균 단가 +{od['tp'] * 100:.0f}% 목표가 — 지정가 매도"
                break
            if x[0] == "hold" and od.get("band_gain") is not None:
                u = up[j - 1]
                if np.isfinite(u) and h[j] >= u and max(o[j], u) >= a * (1 + od["band_gain"]):
                    sell, px, why = j, max(o[j], u), f"수익 +{od['band_gain'] * 100:.0f}% 이상에서 볼린저 상단 {u:,.0f}원 — 지정가 매도"
                    break
        if x[0] != "hold":
            u = up[j - 1]
            if not added and np.isfinite(u) and h[j] >= u and max(o[j], u) >= entry * (1 + x[2]):
                if ride:
                    armed = j                         # 고가 매도: 여기서 팔지 않고 꺾이는 날을 기다림
                    if (ride == "band" and c[j] < up[j]) or (ride == "ma5" and c[j] < ma5(j)):
                        sell, px, why = j, c[j], f"수익 +{x[2] * 100:.0f}% 이상에서 볼린저 상단 도달했으나 그날 바로 꺾여 종가 매도"
                        break
                    continue
                sell, px, why = j, max(o[j], u), f"수익 +{x[2] * 100:.0f}% 이상에서 볼린저 상단 {u:,.0f}원 도달"
                break
            if j - b >= 5 and sm5[j] < x[1]:
                sell, px, why = j, c[j], f"AI 점수(5봉 평균) {sm5[j] * 100:.0f}점 — {x[1] * 100:.0f}점 아래"
                break
            if j == b + cap:
                sell, px, why = j, c[j], f"{cap}거래일 동안 매도 조건이 없어 종가 매도"
                break
        if not od.get("add_limit") and nxt < len(drops) and trig is None and c[j] <= entry * (1 - drops[nxt]):
            trig = j
    if sell is None:
        return b, entry, None, None, "", fills, None, trig is not None
    return b, entry, sell, float(px), why, fills, float(px / avg() - 1 - COST), False


def _exit_plus(o, h, c, up, sm5, b, n, entry, r):
    """나눠 사기 + 기본 매도 + (선택) 이익 지키기·고점 대비 하락·절반 익절을 한 번에 따라간다.
    protect=(오른 폭, 남길 폭): 평균 단가 기준 종가 수익이 한 번이라도 '오른 폭' 이상이었다가 '남길 폭' 이하로 내려오면 그날 종가에 매도(수익일 때 팔기)
    trail=(오른 폭, 하락 폭): 수익이 '오른 폭' 이상이 된 뒤 가장 높았던 종가보다 '하락 폭' 이상 내려오면 매도
    tp_half=목표: 고가가 평균 단가 +목표에 닿으면 절반을 그 가격에 팔고, 나머지는 위 조건·기본 매도로
    돌려줌: (매도 봉, 매도가, 이유, 2차 매수(봉, 가격)|None, 수익|None, 오늘 2차 매수 신호 여부)"""
    x = r["exit"]
    cap = x[1] if x[0] == "hold" else CAP
    add, avg, peak, tp = None, entry, -1.0, None
    sell, px, why = None, None, ""
    end = min(b + cap, n - 1)
    for j in range(b + 1, end + 1):
        if SPLIT and add is None and c[j - 1] <= entry * (1 - SPLIT):
            add = (j, float(o[j])); avg = (entry + o[j]) / 2
        if x[0] == "hold" and j == b + cap:
            sell, px, why = j, c[j], f"매수 후 {cap}거래일째 종가"
            break
        if r.get("tp_half") and tp is None and h[j] >= avg * (1 + r["tp_half"]):
            tp = (j, max(o[j], avg * (1 + r["tp_half"])))
        if x[0] != "hold":
            u = up[j - 1]
            if np.isfinite(u) and h[j] >= u and max(o[j], u) >= entry * (1 + x[2]):
                sell, px, why = j, max(o[j], u), f"수익 +{x[2] * 100:.0f}% 이상에서 볼린저 상단 {u:,.0f}원 도달"
                break
            if j - b >= 5 and sm5[j] < x[1]:
                sell, px, why = j, c[j], f"AI 점수(5봉 평균) {sm5[j] * 100:.0f}점 — {x[1] * 100:.0f}점 아래"
                break
            if j == b + cap:
                sell, px, why = j, c[j], f"{cap}거래일 동안 매도 조건이 없어 종가 매도"
                break
        g = c[j] / avg - 1
        peak = max(peak, g)
        if r.get("protect") and peak >= r["protect"][0] and g <= r["protect"][1]:
            sell, px, why = j, c[j], f"한때 +{peak * 100:.0f}%까지 올랐다가 +{r['protect'][1] * 100:.0f}% 아래로 — 이익 지키기"
            break
        if r.get("trail") and peak >= r["trail"][0] and (1 + g) <= (1 + peak) * (1 - r["trail"][1]):
            sell, px, why = j, c[j], f"고점(+{peak * 100:.0f}%)보다 {r['trail'][1] * 100:.0f}% 넘게 내려옴"
            break
    padd = sell is None and add is None and SPLIT and c[n - 1] <= entry * (1 - SPLIT)
    if sell is None:
        return None, None, "", add, None, bool(padd)
    ret = px / avg - 1 - COST
    if tp and tp[0] <= sell:
        ret = 0.5 * (tp[1] / avg - 1) + 0.5 * (px / avg - 1) - COST
        why += f" (절반은 {tp[1]:,.0f}원에 먼저 익절)"
    return sell, float(px), why, add, float(ret), False


def market_dd(dates) -> np.ndarray:
    """날짜별 시장(전 종목 평균 지수) 52주 고점 대비 — 대형주 규칙의 시장 조건용. 지수 파일이 없으면 모두 NaN(조건 없이)."""
    try:
        from core import predict
        m = predict._mkt()
        return m["mkt_dd250"].reindex([str(d)[:10] for d in dates]).to_numpy(float)
    except Exception:
        return np.full(len(dates), np.nan)


def near_low(l, i):
    """매수 신호 무렵의 실제 저점: 신호 앞 5봉 ~ 뒤 15봉 저가 중 최저(봉, 가격) — 저점을 얼마나 잘 잡았는지 확인용(지난 거래만 의미)."""
    a, z = max(0, i - 5), min(len(l), i + 16)
    k = a + int(np.argmin(l[a:z]))
    return k, float(l[k])


def near_high(h, j):
    """매도 무렵의 실제 고점: 매도 앞뒤 10봉 고가 중 최고(봉, 가격)."""
    a, z = max(0, j - 10), min(len(h), j + 11)
    k = a + int(np.argmax(h[a:z]))
    return k, float(h[k])


def fills_of(aux, b, o):
    """한 거래의 체결 목록 [(봉, 가격, 비중)] — 지정가 주문 방식이면 그 기록, 예전 방식이면 1차(시가)+2차(나눠 사기)."""
    f = (aux.get("fills") or {}).get(b)
    if f:
        return f
    adds = aux.get("adds", {})
    if b in adds:
        return [(b, float(o[b]), 0.5), (adds[b][0], float(adds[b][1]), 0.5)]
    return [(b, float(o[b]), 1.0)]


def avg_price(fills, upto=None):
    f = [x for x in fills if upto is None or x[0] <= upto]
    return sum(x[1] * x[2] for x in f) / sum(x[2] for x in f)


def markers(dates, o, c, score, tr, aux, start: str = "", h=None, l=None):
    """JB차트 표식과 거래 목록(JB차트·휴대폰 스냅샷이 같이 씀). 2차 매수(나눠 사기)도 표시. 돌려줌: (표식[], 끝난 거래[]).
    h·l 을 주면 거래마다 그 무렵 실제 저점·고점(작은 점)과 '저점보다 +x% / 고점보다 −y%'를 함께 — 차트로 직접 확인하는 용도."""
    mk, done = [], []
    adds = aux.get("adds", {})
    n = len(c)
    for i, b, j, px, ret, why in tr:
        fl = fills_of(aux, b, o)
        e = fl[0][1]
        low_txt = ""
        if l is not None:
            lk, lp = near_low(l, i)
            low_txt = f" · 그 무렵 실제 저점 {dates[lk]} {lp:,.0f}원 → 산 가격이 저점보다 +{(e / lp - 1) * 100:.1f}%"
            if dates[lk] >= start and lk <= n - 1:
                mk.append({"date": dates[lk], "side": "low", "tier": "참고", "label": "저점", "why": f"매수({dates[b]}) 무렵 실제 저점 {lp:,.0f}원(신호 앞 5봉~뒤 15봉 최저가)"})
        if dates[b] >= start:
            mk.append({"date": dates[b], "side": "buy", "tier": "강", "label": "매수",
                       "why": f"{dates[i]} AI 점수 {score[i] * 100:.0f}점(95점 상향 돌파) · 볼린저 가운데 아래 → {dates[b]} {e:,.0f}원에 1차 매수"
                              + ("(시가)" if abs(e - o[b]) < 1e-9 else "(지정가 기다리다 안 닿아 종가)" if abs(e - c[b]) < 1e-9 else "(신호일 종가보다 낮은 지정가 체결)") + low_txt})
        for n_, (ab, apx, _w) in enumerate(fl[1:], 2):
            if dates[ab] >= start:
                mk.append({"date": dates[ab], "side": "buy", "tier": "약", "label": f"{n_}차 매수",
                           "why": f"1차 매수가 {e:,.0f}원보다 {(1 - apx / e) * 100:.0f}% 아래 → {dates[ab]} {apx:,.0f}원에 {n_}차 매수(평균 단가 {avg_price(fl, ab):,.0f}원)"})
        avg = avg_price(fl, j)
        if j is not None:
            t_ = {"buy": dates[b], "buy_px": e, "avg": round(avg, 2), "add": dates[fl[1][0]] if len(fl) > 1 and fl[1][0] <= j else None, "sell": dates[j], "sell_px": px, "ret": round(ret, 4), "tier": "강", "seen": False}
            high_txt = ""
            if l is not None:
                t_["from_low"] = round(e / near_low(l, i)[1] - 1, 4)
            if h is not None:
                hk, hp = near_high(h, j)
                t_["from_high"] = round(1 - px / hp, 4)
                high_txt = f" · 그 무렵 실제 고점 {dates[hk]} {hp:,.0f}원 → 판 가격이 고점보다 −{(1 - px / hp) * 100:.1f}%"
                if dates[hk] >= start:
                    mk.append({"date": dates[hk], "side": "high", "tier": "참고", "label": "고점", "why": f"매도({dates[j]}) 무렵 실제 고점 {hp:,.0f}원(매도 앞뒤 10봉 최고가)"})
            done.append(t_)
            if dates[j] >= start:
                mk.append({"date": dates[j], "side": "sell", "tier": "강", "label": f"매도 {ret * 100:+.0f}%",
                           "why": f"{dates[b]} 매수{''.join(f' + {dates[x[0]]} 추가 매수' for x in fl[1:] if x[0] <= j)} → {dates[j]} {why}, {px:,.0f}원에 매도"
                                  f"(평균 단가 {avg:,.0f}원, 비용 포함 {ret * 100:+.1f}%)" + high_txt})
    mk.sort(key=lambda m: m["date"])
    return mk, done


def action(dates, o, c, tr, pending, aux, score=None, group=None) -> dict:
    """오늘 할 일 하나(종합 신호) — JB차트 이름 옆 배지·휴대폰 화면이 같이 씀.
    kind: buy(다음 거래일 시가 매수) · add(다음 거래일 시가 2차 매수) · sell(오늘 매도) · hold(보유 중) · wait(관망)."""
    n = len(c)
    g = (group or "소형주")
    sc = None if score is None or not np.isfinite(score[-1]) else float(score[-1]) * 100
    sc_txt = f"AI {sc:.0f}점" if sc is not None else "AI 점수 없음"
    od = rule_for(group).get("order") or {}
    if pending is not None:
        if od.get("buy_k"):
            lim = c[pending] * (1 - od["buy_k"])
            left = pending + od.get("buy_wait", 1) - (n - 1)
            return {"kind": "buy", "text": f"{g} ▲ 지정가 매수 {lim:,.0f}원 · {left}일",
                    "title": f"{dates[pending]} AI 점수 {score[pending] * 100:.0f}점(95점 상향 돌파) + 볼린저 가운데 아래 → 신호일 종가 {c[pending]:,.0f}원보다 {od['buy_k'] * 100:.0f}% 낮은 "
                             f"{lim:,.0f}원에 지정가 매수 주문(앞으로 {left}거래일, 안 닿으면 사지 않음) — 1차 매수(3분의 1)"}
        return {"kind": "buy", "text": f"{g} ▲ 매수 — 다음 거래일 시가",
                "title": f"{dates[pending]} AI 점수 {score[pending] * 100:.0f}점(95점 상향 돌파) + 볼린저 가운데 아래 → 다음 거래일 시가에 1차(절반) 매수"}
    if tr and tr[-1][2] == n - 1:
        i, b, j, px, ret, why = tr[-1]
        return {"kind": "sell", "text": f"{g} ▼ 매도 {ret * 100:+.1f}%", "title": f"{dates[j]} {why} — {px:,.0f}원에 매도({dates[b]} 매수)"}
    h = holding(dates, o, c, tr, aux)
    if h:
        if h["add_pending"]:
            nth = h.get("fills", 1) + 1
            return {"kind": "add", "text": f"{g} ▲ {nth}차 매수 — 다음 거래일 시가",
                    "title": f"오늘 종가가 추가 매수 기준(1차 매수가 {h['buy_px']:,.0f}원 대비)에 닿음 → 다음 거래일 시가에 {nth}차 매수(지금 {h['ret'] * 100:+.1f}%)"}
        return {"kind": "hold", "text": f"{g} 보유 중 {h['ret'] * 100:+.1f}% · {h['days']}일째",
                "title": f"{h['buy']} 매수" + (f" + {h['add']} 2차 매수" if h["add"] else "") + f", 평균 단가 {h['avg']:,.0f}원 · 매도 조건: {describe(group)[1]}"}
    return {"kind": "wait", "text": f"{g} 관망 · {sc_txt}", "title": "매수 신호 없음 — " + describe(group)[0]}


def holding(dates, o, c, tr, aux):
    """마지막 거래가 보유 중이면 {buy, buy_px, avg, add, add_pending, ret(평균 단가 기준), days}, 아니면 None."""
    if not tr or tr[-1][2] is not None:
        return None
    b = tr[-1][1]
    fl = fills_of(aux, b, o)
    e, avg = fl[0][1], avg_price(fl)
    return {"buy": dates[b], "buy_px": e, "avg": round(avg, 2), "add": dates[fl[1][0]] if len(fl) > 1 else None, "fills": len(fl),
            "add_pending": b in aux.get("add_pending", []), "ret": round(float(c[-1] / avg - 1), 4), "days": int(len(c) - 1 - b)}
