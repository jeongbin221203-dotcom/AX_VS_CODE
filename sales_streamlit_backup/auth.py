"""로그인 / 권한 게이트

현재는 '간편 선택' 방식(사용자 목록에서 본인 선택)이다.
사내 표준 인증으로 교체할 때는 이 파일의 `authenticate()` 한 곳만 바꾸면 되고,
나머지 화면 코드는 손대지 않아도 된다.

  simple   : 사용자 목록에서 선택 (데모/PoC)
  password : 사번 + 비밀번호 (PBKDF2 해시 검증, verify_password 참고)
  sso      : 리버스 프록시가 넣어주는 사용자 헤더를 신뢰 (SAML/OIDC 연동)
"""
from __future__ import annotations

import hashlib
import os
from typing import Optional

import streamlit as st

import enterprise as ent
import sales_db as db

MODULE_VERSION = 2

AUTH_MODE = os.environ.get("SALES_AUTH_MODE", "simple")   # simple | password | sso
SSO_HEADER_ENV = "SALES_SSO_USER"                          # sso 모드에서 참조할 환경변수


# ---------------------------------------------------------------------------
# 비밀번호 유틸 (password 모드로 전환할 때 사용)
# ---------------------------------------------------------------------------
def hash_password(raw: str, salt: str | None = None) -> str:
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", raw.encode(), bytes.fromhex(salt), 200_000).hex()
    return f"pbkdf2$200000${salt}${digest}"


def verify_password(raw: str, stored: str | None) -> bool:
    if not stored or stored.count("$") != 3:
        return False
    _algo, rounds, salt, digest = stored.split("$")
    calc = hashlib.pbkdf2_hmac("sha256", raw.encode(), bytes.fromhex(salt), int(rounds)).hex()
    return hashlib.compare_digest(calc, digest) if hasattr(hashlib, "compare_digest") else calc == digest


def authenticate(emp_no: str = "", password: str = "") -> Optional[dict]:
    """인증 성공 시 사용자 dict, 실패 시 None."""
    if AUTH_MODE == "sso":
        name = os.environ.get(SSO_HEADER_ENV, "")
        return ent.get_user(name=name) if name else None
    user = ent.get_user(emp_no=emp_no)
    if not user or not user.get("active"):
        return None
    if AUTH_MODE == "password" and not verify_password(password, user.get("pw_hash")):
        return None
    return user


# ---------------------------------------------------------------------------
# 로그인 화면
# ---------------------------------------------------------------------------
def _first_run_setup() -> None:
    """사용자가 한 명도 없을 때의 초기 설정 화면."""
    st.info("아직 등록된 사용자가 없습니다. 초기 조직도와 계정을 만들어야 시작할 수 있습니다.")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**① 샘플 조직으로 시작 (권장)**")
        st.caption("영업본부 / 영업1·2팀과 역할별 계정 8개를 만들고, 기존 담당자 이름을 사용자로 편입합니다.")
        if st.button("샘플 조직·계정 생성", type="primary", key="setup_seed"):
            db.set_context("system", None)
            created = ent.seed_org_demo()
            st.success(f"조직 {created['orgs']}개, 사용자 {created['users']}명 생성 완료")
            st.rerun()
    with c2:
        st.markdown("**② 관리자 계정만 직접 만들기**")
        with st.form("first_admin"):
            emp_no = st.text_input("사번", value="admin")
            name = st.text_input("이름", value="관리자")
            if st.form_submit_button("관리자 생성"):
                try:
                    db.set_context("system", None)
                    ent.upsert_user({"emp_no": emp_no, "name": name, "role": "ADMIN"})
                    st.success("관리자 계정을 만들었습니다. 로그인하세요.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))


def login_gate() -> Optional[dict]:
    """로그인 상태면 사용자 dict, 아니면 로그인 화면을 그리고 None 반환."""
    if st.session_state.get("user_id"):
        # 세션에 id 만 보관하고 매번 다시 읽는다 → 권한 변경이 즉시 반영된다
        user = ent.get_user(user_id=st.session_state["user_id"])
        if user and user.get("active"):
            return user
        st.session_state.pop("user_id", None)
        st.warning("계정이 비활성화되었습니다. 관리자에게 문의하세요.")

    st.title("📈 영업관리 시스템")
    try:
        users = ent.list_users(active_only=True)
    except Exception:
        # DB 파일이 예전 버전이거나 테이블이 없는 경우: 스키마를 만들고 한 번만 재시도한다
        db.init_db()
        users = ent.list_users(active_only=True)
    if users.empty:
        _first_run_setup()
        return None

    if AUTH_MODE == "sso":
        user = authenticate()
        if user:
            st.session_state["user_id"] = user["id"]
            st.rerun()
        st.error("SSO 사용자 정보를 확인할 수 없습니다. 관리자에게 문의하세요.")
        return None

    st.caption("본인 계정을 선택해 로그인하세요. 역할에 따라 볼 수 있는 데이터와 메뉴가 달라집니다.")
    labels = {int(r.id): f"{r.이름} · {r.역할} · {r.소속 or '미배정'} ({r.사번})"
              for r in users.itertuples()}
    with st.form("login"):
        uid = st.selectbox("사용자", list(labels.keys()), format_func=lambda i: labels[i])
        pw = st.text_input("비밀번호", type="password") if AUTH_MODE == "password" else ""
        if st.form_submit_button("로그인", type="primary"):
            row = ent.get_user(user_id=uid)
            user = authenticate(row["emp_no"], pw) if row else None
            if user:
                st.session_state["user_id"] = user["id"]
                db.set_context(user["name"], ent.visible_owners(user))
                db.audit("로그인", "사용자", user["id"], {"역할": user["role"]})
                st.rerun()
            else:
                st.error("로그인에 실패했습니다. 사번 또는 비밀번호를 확인하세요.")

    with st.expander("역할별 권한 안내"):
        st.markdown("""
| 역할 | 데이터 범위 | 주요 권한 |
|---|---|---|
| 영업사원(REP) | 본인 담당 건만 | 등록·수정, 할인 결재 요청 |
| 팀장(MANAGER) | 본인 팀 + 하위 조직 | 팀 실적/예측 조회, 10% 이하 할인 결재 |
| 임원(EXEC) | 전사 | 전사 예측·분석, 20% 이하 할인 결재 |
| 관리자(ADMIN) | 전사 | 조직·사용자 관리, 감사로그, 모든 결재 |
""")
    return None


def logout() -> None:
    uid = st.session_state.pop("user_id", None)
    if uid:
        db.audit("로그아웃", "사용자", uid)
    for key in [k for k in st.session_state.keys() if k.startswith(("menu", "base_"))]:
        st.session_state.pop(key, None)


def require(user: dict, minimum: str) -> bool:
    """권한 미달이면 안내를 띄우고 False."""
    if ent.has_role(user, minimum):
        return True
    st.error(f"이 메뉴는 {db.ROLE_LABEL.get(minimum, minimum)} 이상만 사용할 수 있습니다.")
    return False
