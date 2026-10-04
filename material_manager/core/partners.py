"""거래처 마스터: 공급처·납품처를 한 곳에서 관리하고, 화면·엑셀·명세서에 적힌 거래처 이름을 마스터에 맞춘다.

왜 필요한가 — 거래처를 글자로만 받으면 '대한상사'·'(주)대한상사'·'대한 상사㈜'가 서로 다른 거래처로 집계된다.

- 이름 비교는 name_key 로 한다: (주)·㈜·주식회사·유한회사 같은 법인 표시, 띄어쓰기, 기호를 빼고 소문자로.
- 같은 회사를 다르게 적은 이름은 '다른 이름'(partner_aliases)으로 연결한다 → 다음부터는 자동으로 그 거래처.
- 사업자등록번호(숫자 10자리, 검증번호 확인)로도 찾는다.
- resolve() 로 찾으면 거래에는 마스터의 정식 이름과 partner_id 가 남는다. 못 찾으면 적은 이름 그대로 남고
  partner_id 는 비어 있다 → '미등록 이름' 목록에서 거래처로 등록하거나 기존 거래처에 연결한다.
  (거래는 고칠 수 없으므로, 지난 거래는 다른 이름 연결로 집계에 합쳐진다.)
- config.PARTNER_REQUIRED 를 켜면 마스터에 없는 거래처 이름으로는 등록할 수 없다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

import config
from core import audit, db
from core.utils import now_str

KINDS = {"SUPPLIER": "공급처", "CUSTOMER": "납품처", "BOTH": "공급·납품"}
FIELDS = ("name", "biz_no", "kind", "contact", "phone", "email", "note")

_CORP = re.compile(r"\(주\)|\(유\)|\(재\)|\(사\)|㈜|㈔|주식회사|유한회사|유한책임회사|합자회사|합명회사|재단법인|사단법인"
                   r"|co\.?,?\s*ltd\.?|corp(oration)?\.?|inc\.?|ltd\.?|llc", re.IGNORECASE)
_NON_WORD = re.compile(r"[^0-9a-z가-힣]")


@dataclass
class PResult:
    ok: bool
    message: str
    id: int = 0


def key(name: str) -> str:
    """비교용 이름. '(주) 대한 상사' == '대한상사㈜' == '대한상사 주식회사'."""
    s = _CORP.sub("", (name or "").lower())
    return _NON_WORD.sub("", s)


def norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip())


def biz_digits(v: str) -> str:
    return re.sub(r"\D", "", v or "")


def biz_no_ok(digits: str) -> bool:
    """사업자등록번호 검증번호 확인 (국세청 방식)."""
    if not re.fullmatch(r"\d{10}", digits or ""):
        return False
    w = [1, 3, 7, 1, 3, 7, 1, 3, 5]
    d = [int(c) for c in digits]
    s = sum(a * b for a, b in zip(d[:9], w)) + (d[8] * 5) // 10
    return (10 - s % 10) % 10 == d[9]


def biz_fmt(digits: str) -> str:
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}" if len(digits or "") == 10 else (digits or "")


# ── 찾기 ─────────────────────────────────────────────────────
def resolve(conn, text: str) -> dict | None:
    """거래처 이름·코드·사업자번호 → 사용 중인 거래처 (없으면 None)."""
    text = norm_name(text)
    if not text:
        return None
    row = conn.execute("SELECT * FROM partners WHERE active = 1 AND UPPER(code) = ?", (text.upper(),)).fetchone()
    if row:
        return dict(row)
    k = key(text)
    if k:
        row = conn.execute("SELECT * FROM partners WHERE active = 1 AND name_key = ?", (k,)).fetchone()
        if row is None:
            row = conn.execute("SELECT p.* FROM partner_aliases a JOIN partners p ON p.id = a.partner_id "
                               "WHERE p.active = 1 AND a.alias_key = ?", (k,)).fetchone()
        if row:
            return dict(row)
    digits = biz_digits(text)
    if len(digits) == 10 and len(digits) >= len(text.replace("-", "").replace(" ", "")):
        row = conn.execute("SELECT * FROM partners WHERE active = 1 AND biz_no = ?", (digits,)).fetchone()
        if row:
            return dict(row)
    return None


def is_partner_tx(tx_type: str, cost_center: str = "", transfer_no: str = "") -> bool:
    """거래처가 있는 거래인가. 입고와, 원가센터 없는 출고(납품)만. 원가센터 출고의 '거래처' 칸은 사용 부서다."""
    return not transfer_no and (tx_type == "IN" or (tx_type == "OUT" and not cost_center))


PARTNER_TX_SQL = "{t}.transfer_no = '' AND ({t}.tx_type = 'IN' OR ({t}.tx_type = 'OUT' AND {t}.cost_center = ''))"


def apply(conn, text: str) -> tuple[str, int | None, str]:
    """거래에 남길 (이름, partner_id, 문제). 마스터에 있으면 정식 이름으로 바꾼다.
    문제: PARTNER_REQUIRED 일 때 마스터에 없는 이름."""
    text = norm_name(text)
    if not text:
        return "", None, ""
    p = resolve(conn, text)
    if p is None:
        if config.PARTNER_REQUIRED:
            return text, None, (f"거래처 '{text}'가 거래처 마스터에 없습니다. 거래처 메뉴에서 등록하거나 "
                                "기존 거래처의 다른 이름으로 연결한 뒤 다시 등록하세요.")
        return text, None, ""
    return p["name"], int(p["id"]), ""


def unknown_warning(name: str, partner_id) -> str:
    if name and partner_id is None:
        return f"거래처 '{name}'는 거래처 마스터에 없는 이름입니다 — 거래처 메뉴의 '미등록 이름'에서 등록·연결하세요."
    return ""


def names(kind: str = "") -> list[str]:
    """입력 칸 자동완성용 정식 이름 (사용 중인 것만)."""
    sql = "SELECT name FROM partners WHERE active = 1"
    params: tuple = ()
    if kind in ("SUPPLIER", "CUSTOMER"):
        sql += " AND kind IN (?, 'BOTH')"
        params = (kind,)
    return [r[0] for r in db.query_df(sql + " ORDER BY name", params).itertuples(index=False)]


# ── 등록 · 수정 ───────────────────────────────────────────────
def _clean(data: dict) -> tuple[dict, str]:
    d = {f: norm_name(str(data.get(f) or "")) for f in FIELDS}
    d["kind"] = d["kind"] if d["kind"] in KINDS else "BOTH"
    d["biz_no"] = biz_digits(d["biz_no"])
    if not d["name"]:
        return d, "거래처 이름을 입력하세요."
    if not key(d["name"]):
        return d, "거래처 이름에 글자가 없습니다 ((주) 같은 표시만으로는 안 됩니다)."
    if d["biz_no"] and not biz_no_ok(d["biz_no"]):
        return d, "사업자등록번호가 올바르지 않습니다 (10자리, 검증번호 확인)."
    if d["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", d["email"]):
        return d, "메일 주소 형식을 확인하세요."
    return d, ""


def _conflict(conn, d: dict, exclude_id: int | None) -> str:
    k = key(d["name"])
    row = conn.execute("SELECT id, name FROM partners WHERE name_key = ? AND id <> ?", (k, exclude_id or 0)).fetchone()
    if row:
        return f"이름이 같은 거래처가 이미 있습니다: {row['name']}"
    row = conn.execute("SELECT p.name FROM partner_aliases a JOIN partners p ON p.id = a.partner_id "
                       "WHERE a.alias_key = ? AND a.partner_id <> ?", (k, exclude_id or 0)).fetchone()
    if row:
        return f"이 이름은 거래처 '{row['name']}'의 다른 이름으로 연결돼 있습니다."
    if d["biz_no"]:
        row = conn.execute("SELECT name FROM partners WHERE biz_no = ? AND id <> ?", (d["biz_no"], exclude_id or 0)).fetchone()
        if row:
            return f"사업자등록번호가 같은 거래처가 이미 있습니다: {row['name']}"
    return ""


def _next_code(conn) -> str:
    db.lock(conn, "seq:partner")
    n = conn.execute("SELECT COUNT(*) FROM partners").fetchone()[0]
    while True:
        n += 1
        code = f"P{n:05d}"
        if not conn.execute("SELECT 1 FROM partners WHERE code = ?", (code,)).fetchone():
            return code


def create(data: dict, actor: dict | None, code: str = "") -> PResult:
    d, problem = _clean(data)
    if problem:
        return PResult(False, problem)
    code = norm_name(code).upper()
    if code and not re.fullmatch(r"[A-Z0-9._-]{1,20}", code):
        return PResult(False, "거래처 코드는 영문·숫자·._- 20자 이내로 입력하세요.")
    ts = now_str()
    try:
        with db.transaction() as conn:
            problem = _conflict(conn, d, None)
            if problem:
                return PResult(False, problem)
            code = code or _next_code(conn)
            pid = conn.execute(
                "INSERT INTO partners (code, name, name_key, biz_no, kind, contact, phone, email, note, active, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (code, d["name"], key(d["name"]), d["biz_no"], d["kind"], d["contact"], d["phone"], d["email"],
                 d["note"], ts, ts)).lastrowid
            audit.record(conn, actor, "PARTNER_CREATE", "partner", pid, {"code": code, **d})
    except db.IntegrityError:
        return PResult(False, f"거래처 코드 '{code}' 또는 이름이 이미 있습니다.")
    return PResult(True, f"거래처 [{code}] {d['name']} 등록", pid)


def update(partner_id: int, data: dict, actor: dict | None, expected: str | None = None) -> PResult:
    d, problem = _clean(data)
    if problem:
        return PResult(False, problem)
    with db.transaction() as conn:
        before = conn.execute("SELECT * FROM partners WHERE id = ?", (partner_id,)).fetchone()
        if before is None:
            return PResult(False, "거래처가 없습니다.")
        before = dict(before)
        if expected and str(before["updated_at"]) != expected:
            return PResult(False, "화면을 연 뒤 다른 사용자가 이 거래처를 먼저 바꿨습니다. 새로고침한 뒤 다시 저장하세요.")
        problem = _conflict(conn, d, partner_id)
        if problem:
            return PResult(False, problem)
        diff = audit.changes(before, d, FIELDS)
        if not diff:
            return PResult(True, "변경된 내용이 없습니다.", partner_id)
        conn.execute("UPDATE partners SET name = ?, name_key = ?, biz_no = ?, kind = ?, contact = ?, phone = ?, email = ?, "
                     "note = ?, updated_at = ? WHERE id = ?",
                     (d["name"], key(d["name"]), d["biz_no"], d["kind"], d["contact"], d["phone"], d["email"], d["note"],
                      now_str(), partner_id))
        if before["name_key"] != key(d["name"]):          # 예전 이름으로 적은 거래도 계속 이 거래처로
            _add_alias(conn, partner_id, before["name"], actor)
        audit.record(conn, actor, "PARTNER_UPDATE", "partner", partner_id, {"code": before["code"], **diff})
    return PResult(True, "거래처를 저장했습니다.", partner_id)


def set_active(partner_id: int, active: bool, actor: dict | None) -> PResult:
    with db.transaction() as conn:
        before = conn.execute("SELECT code, active FROM partners WHERE id = ?", (partner_id,)).fetchone()
        if before is None:
            return PResult(False, "거래처가 없습니다.")
        conn.execute("UPDATE partners SET active = ?, updated_at = ? WHERE id = ?", (1 if active else 0, now_str(), partner_id))
        audit.record(conn, actor, "PARTNER_ACTIVE", "partner", partner_id,
                     {"code": before["code"], "active": [bool(before["active"]), active]})
    return PResult(True, "다시 사용합니다." if active else "사용중지했습니다. 지난 거래는 그대로입니다.", partner_id)


def _add_alias(conn, partner_id: int, alias: str, actor: dict | None) -> bool:
    k = key(alias)
    if not k or conn.execute("SELECT 1 FROM partner_aliases WHERE alias_key = ?", (k,)).fetchone():
        return False
    if conn.execute("SELECT 1 FROM partners WHERE name_key = ?", (k,)).fetchone():
        return False
    conn.execute("INSERT INTO partner_aliases (partner_id, alias, alias_key, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                 (partner_id, norm_name(alias), k, (actor or audit.SYSTEM)["name"], now_str()))
    return True


def link(alias: str, partner_id: int, actor: dict | None) -> PResult:
    """미등록 이름을 기존 거래처의 다른 이름으로 연결한다."""
    alias = norm_name(alias)
    k = key(alias)
    if not k:
        return PResult(False, "연결할 이름이 없습니다.")
    with db.transaction() as conn:
        p = conn.execute("SELECT id, name FROM partners WHERE id = ?", (partner_id,)).fetchone()
        if p is None:
            return PResult(False, "거래처가 없습니다.")
        other = conn.execute("SELECT name FROM partners WHERE name_key = ? AND id <> ?", (k, partner_id)).fetchone()
        if other:
            return PResult(False, f"'{alias}'는 다른 거래처({other['name']})의 정식 이름입니다.")
        row = conn.execute("SELECT partner_id FROM partner_aliases WHERE alias_key = ?", (k,)).fetchone()
        if row and int(row["partner_id"]) != partner_id:
            return PResult(False, f"'{alias}'는 이미 다른 거래처에 연결돼 있습니다.")
        if row is None and conn.execute("SELECT 1 FROM partners WHERE id = ? AND name_key = ?", (partner_id, k)).fetchone() is None:
            _add_alias(conn, partner_id, alias, actor)
        audit.record(conn, actor, "PARTNER_ALIAS", "partner", partner_id, {"alias": alias})
    return PResult(True, f"'{alias}'를 거래처 {p['name']}에 연결했습니다. 이 이름으로 적은 거래도 이 거래처로 집계됩니다.",
                   partner_id)


def unlink(alias_id: int, actor: dict | None) -> PResult:
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM partner_aliases WHERE id = ?", (alias_id,)).fetchone()
        if row is None:
            return PResult(False, "연결이 없습니다.")
        conn.execute("DELETE FROM partner_aliases WHERE id = ?", (alias_id,))
        audit.record(conn, actor, "PARTNER_UNALIAS", "partner", row["partner_id"], {"alias": row["alias"]})
    return PResult(True, f"'{row['alias']}' 연결을 풀었습니다.", int(row["partner_id"]))


# ── 조회 ─────────────────────────────────────────────────────
# 지난 거래의 거래처 이름 → 거래처 (partner_id 가 있으면 그것, 없으면 이름·다른 이름으로)
def _name_sources() -> str:
    return """
        SELECT partner AS name, partner_id, 'TX' AS src, tx_type AS kind, qty * unit_price AS amount, tx_date AS at
          FROM transactions t WHERE partner <> '' AND reversal_of IS NULL AND """ + PARTNER_TX_SQL.format(t="t") + """
        UNION ALL
        SELECT supplier, supplier_id, 'PO', 'IN', total_amount, created_at FROM purchase_orders WHERE supplier <> ''
        UNION ALL
        SELECT supplier, NULL, 'MAT', '', 0, '' FROM materials WHERE supplier <> ''
    """


def _key_map() -> dict[str, int]:
    """name_key·alias_key → partner_id (사용중지 포함 — 지난 거래 집계용)."""
    out = {}
    for pid, k in db.query_df("SELECT id, name_key FROM partners").itertuples(index=False):
        out[k] = int(pid)
    for pid, k in db.query_df("SELECT partner_id, alias_key FROM partner_aliases").itertuples(index=False):
        out.setdefault(k, int(pid))
    return out


def _name_counts() -> pd.DataFrame:
    """partner_id 없이 적힌 거래처 이름별 (쓰인 곳, 건수, 마지막) — DB에서 묶어서 가져온다(거래 수와 무관하게 이름 수만큼)."""
    return db.query_df(f"""
        SELECT name, src, kind, COUNT(*) AS n, MAX(at) AS last, SUM(amount) AS amount
        FROM ({_name_sources()}) s WHERE s.partner_id IS NULL GROUP BY name, src, kind""")


def unknown_count() -> int:
    """미등록 이름 수 (목록 화면 안내·데이터 점검) — 거래 전체를 훑으므로 60초 보관 (core/cache.py)."""
    from core import cache
    return cache.memo(("partners_unknown_cnt",), 60, lambda: len(unknown_names(limit=100000)))


def unknown_names(limit: int = 300) -> pd.DataFrame:
    """마스터에 없는(연결도 안 된) 거래처 이름과 쓰인 횟수 — 데이터 정리 목록."""
    cols = ["name", "uses", "tx", "po", "mat", "last", "variants", "guess"]
    df = _name_counts()
    if df.empty:
        return pd.DataFrame(columns=cols)
    known = _key_map()
    df["k"] = df["name"].map(key)
    df = df[(df["k"] != "") & ~df["k"].isin(known)]
    if df.empty:
        return pd.DataFrame(columns=cols)
    rows = []
    for k, grp in df.groupby("k"):
        by_name = grp.groupby("name")["n"].sum()
        rows.append({"name": by_name.idxmax(), "uses": int(grp["n"].sum()),
                     "tx": int(grp.loc[grp["src"] == "TX", "n"].sum()), "po": int(grp.loc[grp["src"] == "PO", "n"].sum()),
                     "mat": int(grp.loc[grp["src"] == "MAT", "n"].sum()), "last": grp["last"].max(),
                     "variants": " / ".join(sorted(by_name.index)[:4])})
    out = pd.DataFrame(rows, columns=cols[:-1])
    out = out.sort_values(["uses", "name"], ascending=[False, True]).head(limit)
    active = list(db.query_df("SELECT id, name, name_key FROM partners WHERE active = 1").itertuples(index=False))
    out["guess"] = out["name"].map(lambda n: _guess(n, active))
    return out.sort_values(["uses", "name"], ascending=[False, True]).head(limit).reset_index(drop=True)


def _guess(name: str, active=None) -> str:
    """비슷한 정식 거래처 (한쪽 이름이 다른 쪽을 포함) — 연결 후보로 보여 준다."""
    k = key(name)
    if len(k) < 2:
        return ""
    if active is None:
        active = db.query_df("SELECT id, name, name_key FROM partners WHERE active = 1").itertuples(index=False)
    for pid, pname, pk in active:
        if len(pk) >= 2 and (k in pk or pk in k):
            return f"{pid}|{pname}"
    return ""


def list_df(q: str = "", include_inactive: bool = False, wh_ids=None) -> pd.DataFrame:
    """거래처 목록 + 거래 실적(다른 이름으로 적은 지난 거래 포함)."""
    df = db.query_df("SELECT * FROM partners" + ("" if include_inactive else " WHERE active = 1") + " ORDER BY name")
    if df.empty:
        return df.assign(in_amt=[], out_amt=[], tx_cnt=[], last_tx=[], aliases=[])
    k = key(q)
    if q.strip():
        alias_hit = set(db.query_df("SELECT partner_id FROM partner_aliases WHERE alias_key LIKE ?",
                                    (f"%{k}%",))["partner_id"].astype(int))
        df = df[df["name_key"].str.contains(k, regex=False) | df["code"].str.contains(q.strip().upper(), regex=False)
                | df["biz_no"].str.contains(biz_digits(q) or "§", regex=False) | df["id"].isin(alias_hit)]
    stats = activity(wh_ids)
    df = df.merge(stats, how="left", left_on="id", right_index=True)
    for c in ("in_amt", "out_amt", "tx_cnt"):
        df[c] = df[c].fillna(0)
    df["last_tx"] = df["last_tx"].fillna("")
    al = db.query_df("SELECT partner_id, alias FROM partner_aliases ORDER BY alias")
    df["aliases"] = df["id"].map(al.groupby("partner_id")["alias"].agg(lambda s: ", ".join(s))).fillna("")
    return df.reset_index(drop=True)


def activity(wh_ids=None) -> pd.DataFrame:
    """거래처별 입고 금액·출고 금액·거래 수·마지막 거래일. DB에서 묶어 계산한다:
    partner_id 가 있는 거래는 그 거래처로, 없는(마스터 전에 적은) 거래는 이름 → 이름·다른 이름으로 맞춘다.
    취소 거래는 원거래와 상쇄된다(금액은 부호가 반대, 건수는 원거래만)."""
    cols = ["in_amt", "out_amt", "tx_cnt", "last_tx"]
    agg = """SUM(CASE WHEN t.tx_type = 'IN' THEN t.qty * t.unit_price ELSE 0 END) AS in_amt,
             SUM(CASE WHEN t.tx_type = 'OUT' THEN t.qty * t.unit_price ELSE 0 END) AS out_amt,
             SUM(CASE WHEN t.reversal_of IS NULL THEN 1 ELSE 0 END) AS tx_cnt, MAX(t.tx_date) AS last_tx"""
    cond = PARTNER_TX_SQL.format(t="t")
    wfrag, wp = db.in_clause(None if wh_ids is None else (list(wh_ids) or [-1]))
    if wfrag:                                                    # 창고 범위 사용자는 자기 창고 거래 실적만
        cond += f" AND t.warehouse_id{wfrag}"
    by_id = db.query_df(f"""
        SELECT COALESCE(t.partner_id, o.partner_id) AS pid, {agg}
        FROM transactions t LEFT JOIN transactions o ON o.id = t.reversal_of
        WHERE COALESCE(t.partner_id, o.partner_id) IS NOT NULL AND {cond}
        GROUP BY COALESCE(t.partner_id, o.partner_id)""", wp)
    by_name = db.query_df(f"""
        SELECT t.partner AS name, {agg}
        FROM transactions t LEFT JOIN transactions o ON o.id = t.reversal_of
        WHERE t.partner <> '' AND t.partner_id IS NULL AND o.partner_id IS NULL AND {cond}
        GROUP BY t.partner""", wp)
    if not by_name.empty:
        known = _key_map()
        by_name["pid"] = by_name["name"].map(lambda n: known.get(key(n)))
        by_name = by_name.dropna(subset=["pid"]).drop(columns="name")
    frames = [f for f in (by_id, by_name) if not f.empty]
    if not frames:
        return pd.DataFrame(columns=cols)
    both = pd.concat(frames, ignore_index=True)
    both["pid"] = both["pid"].astype(int).map(_final_map())
    return both.groupby("pid").agg(in_amt=("in_amt", "sum"), out_amt=("out_amt", "sum"), tx_cnt=("tx_cnt", "sum"),
                                   last_tx=("last_tx", "max"))


def _final_map() -> "callable":
    """병합 사슬을 따라간 최종 거래처 id (A→B→C 면 A·B·C 모두 C)."""
    into = {int(r.id): int(r.merged_into) for r in db.query_df(
        "SELECT id, merged_into FROM partners WHERE merged_into IS NOT NULL").itertuples()}

    def final(pid: int) -> int:
        seen = set()
        while pid in into and pid not in seen:
            seen.add(pid)
            pid = into[pid]
        return pid
    return final


def merged_ids(partner_id: int) -> list[int]:
    """이 거래처로 병합된(사슬 포함) 거래처 id."""
    final = _final_map()
    ids = db.query_df("SELECT id FROM partners WHERE merged_into IS NOT NULL")["id"].astype(int).tolist()
    return [i for i in ids if final(i) == partner_id]


# ── 병합 ─────────────────────────────────────────────────────
def merge(src_id: int, dst_id: int, actor: dict | None) -> PResult:
    """중복 거래처 src 를 dst 로 합친다. src 의 이름·다른 이름은 dst 의 다른 이름이 되고, src 는 사용중지(병합됨).
    발주·거래명세서·자재 공급처는 dst 로 바꾸고, 고칠 수 없는 지난 거래는 병합 기록으로 dst 실적에 합쳐진다."""
    if src_id == dst_id:
        return PResult(False, "같은 거래처끼리는 병합할 수 없습니다.")
    who = actor or audit.SYSTEM
    with db.transaction() as conn:
        src = conn.execute("SELECT * FROM partners WHERE id = ?", (src_id,)).fetchone()
        dst = conn.execute("SELECT * FROM partners WHERE id = ?", (dst_id,)).fetchone()
        if src is None or dst is None:
            return PResult(False, "거래처가 없습니다.")
        if src["merged_into"] is not None:
            return PResult(False, f"{src['name']}는 이미 다른 거래처로 병합됐습니다.")
        if not dst["active"] or dst["merged_into"] is not None:
            return PResult(False, "남길 거래처는 사용 중이어야 합니다.")
        src, dst = dict(src), dict(dst)
        ts = now_str()
        conn.execute("UPDATE partners SET name_key = ?, active = 0, merged_into = ?, biz_no = '', updated_at = ? WHERE id = ?",
                     (f"merged:{src_id}:{src['name_key']}", dst_id, ts, src_id))
        conn.execute("UPDATE partner_aliases SET partner_id = ? WHERE partner_id = ?", (dst_id, src_id))
        _add_alias(conn, dst_id, src["name"], who)
        if not dst["biz_no"] and src["biz_no"]:
            conn.execute("UPDATE partners SET biz_no = ? WHERE id = ?", (src["biz_no"], dst_id))
        for col, val in (("contact", src["contact"]), ("phone", src["phone"]), ("email", src["email"])):
            if not dst[col] and val:
                conn.execute(f"UPDATE partners SET {col} = ? WHERE id = ?", (val, dst_id))
        po = conn.execute("UPDATE purchase_orders SET supplier_id = ?, supplier = ? WHERE supplier_id = ?",
                          (dst_id, dst["name"], src_id)).rowcount
        st = conn.execute("UPDATE statements SET partner_id = ? WHERE partner_id = ?", (dst_id, src_id)).rowcount
        mats = conn.execute("UPDATE materials SET supplier = ?, updated_at = ? WHERE supplier = ?",
                            (dst["name"], ts, src["name"])).rowcount
        conn.execute("UPDATE partners SET updated_at = ? WHERE id = ?", (ts, dst_id))
        audit.record(conn, who, "PARTNER_MERGE", "partner", dst_id,
                     {"from": f"[{src['code']}] {src['name']}", "into": f"[{dst['code']}] {dst['name']}", "po": po,
                      "statements": st, "materials": mats})
    return PResult(True, f"{src['name']} → {dst['name']} 병합 (발주 {po} · 명세서 {st} · 자재 공급처 {mats}건 바꿈, "
                         "지난 거래는 실적에 합쳐짐)", dst_id)


def merge_candidates() -> list[dict]:
    """병합 후보: 사업자번호가 같거나, 이름(표기 무시) 한쪽이 다른 쪽을 포함하는 사용 중인 거래처 쌍."""
    df = db.query_df("SELECT id, code, name, name_key, biz_no FROM partners WHERE active = 1 ORDER BY id").to_dict("records")
    # 모든 쌍을 비교하지 않는다(거래처 3천이면 450만 쌍): 사업자번호는 묶음으로, 이름은 '한 이름의 부분 글자 = 다른 이름'을 사전에서 찾는다
    by_key: dict[str, list[dict]] = {}
    by_biz: dict[str, list[dict]] = {}
    for p in df:
        if len(p["name_key"] or "") >= 2:
            by_key.setdefault(p["name_key"], []).append(p)
        if p["biz_no"]:
            by_biz.setdefault(p["biz_no"], []).append(p)
    pairs: dict[tuple[int, int], str] = {}
    for grp in by_biz.values():
        for i, a in enumerate(grp):
            for b in grp[i + 1:]:
                pairs[(a["id"], b["id"])] = "사업자번호 같음"
    for b in df:
        k = b["name_key"] or ""
        if len(k) < 2:
            continue
        for n in range(2, len(k) + 1):                  # k 의 부분 글자 중 다른 거래처 이름과 같은 것
            for i in range(len(k) - n + 1):
                for a in by_key.get(k[i:i + n], ()):
                    if a["id"] != b["id"]:
                        pairs.setdefault((min(a["id"], b["id"]), max(a["id"], b["id"])), "이름 비슷함")
    rows = {p["id"]: p for p in df}
    out = [{"a": rows[x], "b": rows[y], "why": why} for (x, y), why in sorted(pairs.items())]
    return out[:100]


def get(partner_id: int) -> dict | None:
    df = db.query_df("SELECT * FROM partners WHERE id = ?", (partner_id,))
    return None if df.empty else df.iloc[0].to_dict()


def aliases_df(partner_id: int) -> pd.DataFrame:
    return db.query_df("SELECT id, alias, created_by, created_at FROM partner_aliases WHERE partner_id = ? ORDER BY alias",
                       (partner_id,))


def recent_tx(partner_id: int, limit: int = 50, wh_ids=None) -> pd.DataFrame:
    """이 거래처의 최근 거래 (partner_id 로 남은 것 + 마스터 전에 이 이름·다른 이름으로 적은 것)."""
    p = get(partner_id)
    if p is None:
        return pd.DataFrame()
    keys = {p["name_key"], *aliases_df(partner_id)["alias"].map(key)}
    names = [n for n in db.query_df("SELECT DISTINCT partner FROM transactions WHERE partner_id IS NULL AND partner <> ''")
             ["partner"] if key(n) in keys]
    frag, params = db.in_clause(names)
    pfrag, pparams = db.in_clause([partner_id, *merged_ids(partner_id)])
    wfrag, wp = db.in_clause(None if wh_ids is None else (list(wh_ids) or [-1]))
    return db.query_df(f"""
        SELECT t.id, t.tx_date, t.tx_type, m.code, m.name, t.qty, m.unit, t.unit_price, t.qty * t.unit_price AS amount,
               t.partner, t.partner_id, t.ref_no, w.code AS wh_code
        FROM transactions t JOIN materials m ON m.id = t.material_id LEFT JOIN warehouses w ON w.id = t.warehouse_id
        WHERE (t.partner_id{pfrag} OR (t.partner_id IS NULL AND t.partner{frag})) AND {PARTNER_TX_SQL.format(t="t")}
              {('AND t.warehouse_id' + wfrag) if wfrag else ''}
        ORDER BY t.tx_date DESC, t.id DESC LIMIT ?""", (*pparams, *params, *wp, limit))


def seed_from_data(actor: dict | None = None) -> int:
    """지금 데이터에 쓰인 거래처 이름으로 거래처 마스터를 만든다 (시연·처음 도입용). 이미 있는 이름은 건너뛴다."""
    names_df = db.query_df(f"SELECT name, kind FROM ({_name_sources()}) s")
    if names_df.empty:
        return 0
    names_df = names_df[~names_df["name"].str.startswith(("→", "←"))]
    names_df["k"] = names_df["name"].map(key)
    names_df = names_df[names_df["k"] != ""]
    made = 0
    for k, grp in names_df.groupby("k"):
        kinds = set(grp["kind"]) - {""}
        kind = "BOTH" if kinds == {"IN", "OUT"} else ("CUSTOMER" if kinds == {"OUT"} else "SUPPLIER")
        name = grp["name"].value_counts().index[0]
        if create({"name": name, "kind": kind}, actor or audit.SYSTEM).ok:
            made += 1
    return made
