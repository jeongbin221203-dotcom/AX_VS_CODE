"""분석 결과(data/*.json)를 한 장의 독립 HTML 보고서로 묶는다 → data/report.html (외부 파일 없이 열림).
실행: python tools/build_report.py
"""
import html
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "report.html"


def load(name):
    try:
        return json.loads((DATA / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def pct(v, d=1, sign=False):
    if v is None:
        return "–"
    return f"{v * 100:+.{d}f}%" if sign else f"{v * 100:.{d}f}%"


def num(v):
    return "–" if v is None else f"{v:,.0f}"


def esc(s):
    return html.escape(str(s))


def cls(v):
    return "up" if (v or 0) > 0 else "down"


def table(head, rows, note=None):
    th = "".join(f"<th{' class=num' if i else ''}>{esc(h)}</th>" for i, h in enumerate(head))
    body = []
    for r in rows:
        attrs = ""
        if isinstance(r, dict):
            attrs = f" class=\"{r.get('cls', '')}\"" if r.get("cls") else ""
            r = r["cells"]
        tds = []
        for i, c in enumerate(r):
            if isinstance(c, tuple):
                c, k = c
                tds.append(f"<td class=\"num {k}\">{c}</td>")
            else:
                tds.append(f"<td{' class=num' if i else ''}>{c}</td>")
        body.append(f"<tr{attrs}>{''.join(tds)}</tr>")
    s = f"<div class=scroll><table><thead><tr>{th}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    if note:
        s += f"<p class=hint>{note}</p>"
    return s


def ul(lines):
    return "<ul class=sum>" + "".join(f"<li>{esc(x)}</li>" for x in lines) + "</ul>"


def st6(s):
    if not s:
        return ["–"] * 6
    return [num(s["n"]), (pct(s["mean"], 2, True), cls(s["mean"])), (pct(s.get("excess"), 2, True), cls(s.get("excess"))), pct(s["p_up"], 0), pct(s["p_big_up"], 0), pct(s["p_dn20"], 0)]


H6 = ["표본", "20일 평균", "시장 대비", "상승", "+10%↑", "−20%↓"]


def sec_overview(reps):
    c, s, r = reps["combined"], reps["strategy"], reps["risk"]
    cards = []
    if c:
        st = c["stages"]
        base = st[0]["te"]
        best = next((x for x in st if x["name"].startswith("5'")), None)
        cards.append(("거래 가능 종목 아무 날 (시험 기간 2022~)", pct(base["p_up"], 0), f"20일 평균 {pct(base['mean'], 2, True)}"))
        if best:
            cards.append(("피함 제외 + 갭 + AI 상위 10%", pct(best["te"]["p_up"], 0), f"20일 평균 {pct(best['te']['mean'], 2, True)} · 시장 대비 {pct(best['te']['excess'], 2, True)}p"))
    if s:
        steps = s["improve"]["steps"]
        mk = next((x for x in steps if "시장 필터" in x["name"]), None)
        if mk:
            cards.append(("+ 시장 52주 고점 −10% 아래일 때만", pct(mk["te"]["p_up"], 0), f"20일 평균 {pct(mk['te']['mean'], 2, True)} · 한 달 {mk['per_month_te']:.0f}건"))
    if r:
        fin = r["improve"]["steps"][-1]
        cards.append(("+ " + " + ".join(r["improve"]["used"]) if r["improve"]["used"] else "보강 없음", pct(fin["te"]["p_up"], 0), f"20일 평균 {pct(fin['te']['mean'], 2, True)} · 한 달 {fin['per_month_te']:.0f}건"))
        w = r["walk"]["total"]
        if w.get("final"):
            cards.append(("걸어가며 검증 (해마다 AI 재학습, 2012~)", pct(w["final"]["p_up"], 0), f"평균 {pct(w['final']['mean'], 2, True)} · {r['walk']['years_better']}/{r['walk']['years']}년 개선"))
    h = "".join(f"<div class=card><div class=k>{esc(t)}</div><div class=v>{v}</div><div class=d>{esc(d)}</div></div>" for t, v, d in cards)
    return f"<div class=cards>{h}</div>"


def sec_combined(c):
    if not c:
        return ""
    out = [ul(c["text"])]
    rows = []
    for s in c["stages"]:
        t = s["te"]
        rows.append({"cells": [esc(s["name"]), f"{s['per_month_te']:.0f}"] + st6(t) + [pct(t["q05"], 0, True) if t else "–", pct(t.get("mae"), 1, True) if t else "–",
                                                                                     f"{t['t']:.1f}" if t and t.get("t") is not None else "–"], "cls": "hl" if s["name"].startswith("5'") else ""})
    out.append("<h3>규칙을 하나씩 쌓았을 때 (시험 기간 2022~)</h3>" + table(["단계", "한 달 신호"] + H6 + ["최악 5%", "최대 낙폭", "t(날짜)"], rows))
    rows = [{"cells": [esc(r["name"]), "✔" if r["pass"] else "", num(r["tr"]["n"]) if r["tr"] else "–", (pct(r["tr"]["excess"], 2, True), cls(r["tr"]["excess"])) if r["tr"] else "–",
                       num(r["te"]["n"]) if r["te"] else "–", (pct(r["te"]["mean"], 2, True), cls(r["te"]["mean"])) if r["te"] else "–", (pct(r["te"]["excess"], 2, True), cls(r["te"]["excess"])) if r["te"] else "–",
                       pct(r["te"]["p_up"], 0) if r["te"] else "–", f"{r['te']['t']:.1f}" if r["te"] and r["te"].get("t") is not None else "–"], "cls": "hl" if r["pass"] else ""} for r in c["candidates"]]
    out.append("<h3>진입 신호 후보 (학습·시험 모두 시장보다 좋아야 채택)</h3>" + table(["신호", "채택", "학습 표본", "학습 시장 대비", "시험 표본", "시험 평균", "시험 시장 대비", "시험 상승", "t"], rows))
    out.append("<h3>피함 규칙 7개</h3>" + table(["규칙", "표본", "비중", "시험 기간 시장 대비"], [[esc(r["name"]), num(r["n"]), pct(r["share"], 1), (pct(r["excess_te"], 2, True), "down")] for r in c["avoid"]["rows"]]))
    rg = c["final"]["regime"]
    out.append("<h3>최종 규칙(신호)의 시장 상황별</h3>" + table(["시장 60일 수익률", "표본", "20일 평균", "시장 대비", "시험 기간 시장 대비", "상승"],
                                                       [[esc(r["name"]), num(r["n"]), (pct(r["mean"], 2, True), cls(r["mean"])), (pct(r["excess"], 2, True), cls(r["excess"])), pct(r.get("excess_te"), 2, True), pct(r["p_up"], 0)] for r in rg]))
    return "".join(out)


def sec_strategy(s):
    if not s:
        return ""
    out = [ul(s["text"])]
    rows = [{"cells": [esc(r["name"]), f"{r['per_month_te']:.0f}"] + st6(r["tr"]) + st6(r["te"]), "cls": "hl" if i == len(s["improve"]["steps"]) - 1 else ""} for i, r in enumerate(s["improve"]["steps"])]
    out.append("<h3>K. 보강 규칙</h3>" + table(["단계", "한 달(시험)"] + ["학습 " + h for h in H6] + ["시험 " + h for h in H6], rows))
    mk = s["market"]
    rows = [{"cells": [esc(f["name"]), "✔" if f["robust"] else "", num(f["n_te"]), pct(f["share_te"], 0), pct(f["p_up_tr"], 0), f"{f['d_up_tr'] * 100:+.1f}%p", pct(f["p_up_te"], 0), f"{f['d_up_te'] * 100:+.1f}%p",
                       (pct(f["mean_tr"], 2, True), cls(f["mean_tr"])), (pct(f["mean_te"], 2, True), cls(f["mean_te"]))], "cls": "hl" if f["robust"] else ""} for f in mk["filters"]]
    out.append(f"<h3>B. 시장 타이밍 ({esc(mk['base']['label'])} 기준: 학습 상승 {pct(mk['base']['tr']['p_up'], 0)} · 시험 {pct(mk['base']['te']['p_up'], 0)})</h3>"
               + table(["시장 조건", "두 시기 개선", "시험 표본", "남는 비율", "학습 상승", "기준 대비", "시험 상승", "기준 대비", "학습 평균", "시험 평균"], rows))
    w = s["walk"]
    rows = []
    for r in w["rows"]:
        cells = [r["year"], r["n_picked"]]
        for k in ("sig", "ai"):
            x = r[k]
            cells += [num(x["n"]), (pct(x["mean"], 2, True), cls(x["mean"])), (pct(x["excess"], 2, True), cls(x["excess"])), pct(x["p_up"], 0)] if x else ["–"] * 4
        cells += [pct(r["base"]["p_up"], 0) if r["base"] else "–"]
        rows.append(cells)
    t = w["total"]
    rows.append({"cells": ["합계", ""] + sum(([num(t[k]["n"]), pct(t[k]["mean"], 2, True), f"{pct(t[k]['excess'], 2, True)} ({t[k + '_years_pos']}/{t[k + '_years']}년 +)", pct(t[k]["p_up"], 0)] if t.get(k) else ["–"] * 4 for k in ("sig", "ai")), []) + [pct(t["base"]["p_up"], 0) if t.get("base") else "–"], "cls": "hl"})
    out.append("<h3>C. 걸어가며 검증 (해마다 그 해 전까지로 신호 재선택·AI 재학습)</h3>" + table(["연도", "고른 신호", "신호 표본", "평균", "시장 대비", "상승", "AI 표본", "평균", "시장 대비", "상승", "기준 상승"], rows))
    rows = []
    for r in s["exits"]["ai"]:
        cells = [esc(r["name"])]
        for k in ("tr", "te"):
            x = r.get(k)
            cells += [(pct(x["mean"], 2, True), cls(x["mean"])), pct(x["p_up"], 0), pct(x["p_dn10"], 0), f"{x['days']:.0f}"] if x else ["–"] * 4
        rows.append({"cells": cells, "cls": "hl" if r["name"].startswith("20일 뒤") else ""})
    out.append("<h3>D. 청산 규칙 (AI 규칙)</h3>" + table(["청산", "학습 평균", "상승", "−10%↓", "보유일", "시험 평균", "상승", "−10%↓", "보유일"], rows))
    pf = s["portfolio"]
    rows = [[esc(k), f"{v['final']:.2f}배", (pct(v["cagr"], 1, True), cls(v["cagr"])), pct(v["mdd"], 0), num(v["trades"])] for k, v in pf.items() if k != "market"]
    rows.append({"cells": ["시장 (전 종목 같은 비중)", f"{pf['market']['curve_final']:.2f}배", "", "", ""], "cls": "hl"})
    out.append("<h3>E. 포트폴리오 시뮬레이션 (시험 기간, 동시 10종목, 20일 보유)</h3>" + table(["규칙 · 비중", "최종 자산", "연수익", "최대 낙폭", "거래"], rows))
    for g in s["traits"]["ai"]:
        rows = [{"cells": [esc(r["name"]), pct(r["share"], 0)] + st6(r["te"]), "cls": "hl" if r["robust"] else ""} for r in g["rows"]]
        out.append(f"<h4>F. 종목 특성별 — {esc(g['group'])} (AI 규칙, 시험)</h4>" + table(["구간", "비중"] + H6, rows))
    q = s["quiet"]
    rows = [[esc(r["name"]), pct(r["share"], 1), pct(r["up20_60"], 0), pct(r["surge60"], 0), (pct(r["r60"], 1, True), cls(r["r60"]))] for r in q["rows"]]
    out.append(f"<h3>G. 조용한 매집 (기준: 60일 안 +20% {pct(q['base']['up20'], 0)} · 급증 {pct(q['base']['surge'], 0)})</h3>" + table(["조건", "비중", "60일 안 +20%", "60일 안 거래량 급증", "60일 평균"], rows))
    ac = s.get("ai_check")
    if ac:
        rows = [["실제 +20%"] + [pct(b["hit"], 1) for b in ac["calibration"]], ["20일 평균"] + [(pct(b["mean"], 2, True), cls(b["mean"])) for b in ac["calibration"]]]
        out.append("<h3>H. AI 캘리브레이션 (시험 기간 점수 10구간)</h3>" + table(["구간"] + [str(b["bin"]) for b in ac["calibration"]], rows))
        out.append(table(["모델 · 필터"] + H6, [{"cells": [esc(r["name"])] + st6(r), "cls": "hl" if r["filter"].startswith("상위 10% +") else ""} for r in ac["models"]]))
    f = s["fail"]
    out.append(f"<h3>I. 실패 원인 (−10% 이하로 끝난 진입 {num(f['n_lose_tr'])}/{num(f['n_tr'])})</h3>" + table(["특징", "크게 진 쪽", "나머지", "차이(표준편차)"], [[esc(r["label"]), f"{r['lose']:.3f}", f"{r['rest']:.3f}", f"{r['d']:+.2f}"] for r in f["diff"][:10]]))
    return "".join(out)


def sec_risk(r):
    if not r:
        return ""
    out = [ul(r["text"])]
    pt = r["patterns"]
    rows = [{"cells": [esc(x["name"]), "✔" if x["robust"] else "", num(x["n"]), pct(x["share"], 1), pct(x["p120"], 2), f"{x['lift_tr']:.1f}배", f"{x['lift_te']:.1f}배"], "cls": "hl" if x["robust"] else ""} for x in pt["rules"]]
    out.append(f"<h3>거래정지 전 위험 조건 (기준 120일 안 거래정지 {pct(pt['base'], 2)})</h3>" + table(["조건", "✔", "표본", "비중", "120일 안 거래정지", "학습 배수", "시험 배수"], rows))
    md = r["model"]
    out.append(f"<h3>거래정지 위험 모델 (AUC 시험 {md['auc']:.3f})</h3>" + table(["구간"] + [str(b["bin"]) for b in md["deciles"]], [["예측"] + [pct(b["p_mean"], 2) for b in md["deciles"]], ["실제"] + [pct(b["actual"], 2) for b in md["deciles"]]]))
    out.append(table(["등급", "시험 비중", "시험 실제 거래정지"], [[esc(t["name"]), pct(t["te"]["share"], 1), pct(t["te"]["actual"], 2)] for t in md["tiers"]]))
    rows = [{"cells": [esc(x["name"]), f"{x['per_month_te']:.0f}"] + st6(x["tr"]) + st6(x["te"]) + [pct(x["tgt10_te"]["p_up"], 0) if x.get("tgt10_te") else "–"], "cls": "hl" if i == len(r["improve"]["steps"]) - 1 else ""} for i, x in enumerate(r["improve"]["steps"])]
    out.append("<h3>성공률 올리기</h3>" + table(["단계", "한 달(시험)"] + ["학습 " + h for h in H6] + ["시험 " + h for h in H6] + ["목표+10% 상승"], rows))
    w = r["walk"]
    rows = []
    for x in w["rows"]:
        cells = [x["year"]]
        for k in ("base", "final"):
            y = x[k]
            cells += [num(y["n"]), (pct(y["mean"], 2, True), cls(y["mean"])), pct(y["p_up"], 0)] if y else ["–"] * 3
        rows.append(cells)
    t = w["total"]
    rows.append({"cells": [f"합계 ({w['years_better']}/{w['years']}년 개선)"] + sum(([num(t[k]["n"]), pct(t[k]["mean"], 2, True), pct(t[k]["p_up"], 0)] if t.get(k) else ["–"] * 3 for k in ("base", "final")), []), "cls": "hl"})
    out.append("<h3>걸어가며 검증 (해마다 AI 재학습)</h3>" + table(["연도", "시장 필터 표본", "평균", "상승", "보강 표본", "평균", "상승"], rows))
    td = r["today"]
    if td.get("rows"):
        rows = [{"cells": [esc(x["name"]) + f" <small>{x['code']}</small>", num(x["close"]), pct(x["prob"], 0) if x["prob"] is not None else "–", esc(x["tier"]), pct(x["halt_p"], 1), esc(" · ".join(x["signals"])), pct(x["ret20"], 1, True), pct(x["nh250"], 0, True)],
                 "cls": "faint" if x["tier"] == "높음" else ""} for x in td["rows"]]
        out.append(f"<h3>{esc(td['date'])} AI 상위 후보와 거래정지 위험 (시장 52주 고점 대비 {pct(td['market_under']['dd250'], 1, True)} → 시장 필터 {'켜짐' if td['market_under']['under'] else '꺼짐'})</h3>"
                   + table(["종목", "종가", "AI 점수", "위험", "예측", "채택 신호", "20일 등락", "52주 고점 대비"], rows))
    if td.get("risk_rows"):
        rows = [[esc(x["name"]) + f" <small>{x['code']}</small>", num(x["close"]), esc(x["tier"]), pct(x["halt_p"], 1), pct(x["ret20"], 1, True), pct(x["nh250"], 0, True), pct(x["dist224"], 0, True), f"{x['liq'] / 1e8:.1f}억"] for x in td["risk_rows"][:30]]
        out.append("<h3>거래정지 위험 중간·높음 종목 (상위 30)</h3>" + table(["종목", "종가", "위험", "예측", "20일 등락", "52주 고점 대비", "224일선 대비", "거래대금"], rows))
    return "".join(out)


def sec_surge(s):
    if not s:
        return ""
    out = [ul(s["text"])]
    lv = s["levels"][0]
    b = s["baseline"]
    rows = []
    for x in ("10", "20", "50"):
        rows.append([f"+{x}%"] + [pct(lv["hit"][x][n], 0) for n in ("5", "20", "60", "120")] + [pct(b["hit"][x][n], 0) for n in ("5", "20", "60", "120")] + [f"{lv['days'][x]['median']:.0f}일" if lv["days"][x]["median"] else "–"])
    out.append("<h3>급증 뒤 N일 안에 닿은 비율 (급증 vs 아무 날) · 닿은 경우 중앙 일수</h3>" + table(["목표", "급증 5일", "20일", "60일", "120일", "아무 날 5일", "20일", "60일", "120일", "중앙 일수"], rows))
    sp = s.get("speed") or []
    rows = [[esc(x["name"]), num(x["n"]), pct(x.get("gain_med"), 0, True) if x.get("gain_med") is not None else "–", pct(x.get("p100"), 0) if x.get("p100") is not None else "–", pct(x["rs_any"], 0), pct(x.get("rs_trig"), 0) if x.get("rs_trig") is not None else "–"] for x in sp]
    out.append("<h3>빨리 오른 급증 vs 늦게 오른 급증</h3>" + table(["+20%까지", "표본", "최고 상승 중앙", "+100%↑", "재폭등(5배↑) 있음", "닿기 직전 5일 재폭등"], rows))
    sg = s.get("signals")
    if sg:
        rows = [[esc(x["name"]), num(x["n"]), pct(x["p20_in20"], 0), f"{x['lift20'] * 100:+.0f}%p", pct(x["p50_in120"], 0)] for x in sg["singles"][:8]]
        out.append("<h3>급증과 겹친 신호 (20일 안 +20% 확률 상위)</h3>" + table(["조건", "표본", "20일 안 +20%", "평균 대비", "120일 안 +50%"], rows))
    return "".join(out)


def sec_boom(b):
    if not b:
        return ""
    out = [ul(b["text"])]
    s = b["stats"]
    out.append(table(["정점 상승폭(중앙)", "상위 25%", "60일 안 +100%", "정점까지(중앙)", "첫날 +10%↑", "첫날 거래량 3배↑", "정점 뒤 60일 되돌림(중앙)", "120일 뒤 평균", "상승폭 절반 유지"],
                     [[pct(s["gain_med"], 0, True), pct(s["gain_q75"], 0, True), pct(s["gain100"], 0), f"{s['peak_med']:.0f}일", pct(s["first_up10"], 0), pct(s["first_vr3"], 0), pct(s["retrace_med"], 0, True), pct(s["r120"], 0, True), pct(s["r120_keep_half"], 0)]]))
    rows = [{"cells": [esc(r["group"]), esc(r["name"]), pct(r["boom"], 0), pct(r["base"], 0), (f"{r['lift']:.1f}배", "up" if r["lift"] >= 1.5 else "down" if r["lift"] <= 0.67 else ""), pct(r.get("pre10"), 0), f"{r['lift10']:.1f}배" if r.get("lift10") else "–", pct(r["enterable"], 0)]} for r in b["conditions"]]
    out.append("<h3>오르기 전 공통점 (급등 시작일 vs 아무 날)</h3>" + table(["묶음", "조건", "급등 시작일", "아무 날", "배수", "10일 전", "배수", "첫날 +10% 미만"], rows))
    sel = [-20, -10, -5, -3, -1, 0, 1, 2, 3, 5, 10]
    ps = {p["d"]: p for p in b["paths"]}
    out.append("<h3>가격·거래량 경로 (시작일 = 0)</h3>" + table(["일"] + [str(d) for d in sel], [["종가(시작일=1)"] + [f"{ps[d]['c']:.2f}" for d in sel], ["거래량 배수(중앙)"] + [f"{ps[d]['v']:.2f}" for d in sel], ["거래량 2배↑ 날 비율"] + [pct(ps[d]["v2"], 0) for d in sel], ["아무 날 2배↑ 비율"] + [pct(ps[d]["bv2"], 0) for d in sel]]))
    rows = [[esc(w["name"]), pct(w["share_first"], 0), pct(w["share_any"], 0), pct(w["gain"], 0, True), f"{w['peak_day']:.0f}일", pct(w["enterable"], 0), pct(w["retrace"], 0, True), pct(w["r120"], 0, True)] for w in b["why"]]
    out.append("<h3>왜 올랐나 (가격·거래량 맥락 추정)</h3>" + table(["유형", "배정", "해당", "정점 상승폭", "정점까지", "첫날 +10% 미만", "정점 뒤 되돌림", "120일 뒤"], rows))
    c = b.get("cache")
    if c:
        out.append("<h3>급등 직전 기법 신호 (아무 날 대비 배수)</h3>" + table(["신호", "아무 날", "아직 안 오른 급등 전", "배수"], [[esc(x["name"]), pct(x["base"], 1), pct(x["pre"], 1), f"{x['lift_pre']:.1f}배"] for x in c["signals"]]))
    return "".join(out)


def sec_jump(j):
    if not j:
        return ""
    out = [ul(j["text"])]
    def g7(s):
        return [num(s["n"]), (pct(s["mean"], 1, True), cls(s["mean"])), pct(s["p_up"], 0), pct(s.get("touch20"), 0), pct(s.get("giveback"), 0), pct(s.get("next_jump250"), 0)] if s else ["–"] * 6
    H = ["건수", "20일 평균", "상승", "+20% 더", "시가 아래로", "250일 안 재급등"]
    out.append("<h3>같은 종목의 이전 급등 내역별 (전체 / 시험 2022~)</h3>" + table(["구분", "비중"] + H + ["시험 " + h for h in H], [[esc(r["name"]), pct(r["share"], 0)] + g7(r["all"]) + g7(r["te"]) for r in j["history"]]))
    out.append("<h3>급등일 모습별</h3>" + table(["구분", "비중"] + H + ["시험 " + h for h in H], [[esc(r["name"]), pct(r["share"], 0)] + g7(r["all"]) + g7(r["te"]) for r in j["context"]]))
    for mdl in j["models"]:
        out.append(f"<h3>정확성 — {esc(mdl['target'])}</h3>" + table(["특징", "AUC", "기준 적중", "상위 10% 적중", "하위 10% 적중", "상위 10% 20일 평균", "상위 10% 상승"],
                   [{"cells": [esc(r["name"]), f"{r['auc']:.3f}", pct(r["base"], 1), pct(r["top10_hit"], 1), pct(r["bot10_hit"], 1), (pct(r["top10_r20"], 1, True), cls(r["top10_r20"])), pct(r["top10_up"], 0)], "cls": "hl" if i else ""} for i, r in enumerate(mdl["rows"])]))
    td = j["today"]
    if td.get("rows"):
        out.append(f"<h3>{esc(td['since'])} 이후 급등일 (20일 플러스 확률 순)</h3>" + table(["종목", "급등일", "등락", "20일 플러스 확률", "250일 안 이전 급등", "마지막 급등 뒤", "이전 급등 20일 결과", "거래량 배수"],
                   [[esc(x["name"]) + f" <small>{x['code']}</small>", x["date"], pct(x["jump"], 1, True), pct(x["prob"], 0), f"{x['n_j250']}회", f"{x['days_since']}일" if x["days_since"] is not None else "없음", pct(x["prev_r20"], 0, True) if x["prev_r20"] is not None else "–", f"{x['volratio']:.1f}배"] for x in td["rows"][:25]]))
    return "".join(out)


def sec_precursor(p):
    if not p:
        return ""
    out = [ul(p["text"])]
    rows = [{"cells": [esc(r["group"]), esc(r["name"]), pct(r["share_pre"], 0), pct(r["share_base"], 0), f"{r['lift_share']:.1f}배", pct(r["p20"], 1), (f"{r['lift20']:.1f}배", "up" if r["lift20"] >= 1.3 else "down" if r["lift20"] <= 0.77 else ""),
                       f"{r['lift_tr']:.1f}" if r["lift_tr"] else "–", f"{r['lift_te']:.1f}" if r["lift_te"] else "–"], "cls": "hl" if r["robust"] else ""} for r in p["conditions"]]
    out.append(f"<h3>조건별 전조 (기준: 20일 안 급등 {pct(p['base']['p20'], 1)})</h3>" + table(["묶음", "조건", "급등 전날", "아무 날", "배수", "20일 안 급등", "배수", "학습", "시험"], rows))
    sel = [-59, -40, -20, -10, -5, -3, -2, -1, 0]
    ps_ = {x["d"]: x for x in p["paths"]}
    out.append("<h3>급등 전 60일 경로 (전날 = 0, 중앙값)</h3>" + table(["일"] + [str(d) for d in sel], [["224일선 대비"] + [pct(ps_[d]["d224"], 0, True) for d in sel], ["224일선 위 비율"] + [pct(ps_[d]["above224"], 0) for d in sel], ["거래량 배수"] + [f"{ps_[d]['v']:.2f}" for d in sel], ["종가(전날=1)"] + [f"{ps_[d]['c']:.2f}" for d in sel]]))
    rows = [{"cells": [esc(n), f"{r['auc']:.3f}", pct(r["base"], 1)] + [pct(d["actual"], 1) for d in r["deciles"]] + [pct(r["top1"], 1)], "cls": "hl" if i else ""} for i, (n, r) in enumerate(p["model"].items())]
    out.append("<h3>전조 모델 (시험 기간 점수 10구간의 실제 급등 비율)</h3>" + table(["특징", "AUC", "기준"] + [f"{i}구간" for i in range(1, 11)] + ["상위 1%"], rows))
    td = p.get("today") or {}
    if td.get("rows"):
        out.append(f"<h3>{esc(td['date'])} 전조 점수 상위</h3>" + table(["종목", "종가", "20일 안 급등 확률", "224일선 대비", "20일 수익", "5일÷20일 거래량", "변동성", "250일 안 급등"],
                   [[esc(x["name"]) + f" <small>{x['code']}</small>", num(x["close"]), pct(x["prob"], 0), pct(x["dist224"], 0, True), pct(x["ret20"], 0, True), f"{x['vr5']:.1f}배", pct(x["atrp"], 1), f"{int(x['n_j250'])}회"] for x in td["rows"][:25]]))
    return "".join(out)


def sec_pattern(p):
    if not p:
        return ""
    out = [ul(p["text"])]
    rows = [{"cells": [esc(r["group"]), esc(r["name"]), pct(r["share_pre"], 1), pct(r["share_base"], 1), f"{r['lift_share']:.1f}배", pct(r["p20"], 1), (f"{r['lift20']:.1f}배", "up" if r["lift20"] >= 1.3 else "down" if r["lift20"] <= 0.77 else ""),
                       f"{r['lift_tr']:.1f}" if r["lift_tr"] else "–", f"{r['lift_te']:.1f}" if r["lift_te"] else "–"], "cls": "hl" if r["robust"] else ""} for r in p["rows"]]
    out.append(f"<h3>패턴별 (기준: 20일 안 급등 {pct(p['base']['p20'], 1)})</h3>" + table(["묶음", "패턴", "급등 전날", "아무 날", "배수", "20일 안 급등", "배수", "학습", "시험"], rows))
    if p.get("combos"):
        out.append("<h3>패턴 겹침</h3>" + table(["조합", "아무 날 건수", "20일 안 급등", "배수", "시험"], [[esc(c["name"]), num(c["n"]), pct(c["p20"], 1), f"{c['lift']:.1f}배", pct(c["p_te"], 1)] for c in p["combos"]]))
    rows = [{"cells": [esc(n), f"{r['auc']:.3f}", pct(r["base"], 1)] + [pct(d, 1) for d in r["deciles"]] + [pct(r["top1"], 1)], "cls": "hl" if n.startswith("패턴·신호 +") else ""} for n, r in p["model"].items()]
    out.append("<h3>패턴 모델</h3>" + table(["특징", "AUC", "기준"] + [f"{i}구간" for i in range(1, 11)] + ["상위 1%"], rows))
    td = p.get("today") or {}
    if td.get("rows"):
        out.append(f"<h3>{esc(td['date'])} 패턴 점수 상위</h3>" + table(["종목", "종가", "20일 안 급등 확률", "패턴", "기법 신호", "250일 안 급등"],
                   [[esc(x["name"]) + f" <small>{x['code']}</small>", num(x["close"]), pct(x["prob"], 0), esc(" · ".join(x["patterns"])), esc(" · ".join(x["signals"])), f"{int(x['n_j250'])}회"] for x in td["rows"][:25]]))
    return "".join(out)


def sec_quietvol(q):
    if not q:
        return ""
    out = [ul(q["text"])]
    rows = []
    for g in q["groups"]:
        rows.append({"cells": [esc(g["name"]), num(g["n"])] + [pct(g["hit"]["20"][h], 0) for h in ("5", "10", "20", "40", "60", "120")] + [f"{g['days']['20']['median']:.0f}일" if g["days"]["20"]["median"] else "–", pct(g["dn10_first"], 0), (pct(g["r20"], 1, True), cls(g["r20"])), (pct(g["r60"], 1, True), cls(g["r60"])), pct(g["jump20"], 1)], "cls": "hl" if g["key"] == "quiet" else ""})
    out.append("<h3>+20%에 닿은 비율 (N일 안) · 중앙 일수 · 20일 안 급등일</h3>" + table(["구분", "건수", "5일", "10일", "20일", "40일", "60일", "120일", "며칠 뒤", "−10% 먼저", "20일 평균", "60일 평균", "20일 안 급등일"], rows))
    rows = []
    for grp in q["breakdown"]:
        for r in grp["rows"]:
            rows.append([esc(grp["group"]), esc(r["name"]), num(r["n"]), pct(r["p20_in20"], 0), pct(r["p20_in60"], 0), f"{r['days20']:.0f}일" if r["days20"] else "–", pct(r["dn10_first"], 0), (pct(r["r20"], 1, True), cls(r["r20"])), (pct(r["r60"], 1, True), cls(r["r60"]))])
    out.append("<h3>조건별</h3>" + table(["묶음", "조건", "건수", "20일 안 +20%", "60일 안", "며칠 뒤", "−10% 먼저", "20일 평균", "60일 평균"], rows))
    if q.get("recent"):
        out.append("<h3>최근 조용한 대량 거래 종목 (비슷한 과거 그룹의 결과)</h3>" + table(["종목", "날짜", "배수", "등락", "52주 고점 대비", "과거 그룹", "20일 안 +20%", "60일 안", "며칠 뒤"],
                   [[esc(r["name"]) + f" <small>{r['code']}</small>", r["date"], f"{r['volratio']:.1f}배", pct(r["ret1"], 1, True), pct(r["nh250"], 0, True), esc(r["group"]), pct(r["p20_in20"], 0), pct(r["p20_in60"], 0), f"{r['days20']:.0f}일" if r["days20"] else "–"] for r in q["recent"][:25]]))
    return "".join(out)


def sec_timing(t):
    if not t:
        return ""
    out = [ul(t["text"])]
    rows = [{"cells": [esc(x["label"]), "✔" if x["robust"] and not x.get("ai") else "", num(x["n_te"]), (pct(x["base"]["te"]["mean"], 2, True), cls(x["base"]["te"]["mean"])), (pct(x["base"]["te"].get("excess"), 2, True), cls(x["base"]["te"].get("excess"))), pct(x["base"]["te"]["p_up"], 0),
                       esc(x["best_entry"]), esc(x["best_exit"]), esc(x["best_cond"]), pct(x["best_p_up"], 0)], "cls": "hl" if x["robust"] and not x.get("ai") else ""} for x in t["techs"]]
    out.append("<h3>기법별 권장 매수 시점 (시험 기간 시장 대비 순)</h3>" + table(["기법", "✔", "시험 신호", "다음날 시가 전액 평균", "시장 대비", "상승", "권장 진입", "권장 청산", "권장 조건", "권장대로 상승"], rows))
    td = t.get("today") or {}
    if td.get("rows"):
        out.append(f"<h3>{esc(td['date'])} 신호 종목의 권장 매수 시점 (상위 30)</h3>" + table(["종목", "오늘 신호", "기준 신호", "권장 진입", "권장 청산", "권장 조건", "상승 확률", "AI", "거래정지 위험"],
                   [{"cells": [esc(x["name"]) + f" <small>{x['code']}</small>", esc(" · ".join(x["signals"]) + (" · AI 상위" if x["ai_top"] else "")), esc(x["best_signal"] or "–") + (" ✔" if x["robust"] else ""), esc(x["entry"]), esc(x["exit"]), esc(x["cond"]), pct(x["p_up"], 0), pct(x["prob"], 0), pct(x["halt_p"], 1)], "cls": "faint" if x["avoid"] else ""} for x in td["rows"][:30]]))
    return "".join(out)


def sec_presignal(p):
    if not p:
        return ""
    out = [ul(p["text"])]
    rows = []
    for t in p["techs"]:
        a, p5 = t["at_signal"]["te"], t["pre5_actual"]["te"]
        rows.append({"cells": [esc(t["label"]), "신호일 다음날 (기준)", "", "", (pct(a["mean"], 2, True), cls(a["mean"])) if a else "–", pct(a["p_up"], 0) if a else "–"], "cls": "hl"})
        rows.append({"cells": ["", "(사후) 5일 전 — 실현 불가", "100%", "", (pct(p5["mean"], 2, True), cls(p5["mean"])) if p5 else "–", pct(p5["p_up"], 0) if p5 else "–"], "cls": "faint"})
        for s_ in t["setups"]:
            te = s_["te"]
            rows.append([ "", esc("셋업: " + s_["name"]) + (" ✔" if s_["robust"] else ""), pct(s_["precision"], 0), f"{s_['lift']:.0f}배" if s_["lift"] else "–", (pct(te["mean"], 2, True), cls(te["mean"])) if te else "–", pct(te["p_up"], 0) if te else "–"])
    out.append("<h3>기법별 — 언제 사나 (시험 기간)</h3>" + table(["기법", "언제", "5일 안 신호 발생", "배수", "20일 평균", "상승"], rows))
    if p.get("sell"):
        rows = []
        for x in sorted(p["sell"], key=lambda z: -(z["best"]["close"]["mean"] if z.get("best") else -9)):
            if not x.get("best"):
                continue
            v = x["best"]
            rows.append({"cells": [esc(x["tech"]), esc(x["setup"]), f"{v['wait']}일" + (" · 손절 −5%" if v["stop"] else ""), pct(v["p_signal"], 0), (pct(v["close"]["mean"], 2, True), cls(v["close"]["mean"])), pct(v["close"]["p_up"], 0),
                                   pct(v["close"]["tr"], 2, True), pct(v["close"]["te"], 2, True), pct(v["close"]["came"], 2, True), pct(v["close"]["not"], 2, True), (pct(v["hold20"]["mean"], 2, True), cls(v["hold20"]["mean"]))], "cls": "hl" if x["robust"] else ""})
        out.append("<h3>전주 매수 → 신호일 종가 매도 (셋업마다 가장 나은 변형)</h3>" + table(["기법", "셋업", "변형", "신호 발생", "평균", "상승", "학습", "시험", "신호 왔을 때", "안 왔을 때", "20일 보유"], rows))
    m = p["model"]
    out.append(f"<h3>'5일 안 단테 신호' 예측 모델 (AUC {m['auc']:.3f})</h3>" + table(["구간"] + [str(d["bin"]) for d in m["deciles"]], [["실제 발생"] + [pct(d["actual"], 0) for d in m["deciles"]], ["그 자리 매수 20일"] + [(pct(d["r20"], 2, True), cls(d["r20"])) for d in m["deciles"]], ["상승"] + [pct(d["p_up"], 0) for d in m["deciles"]]]))
    td = p.get("today") or {}
    if td.get("rows"):
        out.append(f"<h3>{esc(td['date'])} 신호 임박 상위</h3>" + table(["종목", "5일 안 신호 확률", "셋업", "224일선 대비", "직전 언덕 대비"], [[esc(x["name"]) + f" <small>{x['code']}</small>", pct(x["prob"], 0), esc(" · ".join(x["setups"]) or "–"), pct(x["dist224"], 0, True), pct(x["hill_gap"], 0, True)] for x in td["rows"][:25]]))
    return "".join(out)


def sec_bestday(b):
    if not b:
        return ""
    out = [ul(b["text"])]
    def s7(s):
        return [num(s["n"]), (pct(s["mean"], 2, True), cls(s["mean"])), (pct(s.get("excess"), 2, True), cls(s.get("excess"))), pct(s["p_up"], 0), pct(s["p_dn10"], 0)] if s else ["–"] * 5
    H = ["건수", "20일 평균", "시장 대비", "상승", "−10%↓"]
    out.append("<h3>통합 점수 vs 기존 AI (시험 기간)</h3>" + table(["맞히려는 것", "특징", "AUC", "상위 1% 평균", "상승", "상위 5% 평균", "상승", "상위 10% 평균", "상승"],
               [{"cells": [esc(r["target"]), esc(r["features"]), f"{r['auc']:.3f}"] + sum(([(pct(r[k]["mean"], 2, True), cls(r[k]["mean"])), pct(r[k]["p_up"], 0)] for k in ("top1", "top5", "top10")), []), "cls": "hl" if r["features"].startswith("통합") else ""} for r in b["compare"]]))
    out.append("<h3>조건을 쌓았을 때 (학습 / 시험)</h3>" + table(["단계", "한 달(시험)"] + ["학습 " + h for h in H] + ["시험 " + h for h in H], [{"cells": [esc(x["name"]), f"{x['per_month_te']:.0f}"] + s7(x["tr"]) + s7(x["te"]), "cls": "hl" if i == len(b["stages"]) - 1 else ""} for i, x in enumerate(b["stages"])]))
    out.append("<h3>매수일 (점수 상위 10% · 피함 제외 · 시장 필터, 시험 기간)</h3>" + table(["매수일", "체결"] + ["시험 " + h for h in H] + ["신호당"], [{"cells": [esc(x["name"]), pct(x["coverage"], 0)] + s7(x["te"]) + [pct(x["per_signal_te"], 2, True)], "cls": "hl" if x["name"].startswith("오늘") else "faint" if x["name"].startswith("오라클") else ""} for x in b["timing"]]))
    tmd = b["timing_model"]
    out.append(table(["타이밍 규칙"] + ["시험 " + h for h in H], [[esc(x["name"])] + s7(x["te"]) for x in tmd["rules"]], note=f"타이밍 모델 AUC {tmd['auc']:.3f} · 상위 20% 실제 최저 {pct(tmd['hi_best5'], 0)} vs 하위 20% {pct(tmd['lo_best5'], 0)}"))
    w = b["walk"]
    rows = [[x["year"]] + s7(x["final"])[:4] + s7(x["base"])[:4] for x in w["rows"]]
    rows.append({"cells": [f"합계 ({w['years_better']}/{w['years']}년 우세)"] + s7(w["total"]["final"])[:4] + s7(w["total"]["base"])[:4], "cls": "hl"})
    out.append("<h3>걸어가며 검증 (해마다 재학습)</h3>" + table(["연도", "상위 10% 건수", "평균", "시장 대비", "상승", "전체 건수", "평균", "시장 대비", "상승"], rows))
    td = b.get("today") or {}
    if td.get("rows"):
        out.append(f"<h3>{esc(td['date'])} 통합 점수 상위 종목과 매수일 판정</h3>" + table(["종목", "통합 점수", "매수일", "타이밍 점수", "거래정지 위험", "최근 신호", "패턴"],
                   [{"cells": [esc(x["name"]) + f" <small>{x['code']}</small>", pct(x["score"], 0), "오늘" if x["buy_today"] else "기다리기", pct(x["timing"], 0), pct(x["halt_p"], 1), esc(" · ".join(x["signals"])), esc(" · ".join(x["patterns"]))], "cls": "faint" if x["avoid"] or (x["halt_p"] or 0) >= 0.03 else ""} for x in td["rows"][:30]]))
    return "".join(out)


def sec_plan(p):
    if not p:
        return ""
    out = [ul(p["text"])]
    rows = []
    for t in p["techs"]:
        g = t["oos"]["exit"] and t["oos"]["exit"]["mean"] > 0 and t["oos"]["years_pos"] >= t["years"] * 0.6
        e, nv = t["oos"]["exit"], t["oos"]["naive"]
        rows.append({"cells": [esc(t["label"]), "✔" if g and not t.get("ai") else "", f"{t['oos']['years_pos']}/{t['years']}", (pct(nv["mean"], 2, True), cls(nv["mean"])) if nv else "–", pct(nv["p_up"], 0) if nv else "–",
                               pct(e["coverage"], 0) if e else "–", (pct(e["mean"], 2, True), cls(e["mean"])) if e else "–", pct(e["p_up"], 0) if e else "–", pct(e["p_dn10"], 0) if e else "–",
                               esc(f"{t['rule_now']['cond']} → {t['rule_now']['entry']} → {t['rule_now']['exit']}")], "cls": "hl" if g and not t.get("ai") else ""})
    out.append("<h3>기법별 — 해마다 과거로만 고른 규칙을 다음 해에 적용 (처음 보는 해 합계)</h3>" + table(["기법", "✔", "플러스 해", "규칙 없이 평균", "상승", "규칙대로 체결", "평균", "상승", "−10%↓", "지금 규칙"], rows))
    mt = p["base"]["mae"]
    out.append("<h3>손절 위치 (아무 날 기준 MAE)</h3>" + table(["손절선", "걸린 비율", "걸린 뒤 결국 플러스", "걸린 뒤 평균", "이긴 거래 중 걸림", "진 거래 중 걸림"], [[f"−{int(r['level'] * 100)}%", pct(r["share"], 0), pct(r["win_after"], 0), (pct(r["mean_after"], 2, True), cls(r["mean_after"])), pct(r["win_share"], 0), pct(r["lose_share"], 0)] for r in mt["rows"]],
               note=f"이긴 거래 최대 하락 중앙 {pct(mt['mae_win_med'], 1, True)} · 진 거래 {pct(mt['mae_lose_med'], 1, True)}"))
    td = p.get("today") or {}
    if td.get("rows"):
        out.append(f"<h3>{esc(td['date'])} 신호 종목의 매매 계획 (상위 30)</h3>" + table(["종목", "신호", "기준 기법", "조건", "충족", "진입", "청산·손절", "과거 성과"],
                   [{"cells": [esc(x["name"]) + f" <small>{x['code']}</small>", esc(" · ".join(x["signals"]) + (" · AI" if x["ai_top"] else "")), esc(x["best"] or "–") + (" ✔" if x["good"] else ""), esc(x["cond"]), "✔" if x["cond_met"] else "✖", esc(x["entry"]), esc(x["exit"]), (f"{pct(x['oos_mean'], 2, True)} · {pct(x['oos_up'], 0)}" if x["oos_mean"] is not None else "–")], "cls": "faint" if x["avoid"] else ""} for x in td["rows"][:30]]))
    return "".join(out)


def sec_backtest(b):
    if not b or not b.get("labels"):
        return ""
    base = b.get("baseline", {}).get("fixed", {}).get("20") if isinstance(b.get("baseline"), dict) else None
    rows = []
    for lab, v in b["labels"].items():
        f20 = v.get("fixed", {}).get("20")
        if not f20:
            continue
        sim = v.get("sim", {}).get("atr2|3R")
        rows.append([esc(lab), esc(v.get("tech", "")), num(v["n_signals"]), (pct(f20["mean"], 2, True), cls(f20["mean"])), pct(f20["win"], 0),
                     pct(sim["win"], 0) if sim else "–", f"{sim['pf']:.2f}" if sim else "–", f"{sim['held_q50']:.0f}일" if sim and sim.get("held_q50") else "–"])
    note = f"기준(아무 날) 20일 평균 {pct(base['mean'], 2, True)} · 승률 {pct(base['win'], 0)}" if base else None
    return table(["신호", "기법", "신호 수", "20일 평균", "20일 승률", "2×ATR 손절·3R 승률", "손익비", "보유 중앙"], rows, note)


def sec_text(rep, key="text"):
    return ul(rep[key]) if rep and rep.get(key) else ""


CSS = """
:root{--bg:#fbfbf8;--fg:#1e1e1e;--muted:#6b6b6b;--line:#dcdcd4;--card:#f1f1ea;--acc:#2747a3;--up:#c23a2e;--down:#2a6fb8;--hl:#fff6d6}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#15171a;--fg:#e8e8e3;--muted:#9a9a93;--line:#33363b;--card:#1f2226;--acc:#8ea4e8;--up:#ff7b6e;--down:#6cb2ff;--hl:#2d2a1a}}
:root[data-theme="dark"]{--bg:#15171a;--fg:#e8e8e3;--muted:#9a9a93;--line:#33363b;--card:#1f2226;--acc:#8ea4e8;--up:#ff7b6e;--down:#6cb2ff;--hl:#2d2a1a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"Malgun Gothic","Apple SD Gothic Neo",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 60px}h1{font-size:26px;margin:0 0 4px}h1 small,h2 small,h3 small{font-size:13px;color:var(--muted);font-weight:400;margin-left:8px}
h2{font-size:20px;margin:40px 0 8px;padding-top:12px;border-top:2px solid var(--acc)}h3{font-size:16px;margin:22px 0 6px}h4{font-size:14px;margin:16px 0 4px;color:var(--muted)}
.note,.hint{color:var(--muted);font-size:13px}.sum{padding-left:20px}.sum li{margin:4px 0}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;margin:14px 0}.card{background:var(--card);border-radius:8px;padding:12px 14px}
.card .k{font-size:12px;color:var(--muted)}.card .v{font-size:28px;font-weight:700;color:var(--acc)}.card .d{font-size:12px}
.scroll{overflow-x:auto;margin:6px 0}table{border-collapse:collapse;width:100%;font-size:13px;white-space:nowrap}th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left}
th.num,td.num{text-align:right;font-variant-numeric:tabular-nums}thead th{background:var(--card);position:sticky;top:0}
tr.hl td{background:var(--hl);font-weight:600}tr.faint td{opacity:.5}.up{color:var(--up)}.down{color:var(--down)}
nav.toc{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:13px;margin:10px 0 0}nav.toc a{color:var(--acc);text-decoration:none}
small{color:var(--muted)}
"""


def build():
    reps = {k: load(f"{k}.json") for k in ("combined", "strategy", "risk", "surge", "avoid", "entry", "ai", "backtest", "why", "boom", "jump", "precursor", "pattern", "quietvol", "timing", "presignal", "bestday", "plan")}
    meta = (reps["risk"] or reps["strategy"] or reps["combined"] or {}).get("meta", {})
    sections = [
        ("overview", "한눈에 보기", sec_overview(reps)),
        ("plan", "매매 계획 — 기법별 최적 매수 시점·매도·손절 (미래를 모르는 가정)", sec_plan(reps["plan"])),
        ("bestday", "모든 신호로 고르는 최적 매수일 — 통합 점수와 타이밍", sec_bestday(reps["bestday"])),
        ("timing", "매수 시점 — 기법별 진입·청산·조건과 오늘 신호 종목", sec_timing(reps["timing"])),
        ("presignal", "단테 신호 한 주 전 매수 — 셋업의 정확도와 수익", sec_presignal(reps["presignal"])),
        ("risk", "거래정지 위험 · 성공률 보강", sec_risk(reps["risk"])),
        ("strategy", "전략 보강 (시장 타이밍 · 걸어가며 검증 · 청산 · 포트폴리오 · 특성 · 매집 · AI · 실패)", sec_strategy(reps["strategy"])),
        ("combined", "종합 전략 (피함 → 신호 → 갭 → 3분할 → AI)", sec_combined(reps["combined"])),
        ("precursor", "+20% 급등 전조 — 224일선·이평선·거래량", sec_precursor(reps["precursor"])),
        ("pattern", "+20% 급등 전 패턴 — 매집봉·거래량·가격·기법 신호", sec_pattern(reps["pattern"])),
        ("jump", "하루 +20% 급등일 — 그 뒤와 이전 급등 내역", sec_jump(reps["jump"])),
        ("boom", "급등주 — 왜 올랐나, 오르기 전 공통점", sec_boom(reps["boom"])),
        ("quietvol", "조용한 대량 거래 — 거래량은 터졌는데 주가는 제자리, 얼마 뒤에 올랐나", sec_quietvol(reps["quietvol"])),
        ("surge", "거래량 급증 뒤 큰 상승", sec_surge(reps["surge"])),
        ("avoid", "피할 종목 · 갭", sec_text(reps["avoid"])),
        ("entry", "진입 시점", sec_text(reps["entry"])),
        ("ai", "AI 분석 (20일 +20%)", sec_text(reps["ai"])),
        ("backtest", "기법 정확도 (단테 기법 백테스트)", sec_backtest(reps["backtest"])),
    ]
    sections = [s for s in sections if s[2]]
    toc = "<nav class=toc>" + "".join(f"<a href=#{i}>{esc(t.split(' (')[0])}</a>" for i, t, _ in sections) + "</nav>"
    body = "".join(f"<section id={i}><h2>{esc(t)}</h2>{h}</section>" for i, t, h in sections)
    split = meta.get("split", {})
    page = f"""<!doctype html><html lang=ko><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>주식 분석 보고서</title><meta name=description content="거래 데이터 {esc(split.get('date_min', '')[:4])}~{esc(split.get('date_max', '')[:4])} 전 종목 백테스트: 피할 종목, 진입 신호, AI, 시장 타이밍, 거래정지 위험을 쌓아 상승 확률을 어디까지 올릴 수 있는지"><style>{CSS}</style></head>
<body><main><h1>주식 차트 분석 보고서 <small>개인 학습용 · 생성 {datetime.now().strftime('%Y-%m-%d %H:%M')}</small></h1>
<p class=note>거래 가능 종목(20일 평균 거래대금 3억↑) {num(meta.get('rows'))}개 표본({num(meta.get('stocks'))}종목, {esc(split.get('date_min', '')[:4])}~{esc(split.get('date_max', '')[:4])}). 진입은 신호일 다음날 시가, 청산은 20거래일 뒤 종가(비용 0.3% 차감). 규칙은 ~2021년에서 정하고 2022년 이후는 확인만(시험 기간). "시장 대비" = 같은 날 모든 표본 평균을 뺀 값. 상장폐지 종목이 빠진 생존 편향이 있고, 과거 통계이며 투자 권유가 아닙니다.</p>
{toc}{body}</main></body></html>"""
    OUT.write_text(page, encoding="utf-8")
    return OUT, len(page)


if __name__ == "__main__":
    p, n = build()
    print(p, f"{n / 1024:.0f}KB")
