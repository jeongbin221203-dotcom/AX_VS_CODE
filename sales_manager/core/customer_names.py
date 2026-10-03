"""거래처 이름 맞추기 — 엑셀 업로드·외부에서 온 이름을 거래처 마스터로 (자재관리 partners 와 같은 방식).

  찾는 순서  ① 정식 이름 그대로 ② 다른 이름(customer_aliases) ③ 법인 표시·띄어쓰기·기호를 뺀 이름(name_key)이 한 곳만 같을 때
             ④ 사업자번호(검증번호까지 같은 10자리)
  '(주)대한산업' · '대한산업 주식회사' · '대한 산업' 은 같은 이름으로 본다 (sales_db.name_key).
  못 찾은 이름은 unknown_names 에 쌓아 두고 거래처 > 🏷️ 이름 정리에서 기존 거래처의 다른 이름으로 연결하거나 새로 등록한다.
  거래처 이름을 바꾸거나 병합하면 예전 이름은 자동으로 다른 이름이 된다 → 예전 이름으로 된 엑셀도 그대로 들어온다.
"""
from __future__ import annotations

import difflib
from typing import Any, Optional

from . import sales_db as db


class Index:
    """한 번의 업로드 동안 쓰는 이름 색인 (거래처 1만 곳도 수 ms)."""

    def __init__(self, db_path: str | None = None):
        rows = db._df("SELECT id, name, biz_no_norm, owner_id FROM customers WHERE merged_into IS NULL", (), db_path)
        scope = db.current_scope()                   # 추천(비슷한 이름 안내)은 볼 수 있는 거래처만 — 다른 팀 거래처 이름이 새지 않게
        self.visible = None if scope is None else {int(r.id) for r in rows.itertuples()
                                                   if r.owner_id is not None and int(r.owner_id) in set(scope)}
        self.by_name: dict[str, int] = {}
        self.by_key: dict[str, list[int]] = {}
        self.by_biz: dict[str, int] = {}
        self.names: dict[int, str] = {}
        for r in rows.itertuples():
            cid = int(r.id)
            self.names[cid] = r.name
            self.by_name.setdefault(str(r.name).strip(), cid)
            self.by_key.setdefault(db.name_key(r.name), []).append(cid)
            if r.biz_no_norm:
                self.by_biz.setdefault(str(r.biz_no_norm), cid)
        self.grams: dict[str, set[str]] = {}         # 2글자 묶음 → 이름 키 (비슷한 이름 후보를 빨리 좁힘)
        for k in self.by_key:
            for g in {k[i:i + 2] for i in range(max(len(k) - 1, 1))}:
                self.grams.setdefault(g, set()).add(k)
        aliases = db._df("SELECT a.alias_key, a.customer_id FROM customer_aliases a "
                         "JOIN customers c ON c.id = a.customer_id WHERE c.merged_into IS NULL", (), db_path)
        self.by_alias = {r.alias_key: int(r.customer_id) for r in aliases.itertuples()}

    def resolve(self, name: str, biz_no: Any = None) -> tuple[Optional[int], str]:
        """(거래처 id, 찾은 방법) — 못 찾으면 (None, '')."""
        name = str(name or "").strip()
        if name in self.by_name:
            return self.by_name[name], "이름"
        key = db.name_key(name)
        if key and key in self.by_alias:
            return self.by_alias[key], "다른 이름"
        ids = self.by_key.get(key, []) if key else []
        if len(ids) == 1:
            return ids[0], "비슷한 이름"
        digits = db.biz_digits(biz_no)
        if len(digits) == 10 and digits in self.by_biz:
            return self.by_biz[digits], "사업자번호"
        return None, ""

    def suggest(self, name: str, limit: int = 3) -> list[tuple[int, str]]:
        """비슷한 거래처 후보 (이름 정리·오류 안내용)."""
        key = db.name_key(name)
        if not key:
            return []
        candidates: set[str] = set()
        for g in {key[i:i + 2] for i in range(max(len(key) - 1, 1))}:
            candidates |= self.grams.get(g, set())
        scored = []
        for k in candidates:
            ids = [cid for cid in self.by_key[k] if self.visible is None or cid in self.visible]
            if not k or not ids:
                continue
            ratio = 0.95 if (key in k or k in key) else difflib.SequenceMatcher(None, key, k).ratio()
            if ratio >= 0.6:
                scored.extend((ratio, cid) for cid in ids)
        scored.sort(key=lambda t: (-t[0], self.names[t[1]]))
        return [(cid, self.names[cid]) for _r, cid in scored[:limit]]


def record_unknown(name: str, source: str, db_path: str | None = None) -> None:
    """맞추지 못한 이름을 쌓는다 (같은 이름·출처는 횟수만 늘림)."""
    name = str(name or "").strip()[:200]
    key = db.name_key(name)
    if not key:
        return
    now = db._now()
    with db.get_conn(db_path) as conn:
        if conn.execute("UPDATE unknown_names SET seen = seen + 1, last_seen = ?, name = ? "
                        "WHERE name_key = ? AND source = ? AND resolved_at IS NULL", (now, name, key, source)).rowcount == 0:
            if not conn.execute("SELECT 1 FROM unknown_names WHERE name_key = ? AND source = ?", (key, source)).fetchone():
                conn.execute("INSERT INTO unknown_names (name, name_key, source, seen, first_seen, last_seen) "
                             "VALUES (?, ?, ?, 1, ?, ?)", (name, key, source, now, now))
            else:                                    # 정리했던 이름이 다시 나오면 다시 정리 대상으로
                conn.execute("UPDATE unknown_names SET seen = seen + 1, last_seen = ?, resolved_at = NULL, "
                             "resolved_customer_id = NULL WHERE name_key = ? AND source = ?", (now, key, source))


def list_unknown(limit: int = 300) -> list[dict]:
    rows = db._df("SELECT id, name, source, seen, first_seen, last_seen FROM unknown_names WHERE resolved_at IS NULL "
                  "ORDER BY seen DESC, last_seen DESC LIMIT ?", [int(limit)]).to_dict("records")
    if rows:
        idx = Index()
        for r in rows:
            r["suggest"] = idx.suggest(r["name"])
    return rows


def aliases_of(customer_id: int) -> list[dict]:
    return db._df("SELECT id, alias, created_by, created_at FROM customer_aliases WHERE customer_id = ? ORDER BY id",
                  [int(customer_id)]).to_dict("records")


def add_alias(customer_id: int, alias: str, actor: str = "", db_path: str | None = None, quiet: bool = False) -> bool:
    """다른 이름 등록. 다른 거래처의 정식 이름·다른 이름과 겹치면 거부 (quiet=True 면 조용히 건너뜀). 등록했으면 True."""
    alias = str(alias or "").strip()[:200]
    key = db.name_key(alias)
    cust = db._one("SELECT id, name FROM customers WHERE id = ?", [int(customer_id)], db_path)
    if not cust or not key:
        if quiet:
            return False
        raise ValueError("거래처나 이름이 올바르지 않습니다.")
    if key == db.name_key(cust["name"]):
        return False                                 # 정식 이름과 같은 표기 — 따로 둘 필요 없음
    other = db._one("SELECT a.customer_id, COALESCE(c.name, '(삭제된 거래처)') AS name FROM customer_aliases a "
                    "LEFT JOIN customers c ON c.id = a.customer_id WHERE a.alias_key = ?", [key], db_path)
    if other and not db._one("SELECT 1 FROM customers WHERE id = ?", [int(other["customer_id"])], db_path):
        with db.get_conn(db_path) as conn:          # 지워진 거래처에 남은 다른 이름은 정리하고 새로 쓴다
            conn.execute("DELETE FROM customer_aliases WHERE alias_key = ?", (key,))
        other = None
    clash = [r for r in db._df("SELECT id, name FROM customers WHERE merged_into IS NULL AND id <> ?",
                               [int(customer_id)], db_path).itertuples() if db.name_key(r.name) == key]
    if other and int(other["customer_id"]) == int(customer_id):
        return False
    if other or clash:
        if quiet:
            return False
        who = other["name"] if other else clash[0].name
        raise ValueError(f"'{alias}' 은(는) 이미 '{who}' 의 이름입니다. 같은 회사면 거래처 병합을 쓰세요.")
    with db.get_conn(db_path) as conn:
        conn.execute("INSERT INTO customer_aliases (customer_id, alias, alias_key, created_by, created_at) VALUES (?,?,?,?,?)",
                     (int(customer_id), alias, key, actor or db.current_actor(), db._now()))
        conn.execute("UPDATE unknown_names SET resolved_customer_id = ?, resolved_at = ? WHERE name_key = ? "
                     "AND resolved_at IS NULL", (int(customer_id), db._now(), key))
    db.audit("다른이름추가", "거래처", int(customer_id), {"거래처": cust["name"], "다른 이름": alias}, db_path)
    return True


def remove_alias(alias_id: int) -> None:
    row = db._one("SELECT customer_id, alias FROM customer_aliases WHERE id = ?", [int(alias_id)])
    if not row:
        return
    with db.get_conn() as conn:
        conn.execute("DELETE FROM customer_aliases WHERE id = ?", (int(alias_id),))
    db.audit("다른이름삭제", "거래처", int(row["customer_id"]), {"다른 이름": row["alias"]})


def dismiss_unknown(unknown_id: int) -> None:
    """정리 목록에서 빼기 (잘못 적은 이름 등 — 거래처에 연결하지 않음)."""
    with db.get_conn() as conn:
        conn.execute("UPDATE unknown_names SET resolved_at = ? WHERE id = ?", (db._now(), int(unknown_id)))


def link_unknown(unknown_id: int, customer_id: int, actor: str = "") -> None:
    row = db._one("SELECT name FROM unknown_names WHERE id = ?", [int(unknown_id)])
    if not row:
        raise ValueError("정리할 이름을 찾을 수 없습니다.")
    add_alias(customer_id, row["name"], actor)
    with db.get_conn() as conn:                     # 정식 이름과 표기만 다른 경우(add_alias 가 건너뜀)도 정리 완료로
        conn.execute("UPDATE unknown_names SET resolved_customer_id = ?, resolved_at = COALESCE(resolved_at, ?) "
                     "WHERE id = ?", (int(customer_id), db._now(), int(unknown_id)))
