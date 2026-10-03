"""엑셀 일괄 등록: 거래처 · BOM (처음 도입할 때 하나씩 넣지 않게).

공통: 회사 엑셀/CSV → 열 이름 맞추기(엑셀 양식의 별칭, core/excel_forms.py) → 줄마다 검사 → 미리보기 →
'반영'을 누르면 한 트랜잭션으로 모두 반영. 한 줄이라도 문제가 있으면 아무것도 반영하지 않는다.

거래처   거래처코드가 있으면 그 코드, 없으면 이름(표기 무시)으로 찾아 있으면 갱신(빈 칸은 그대로), 없으면 등록.
         '다른 이름' 칸(쉼표로 여러 개)은 다른 이름으로 연결한다.
BOM      제품코드별로 묶어 그 제품의 BOM 전체를 파일 내용으로 바꾼다(줄을 지우려면 파일에서 빼면 된다).
         부품·출고 창고는 코드로. 순환·자기 자신·같은 부품 두 줄은 거부.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from core import audit, db, partners, production, services
from core.utils import clean_str_series, code_series, now_str

PARTNER_COLS = {"code": "거래처코드", "name": "거래처명", "kind": "구분", "biz_no": "사업자등록번호", "contact": "담당자",
                "phone": "전화", "email": "메일", "note": "메모", "aliases": "다른 이름"}
BOM_COLS = {"product": "제품코드", "base_qty": "기준수량", "component": "부품코드", "qty": "수량", "scrap_pct": "손실률",
            "wh": "출고창고", "note": "메모"}
KIND_WORDS = {"공급처": "SUPPLIER", "공급": "SUPPLIER", "매입": "SUPPLIER", "SUPPLIER": "SUPPLIER",
              "납품처": "CUSTOMER", "납품": "CUSTOMER", "매출": "CUSTOMER", "고객": "CUSTOMER", "CUSTOMER": "CUSTOMER",
              "공급·납품": "BOTH", "공급납품": "BOTH", "둘다": "BOTH", "BOTH": "BOTH", "": "SUPPLIER"}
MAX_ROWS = 5000


@dataclass
class Preview:
    rows: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    summary: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.rows)


def _frame(raw: pd.DataFrame, cols: dict[str, str]) -> pd.DataFrame:
    reverse = {v: k for k, v in cols.items()}
    df = raw.rename(columns=lambda c: reverse.get(str(c).strip(), str(c).strip()))
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df[list(cols)]


def _num(v) -> float | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = str(v).replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return float("nan")


# ── 거래처 ───────────────────────────────────────────────────
def preview_partners(raw: pd.DataFrame) -> Preview:
    df = _frame(raw, PARTNER_COLS)
    texts = {c: clean_str_series(df[c]) for c in PARTNER_COLS if c != "code"}
    codes = code_series(df["code"]).str.upper()
    out = Preview()
    seen_keys, seen_codes = {}, {}
    existing = db.query_df("SELECT id, code, name, name_key, biz_no FROM partners")
    by_code = {r.code: r for r in existing.itertuples()}
    by_key = {r.name_key: r for r in existing.itertuples()}
    for i in range(len(df)):
        line = i + 2                                          # 엑셀 줄 번호(머리글 1줄)
        name, code = texts["name"].iloc[i], codes.iloc[i]
        if not name and not code:
            continue
        kind_raw = texts["kind"].iloc[i].replace(" ", "")
        row = {"line": line, "code": code, "name": name, "kind": KIND_WORDS.get(kind_raw.upper(), KIND_WORDS.get(kind_raw)),
               "biz_no": partners.biz_digits(texts["biz_no"].iloc[i]), "contact": texts["contact"].iloc[i],
               "phone": texts["phone"].iloc[i], "email": texts["email"].iloc[i], "note": texts["note"].iloc[i],
               "aliases": [a.strip() for a in texts["aliases"].iloc[i].replace(";", ",").split(",") if a.strip()],
               "problem": ""}
        k = partners.key(name)
        target = by_code.get(code) if code else None
        if target is None and k:
            target = by_key.get(k)
        row["action"] = "갱신" if target is not None else "등록"
        row["id"] = int(target.id) if target is not None else None
        problems = []
        if not name and target is None:
            problems.append("새 거래처는 거래처명이 필요합니다.")
        if row["kind"] is None:
            problems.append(f"구분 '{texts['kind'].iloc[i]}'를 알 수 없습니다 (공급처·납품처·공급·납품).")
        if row["biz_no"] and not partners.biz_no_ok(row["biz_no"]):
            problems.append("사업자등록번호 검증번호가 맞지 않습니다.")
        if k and k in seen_keys:
            problems.append(f"{seen_keys[k]}번 줄과 같은 거래처입니다.")
        if code and code in seen_codes:
            problems.append(f"{seen_codes[code]}번 줄과 거래처코드가 같습니다.")
        if target is not None and k and by_key.get(k) is not None and int(by_key[k].id) != int(target.id):
            problems.append(f"이름이 다른 거래처({by_key[k].code})와 같습니다.")
        if k:
            seen_keys[k] = line
        if code:
            seen_codes[code] = line
        row["problem"] = " ".join(problems)
        if problems:
            out.errors.append(f"{line}번 줄: {row['problem']}")
        out.rows.append(row)
    if len(out.rows) > MAX_ROWS:
        out.errors.insert(0, f"한 번에 {MAX_ROWS:,}줄까지 올릴 수 있습니다.")
    new = sum(1 for r in out.rows if r["action"] == "등록")
    out.summary = f"{len(out.rows)}줄 — 등록 {new} · 갱신 {len(out.rows) - new}"
    return out


def apply_partners(rows: list[dict], actor: dict | None) -> services.Result:
    who = actor or audit.SYSTEM
    try:
        with db.transaction() as conn:
            for r in rows:
                data = {f: r.get(f) or "" for f in partners.FIELDS}
                if r["action"] == "갱신":
                    cur = dict(conn.execute("SELECT * FROM partners WHERE id = ?", (r["id"],)).fetchone())
                    merged = {f: (data[f] if data[f] else cur[f]) for f in partners.FIELDS}
                    merged["kind"] = data["kind"] or cur["kind"]
                    if partners.key(merged["name"]) == cur["name_key"]:
                        merged["name"] = cur["name"]                  # 표기만 다른 같은 이름이면 정식 이름은 그대로
                    d, problem = partners._clean(merged)
                    problem = problem or partners._conflict(conn, d, r["id"])
                    if problem:
                        raise services._Rejected(r["line"], problem)
                    conn.execute("UPDATE partners SET name = ?, name_key = ?, biz_no = ?, kind = ?, contact = ?, phone = ?, "
                                 "email = ?, note = ?, updated_at = ? WHERE id = ?",
                                 (d["name"], partners.key(d["name"]), d["biz_no"], d["kind"], d["contact"], d["phone"],
                                  d["email"], d["note"], now_str(), r["id"]))
                    pid = r["id"]
                    if partners.key(d["name"]) != cur["name_key"]:     # 코드로 찾아 이름을 바꿨으면 예전 이름은 다른 이름으로
                        partners._add_alias(conn, pid, cur["name"], who)
                else:
                    d, problem = partners._clean(data)
                    problem = problem or partners._conflict(conn, d, None)
                    if problem:
                        raise services._Rejected(r["line"], problem)
                    code = r["code"] or partners._next_code(conn)
                    if conn.execute("SELECT 1 FROM partners WHERE code = ?", (code,)).fetchone():
                        raise services._Rejected(r["line"], f"거래처코드 {code}가 이미 있습니다.")
                    ts = now_str()
                    pid = conn.execute(
                        "INSERT INTO partners (code, name, name_key, biz_no, kind, contact, phone, email, note, active, "
                        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                        (code, d["name"], partners.key(d["name"]), d["biz_no"], d["kind"], d["contact"], d["phone"],
                         d["email"], d["note"], ts, ts)).lastrowid
                for a in r.get("aliases") or []:
                    partners._add_alias(conn, pid, a, who)
            audit.record(conn, who, "PARTNER_IMPORT", "partner", "",
                         {"rows": len(rows), "new": sum(1 for r in rows if r["action"] == "등록")})
    except services._Rejected as exc:
        return services.Result(False, f"{exc.no}번 줄 때문에 아무것도 반영하지 않았습니다: {exc.message}")
    return services.Result(True, f"거래처 {len(rows)}줄 반영 (등록 {sum(1 for r in rows if r['action'] == '등록')})")


# ── BOM ──────────────────────────────────────────────────────
def preview_boms(raw: pd.DataFrame) -> Preview:
    df = _frame(raw, BOM_COLS)
    prod = code_series(df["product"]).str.upper()
    comp = code_series(df["component"]).str.upper()
    whc = code_series(df["wh"]).str.upper()
    notes = clean_str_series(df["note"])
    mats = {r.code.upper(): r for r in db.query_df("SELECT id, code, name, active FROM materials").itertuples()}
    whs = {r.code.upper(): int(r.id) for r in db.query_df("SELECT id, code FROM warehouses WHERE active = 1").itertuples()}
    out = Preview()
    groups: dict[str, dict] = {}
    for i in range(len(df)):
        line = i + 2
        p, c = prod.iloc[i], comp.iloc[i]
        if not p and not c:
            continue
        problems = []
        if not p or p not in mats or not mats[p].active:
            problems.append(f"제품코드 '{p}'가 사용 중인 자재가 아닙니다.")
        if not c or c not in mats or not mats[c].active:
            problems.append(f"부품코드 '{c}'가 사용 중인 자재가 아닙니다.")
        qty, base, scrap = _num(df["qty"].iloc[i]), _num(df["base_qty"].iloc[i]), _num(df["scrap_pct"].iloc[i])
        if qty is None or qty != qty or qty <= 0:
            problems.append("수량은 0보다 큰 숫자여야 합니다.")
        if base is not None and (base != base or base <= 0):
            problems.append("기준수량은 0보다 큰 숫자여야 합니다.")
        if scrap is not None and (scrap != scrap or not 0 <= scrap < 100):
            problems.append("손실률은 0 이상 100 미만(%)이어야 합니다.")
        if whc.iloc[i] and whc.iloc[i] not in whs:
            problems.append(f"출고창고 '{whc.iloc[i]}'가 없습니다.")
        g = groups.setdefault(p, {"product": p, "base": None, "lines": [], "first": line})
        if base is not None and base == base:
            if g["base"] is not None and abs(g["base"] - base) > 1e-9:
                problems.append(f"같은 제품({p})의 기준수량이 줄마다 다릅니다.")
            g["base"] = base
        row = {"line": line, "product": p, "component": c, "qty": qty, "scrap": scrap or 0.0, "wh": whc.iloc[i],
               "note": notes.iloc[i], "base": base if base is not None and base == base and base > 0 else None,
               "problem": " ".join(problems)}
        g["lines"].append(row)
        out.rows.append(row)
        if problems:
            out.errors.append(f"{line}번 줄: {row['problem']}")
    for p, g in groups.items():                               # 제품별 검사 (자기 자신·같은 부품 두 줄)
        if p not in mats or any(r["problem"] for r in g["lines"]):
            continue
        lines = [production.BomLine(int(mats[r["component"]].id), r["qty"], r["scrap"], whs.get(r["wh"]), r["note"])
                 for r in g["lines"]]
        _, problem = production.check_bom(int(mats[p].id), g["base"] or 1, lines)
        if problem:
            out.errors.append(f"제품 {p} ({g['first']}번 줄부터): {problem}")
    out.summary = f"제품 {len(groups)}개 · 부품 줄 {len(out.rows)}개 (제품마다 BOM 전체를 이 내용으로 바꿉니다)"
    return out


def apply_boms(rows: list[dict], actor: dict | None) -> services.Result:
    who = actor or audit.SYSTEM
    mats = {r.code.upper(): int(r.id) for r in db.query_df("SELECT id, code FROM materials").itertuples()}
    whs = {r.code.upper(): int(r.id) for r in db.query_df("SELECT id, code FROM warehouses").itertuples()}
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["product"], []).append(r)
    try:
        with db.transaction() as conn:
            for p, rs in groups.items():
                lines = [production.BomLine(mats[r["component"]], r["qty"], r["scrap"], whs.get(r["wh"]), r["note"]) for r in rs]
                base = next((r.get("base") for r in rs if r.get("base")), None) or 1
                lines, problem = production.check_bom(mats[p], base, lines)
                res = services.Result(False, problem) if problem else production._save_bom(conn, mats[p], base, lines,
                                                                                              "엑셀 일괄 등록", who)
                if not res.ok:
                    raise services._Rejected(rs[0]["line"], f"{p}: {res.message}")
            audit.record(conn, who, "BOM_IMPORT", "bom", "", {"products": list(groups)[:200], "lines": len(rows)})
    except services._Rejected as exc:
        return services.Result(False, f"{exc.no}번 줄부터 문제가 있어 아무것도 반영하지 않았습니다: {exc.message}")
    return services.Result(True, f"BOM {len(groups)}개 반영 (부품 줄 {len(rows)}개)")
