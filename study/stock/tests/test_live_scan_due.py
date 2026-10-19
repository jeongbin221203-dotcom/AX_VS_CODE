"""장중 훑기 시각(core/live_scan.due) — 15시 이후 첫 훑기는 30분 간격과 상관없이 바로, 14:40~15:00 에는 새로 시작하지 않음."""
import json
from datetime import datetime

from core import live, live_scan


def _rep(tmp_path, monkeypatch, generated, asof):
    p = tmp_path / "signals_live.json"
    p.write_text(json.dumps({"meta": {"date": generated[:10], "generated": generated, "asof": asof}}), encoding="utf-8")
    monkeypatch.setattr(live_scan, "OUT_PATH", p)


def at(s):
    return datetime.fromisoformat(s).replace(tzinfo=live.KST)


def test_due_at_three(tmp_path, monkeypatch):
    _rep(tmp_path, monkeypatch, "2026-10-20T14:55:00", "2026-10-20T14:38:00")     # 14:38 가격으로 훑은 것이 14:55 에 끝남
    assert live_scan.due(at("2026-10-20T15:00:30"))                                # 15시가 지나면 30분 안 지났어도 바로
    _rep(tmp_path, monkeypatch, "2026-10-20T15:12:00", "2026-10-20T15:03:00")
    assert not live_scan.due(at("2026-10-20T15:20:00"))                            # 15시 이후 가격으로 이미 훑음


def test_no_new_run_just_before_three(tmp_path, monkeypatch):
    _rep(tmp_path, monkeypatch, "2026-10-20T14:05:00", "2026-10-20T13:55:00")
    assert not live_scan.due(at("2026-10-20T14:45:00"))                            # 40분 지났지만 15시 훑기를 위해 쉼
    assert live_scan.due(at("2026-10-20T14:35:00"))
