"""QA follow-ups on the twin and the shadow gate (2026-11-05): temp copies removed, no look-ahead in the plan
snapshot, restock levers never PASS in actual mode, one close-rate definition, revenue basis, resolved --end."""
import sqlite3
import sys
import tempfile
import types

import pytest

from r3lib import market, shadow_gate, twin
from tests.test_twin import CAT, T0, deal, make_db, market_prices, walk


@pytest.fixture
def tmpdir_only(tmp_path, monkeypatch):
    """tempfile.mkdtemp goes to a private dir, so the test sees only the copies it made."""
    d = tmp_path / "tmp"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


@pytest.fixture
def fake_plan(monkeypatch):
    """restock_r3.plan stand-in: records calls, returns no lines (no network, no live module state)."""
    calls = []
    mod = types.ModuleType("restock_r3")

    def plan(inv, demand_rows, mem, now=None, dry_run=False, adjust=None):
        assert dry_run is True
        calls.append((now, str(mem.path)))
        return [], None, None
    mod.plan = plan
    monkeypatch.setitem(sys.modules, "restock_r3", mod)
    return calls


@pytest.fixture
def w(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_CATALOG", CAT, raising=False)
    db = make_db(tmp_path / "q.sqlite", visits=[deal(T0 - 3600 * k) for k in range(1, 6)] + [walk(T0 - 900)]
                 + [dict(walk(T0 - 300), offers=[])],                       # a silent session
                 settles=market_prices() + [{"ts": T0 + 3600, "sku": "G1", "amount": 1}])   # one row after end
    inv = {"balance": 5_000_000, "units": [{"sku": "G1", "cost": 60000}] * 7}
    return twin.load_window(db, hours=6, end=T0, inventory=inv, catalog=CAT, demand_rows=[])


def leftovers(d):
    return sorted(p.name for p in d.iterdir() if p.name.startswith("twin-"))


def test_plan_replay_makes_one_copy_and_removes_it(w, tmpdir_only, fake_plan):
    r = twin.replay(w, twin.live_policy(), seed=0, restock="plan")
    assert len(fake_plan) >= 2                                # many ticks ...
    assert len({p for _, p in fake_plan}) == 1                # ... one DB copy
    assert r["visits"] == 7
    assert leftovers(tmpdir_only) == []


def test_copy_removed_on_error_and_interrupt(w, tmpdir_only, monkeypatch, fake_plan):
    for exc in (RuntimeError("boom"), KeyboardInterrupt()):
        def bad(*a, **k):
            raise exc
        monkeypatch.setattr(sys.modules["restock_r3"], "plan", bad)
        with pytest.raises(type(exc)):
            twin.replay(w, twin.live_policy(), seed=0, restock="plan")
        assert leftovers(tmpdir_only) == []


def test_gate_run_shares_one_copy_and_cleans_up(w, tmpdir_only, fake_plan):
    cands = [twin.make_policy({"restock_adjust": {"침구": 0.5}}, name="a"),
             twin.make_policy({"restock_adjust": {"침구": 0.7}}, name="b")]
    rep = shadow_gate.evaluate(w, cands, seed=0, calibrate=True)
    assert rep["plan_snapshots"] == 1
    assert len({p for _, p in fake_plan}) == 1
    assert leftovers(tmpdir_only) == []


def test_snapshot_has_no_rows_after_window_end(w, tmp_path):
    c = sqlite3.connect(w.db_path)                            # state table: latest stock, ts = last sync
    c.execute("INSERT INTO shop_stock VALUES ('https://other', 'o', 'k', 'G1', 3, ?)", (w.end + 60,))
    c.commit()
    c.close()
    (tmp_path / "snap").mkdir()
    path = twin.snapshot_db(w.db_path, tmp_path / "snap", end=w.end)
    c = sqlite3.connect(path)
    try:
        assert c.execute("SELECT COUNT(*) FROM shop_stock").fetchone()[0] == 1          # kept (documented)
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            if t not in twin.SNAPSHOT_STATE_TABLES and any(col[1] == "ts" for col in c.execute(f'PRAGMA table_info("{t}")')):
                assert c.execute(f'SELECT COUNT(*) FROM "{t}" WHERE ts > ?', (w.end,)).fetchone()[0] == 0, t
        assert c.execute("SELECT COUNT(*) FROM settles").fetchone()[0] == 5          # the later row is gone
    finally:
        c.close()
    src = twin.open_readonly(w.db_path)
    assert src.execute("SELECT COUNT(*) FROM settles WHERE ts > ?", (w.end,)).fetchone()[0] == 1   # source intact
    src.close()


def test_failed_snapshot_leaves_no_temp_dir(tmpdir_only, tmp_path):
    with pytest.raises(sqlite3.Error):
        twin.snapshot_db(tmp_path / "missing.sqlite")
    assert leftovers(tmpdir_only) == []


def test_restock_lever_is_not_applicable_in_actual_mode(w):
    cand = twin.make_policy({"restock_adjust": {"침구": 0.5}}, name="r")
    rep = shadow_gate.evaluate(w, [cand], seed=0, restock="actual", calibrate=False)
    assert rep["results"][0]["gate"]["verdict"] == "NOT_APPLICABLE"
    same = shadow_gate.evaluate(w, [twin.make_policy({"ASK_ROOM": 0.09})], restock="actual", calibrate=False)
    assert same["results"][0]["gate"]["verdict"] in ("PASS", "FAIL")


def test_one_close_rate_definition(w):
    a = w.actual
    assert a["visits"] == 7 and a["negotiations"] == 6 and a["deals"] == 5
    assert a["close_rate"] == pytest.approx(5 / 6, abs=1e-4)                 # deals / negotiations
    assert a["served_close_rate"] == pytest.approx(5 / 7, abs=1e-4)          # deals / sessions
    r = twin.replay(w, twin.live_policy(), seed=0)
    assert r["negotiations"] == r["served"] - r["silent"]
    assert r["close_rate"] == pytest.approx(r["deals"] / r["negotiations"], abs=1e-4)
    assert r["served_close_rate"] == pytest.approx(r["deals"] / r["served"], abs=1e-4)


def test_revenue_basis_and_resolved_end_in_report(w):
    rep = shadow_gate.evaluate(w, [twin.make_policy({}, name="same")], seed=0)
    cal = rep["calibration"]
    assert "deal_revenue" in cal["observed"] and "settled_revenue" in cal["observed"]
    assert cal["twin_actual"]["deal_revenue"] == rep["results"][0]["live"]["deal_revenue"]
    assert "deal revenue" in rep["revenue_basis"] and "deals / negotiations" in rep["close_rate_def"]
    assert rep["window"]["end"] == T0 and rep["window"]["end_source"] == "argument"
    assert repr(T0) in rep["window"]["reproduce"]
    text = shadow_gate.render(rep)
    assert "settle revenue" in text and "reproduce with --end" in text


def test_default_end_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(market, "_CATALOG", CAT, raising=False)
    db = make_db(tmp_path / "e.sqlite", visits=[deal(T0 - 3600), deal(T0 - 60)], settles=market_prices())
    w = twin.load_window(db, hours=6, inventory={"balance": 1, "units": []}, catalog=CAT, demand_rows=[])
    assert w.end_source == "last negotiation" and w.end == T0 - 60
