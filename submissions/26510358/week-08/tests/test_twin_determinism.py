"""Reruns of the twin and the gate on the same inputs give the same numbers (QA 2026-11-05: two gate runs with
PYTHONHASHSEED=0 and the same --end gave lever1 book -14.35M vs -14.78M and live deals 742 vs 736)."""
import json
import sys
import time
import types

import pytest

from r3lib import market, shadow_gate, twin
from tests.test_twin import CAT, SHOP, T0, deal, make_db, market_prices, walk


@pytest.fixture
def clock_plan(monkeypatch):
    """restock_r3.plan stand-in that reads the wall clock like memory.py does: the line it returns depends on
    time.time(), so an unfrozen clock changes the replay."""
    seen = []
    mod = types.ModuleType("restock_r3")

    def plan(inv, demand_rows, mem, now=None, dry_run=False, adjust=None):
        assert dry_run is True
        seen.append((now, time.time()))
        return [{"sku": "G1", "qty": 1 + int(time.time()) % 3}], None, None
    mod.plan = plan
    monkeypatch.setitem(sys.modules, "restock_r3", mod)
    monkeypatch.setattr(market, "_CATALOG", CAT, raising=False)
    return seen


def _db(tmp_path):
    visits = [deal(T0 - 1200 * k) for k in range(1, 15)] + [walk(T0 - 1500), walk(T0 - 700)]
    settles = market_prices() + [{"ts": T0 - 1200 * k, "sku": "G1", "amount": 84000, "shop": SHOP}
                                 for k in range(1, 15)]
    return make_db(tmp_path / "d.sqlite", visits=visits, settles=settles,
                   supplies=[(T0 - 3600 * 7, "G1", 10, 600000), (T0 - 3600 * 2, "G1", 3, 180000)])


def test_frozen_clock_sets_and_restores_time():
    real = time.time
    with twin.frozen_clock(123.0):
        assert time.time() == 123.0
    assert time.time is real


def test_plan_sees_the_tick_time_not_the_wall_clock(tmp_path, clock_plan):
    w = twin.load_window(_db(tmp_path), hours=6, end=T0, inventory={"balance": 1e6, "units": []}, catalog=CAT,
                         demand_rows=[])
    twin.replay(w, twin.live_policy(), seed=0, restock="plan")
    assert clock_plan and all(now == wall for now, wall in clock_plan)


def test_twin_twice_on_the_same_inputs_is_identical(tmp_path, clock_plan, monkeypatch):
    db = _db(tmp_path)
    inv = {"balance": 2_000_000, "units": [{"sku": "G1", "cost": 60000}] * 4}
    live = twin.live_policy()
    cand = twin.make_policy({"CATEGORY_MAX_SHARE": 0.4}, name="c")
    runs = []
    for wall in (1.0e9, 2.0e9):                     # a later rerun: the wall clock moved on
        monkeypatch.setattr(time, "time", lambda wall=wall: wall)
        w = twin.load_window(db, hours=6, end=T0, inventory=dict(inv), catalog=CAT, demand_rows=[])
        runs.append([twin.replay(w, p, seed=3, restock="plan", live=live, keep_visits=True) for p in (live, cand)])
    assert runs[0] == runs[1]
    assert runs[0][0]["restock_units"] > 0


def test_inventory_taken_before_end_is_moved_to_end(tmp_path, clock_plan):
    db = _db(tmp_path)
    at_end = twin.load_window(db, hours=6, end=T0, catalog=CAT, demand_rows=[],
                              inventory={"balance": 1_000_000, "units": [{"sku": "G1", "cost": 60000}] * 3})
    # just under 1 h before end we held 2 more units (our sales at T0-1200 and T0-2400 come after), cash 2 x 84000 less
    before = twin.load_window(db, hours=6, end=T0, catalog=CAT, demand_rows=[], inventory_ts=T0 - 3600 + 1,
                              inventory={"balance": 1_000_000 - 2 * 84000, "units": [{"sku": "G1", "cost": 60000}] * 5})
    assert {k: len(v) for k, v in before.start_stock.items()} == {k: len(v) for k, v in at_end.start_stock.items()}
    assert before.balance_end == at_end.balance_end
    assert any("moved" in n for n in before.notes)


def _monitor(tmp_path, end):
    d = tmp_path / "monitor" / "day"
    d.mkdir(parents=True)
    stamp = lambda ts: time.strftime("%Y%m%dT%H%M", time.localtime(ts))
    for ts, units in ((end - 1800, 9), (end - 600, 4), (end + 1200, 99)):      # the last one is after --end
        (d / f"inventory-{stamp(ts)}.json").write_text(json.dumps(
            {"balance": 3_000_000, "units": [{"sku": "G1", "cost": 60000}] * units}))
        (d / f"board-{stamp(ts)}.json").write_text(json.dumps({"tag": units, "shops": []}))
    return tmp_path / "monitor"


def test_board_and_inventory_are_pinned_to_end(tmp_path):
    end = float(int(T0) // 60 * 60)
    mon = _monitor(tmp_path, end)
    board, path = shadow_gate.board_at(mon, end)
    assert board["tag"] == 4 and shadow_gate.file_stamp(path) == end - 600
    assert shadow_gate.board_at(mon, end - 3600)[0]["tag"] == 9      # none before: the first one after
    assert shadow_gate.latest_board(mon)[0]["tag"] == 99


def test_pinned_inputs_are_written_once_and_reused(tmp_path, monkeypatch):
    end = float(int(T0) // 60 * 60)
    mon = _monitor(tmp_path, end)
    monkeypatch.setattr(market, "_CATALOG", CAT, raising=False)
    monkeypatch.setattr(market, "read_json", lambda name, default=None, md=None: [{"sku": "G1", "n": 1}])
    a = shadow_gate.pinned_inputs(end, mon, tmp_path / "out")
    assert not a["reused"] and len(a["inventory"]["units"]) == 4 and a["inventory_ts"] == end - 600
    monkeypatch.setattr(market, "read_json", lambda name, default=None, md=None: [{"sku": "G2", "n": 7}])
    b = shadow_gate.pinned_inputs(end, mon, tmp_path / "out")
    assert b["reused"] and b["demand_rows"] == a["demand_rows"] == [{"sku": "G1", "n": 1}]
    assert not b["catalog_changed"]


def test_gate_main_twice_gives_the_same_results(tmp_path, clock_plan, monkeypatch):
    db = _db(tmp_path)
    end = float(int(T0) // 60 * 60)
    mon = _monitor(tmp_path, end)
    cand = tmp_path / "c.json"
    cand.write_text(json.dumps({"name": "c", "policy": {"CATEGORY_MAX_SHARE": 0.4}}))
    live_inv = iter([{"balance": 1, "units": []}, {"balance": 9e9, "units": [{"sku": "G1", "cost": 1}] * 50}])
    monkeypatch.setattr(market, "read_json",
                        lambda name, default=None, md=None: next(live_inv, {}) if name == "inventory.json" else [])
    out = tmp_path / "out"
    reports = []
    for wall in (1.0e9, 2.0e9):
        monkeypatch.setattr(time, "time", lambda wall=wall: wall)
        shadow_gate.main(["--db", str(db), "--end", str(end), "--hours", "6", "--restock", "plan",
                          "--candidate", str(cand), "--monitor-dir", str(mon), "--out", str(out)])
        reports.append(json.loads(sorted(out.glob("gate-*.json"))[-1].read_text(encoding="utf-8")))
        for p in out.glob("gate-*.json"):
            p.unlink()                              # write_report names files by wall time; keep one at a time
    a, b = reports
    assert a["results"] == b["results"] and a["board"] == b["board"]
    assert a.get("calibration") == b.get("calibration")
