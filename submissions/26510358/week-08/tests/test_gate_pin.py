"""Gate pins (MO 2026-11-05 16:57-17:01 KST). QA ran the gate with three --out dirs and got three pins of a moving
live DB (inputs sha256 f0cedf3686bf / 616e2d16957e, DBs 322,654,208 / 322,920,448 bytes): the baseline book moved
24,237,394 -> 24,689,193 with no code change. Now the pins live in --pin-dir (independent of --out), a missing pin is
an error unless --build-pin, every table of the DB pin is cut to --end, and the report records pin paths and sha256.
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from r3lib import shadow_gate, twin
from tests.test_twin import CAT, SHOP, T0, deal, make_db, market_prices, walk

WEEK = Path(__file__).resolve().parent.parent
END = float(int(T0) // 60 * 60)
# Only these fields may differ between two runs on the same pin: the wall-clock stamp of the report and the
# forum dry-run file paths (they name --out).
EXCLUDE = (("generated",), ("results", "*", "forum_dry_run", "files"))


def _strip(obj, path):
    if not path:
        return
    head, rest = path[0], path[1:]
    items = (obj if isinstance(obj, list) else []) if head == "*" else None
    if head == "*":
        for x in items:
            _strip(x, rest)
    elif isinstance(obj, dict) and head in obj:
        if rest:
            _strip(obj[head], rest)
        else:
            del obj[head]


def comparable(report):
    report = json.loads(json.dumps(report))
    for p in EXCLUDE:
        _strip(report, p)
    return report


def _fixture(tmp_path):
    visits = [deal(T0 - 1200 * k) for k in range(1, 15)] + [walk(T0 - 1500), walk(T0 - 700)]
    settles = market_prices() + [{"ts": T0 - 1200 * k, "sku": "G1", "amount": 84000, "shop": SHOP}
                                 for k in range(1, 15)]
    db = make_db(tmp_path / "d.sqlite", visits=visits, settles=settles,
                 supplies=[(T0 - 3600 * 7, "G1", 10, 600000), (T0 - 3600 * 2, "G1", 3, 180000)])
    md = tmp_path / "market"
    md.mkdir()
    (md / "catalog.json").write_text(json.dumps(CAT), encoding="utf-8")
    (md / "demand.json").write_text(json.dumps([{"category": CAT["G1"]["category"], "kind": "goods"}]), encoding="utf-8")
    (md / "inventory.json").write_text(json.dumps({"balance": 2_000_000, "units": []}), encoding="utf-8")
    day = tmp_path / "monitor" / "day"
    day.mkdir(parents=True)
    stamp = time.strftime("%Y%m%dT%H%M", time.localtime(END - 600))
    (day / f"inventory-{stamp}.json").write_text(json.dumps(
        {"balance": 2_000_000, "units": [{"sku": "G1", "cost": 60000}] * 4}), encoding="utf-8")
    (day / f"board-{stamp}.json").write_text(json.dumps({"shops": []}), encoding="utf-8")
    cand = tmp_path / "c.json"
    cand.write_text(json.dumps({"name": "c", "policy": {"CATEGORY_MAX_SHARE": 0.4}}), encoding="utf-8")
    return db, md, tmp_path / "monitor", cand


def _move_live(db, k):
    """The live DB after --end: a new sync of competitor stock, a later unmet order, more shops."""
    c = sqlite3.connect(db)
    c.execute("DELETE FROM shop_stock")
    c.execute("INSERT OR REPLACE INTO shops(shop_url, name, mine, ts) VALUES (?, 'r', 0, ?)", (f"http://r{k}", END + k))
    c.execute("INSERT INTO shop_stock VALUES (?, 'r', 'pk', 'G1', ?, ?)", (f"http://r{k}", k, END + k))
    c.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (f"unmet_o-{k}", json.dumps({"category": "x", "ts": END + k})))
    c.commit()
    c.close()


def _gate(db, md, mon, cand, out, pin, *extra, seed="0"):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONHASHSEED"}
    env.update(MARKET_DIR=str(md), PYTHONHASHSEED=seed, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-m", "r3lib.shadow_gate", "--db", str(db), "--end", repr(END), "--hours", "6",
                        "--restock", "plan", "--candidate", str(cand), "--monitor-dir", str(mon), "--out", str(out),
                        "--pin-dir", str(pin), *extra], cwd=WEEK, env=env, capture_output=True, text=True, timeout=300)
    reports = sorted(Path(out).glob("gate-*.json"))
    return r, (json.loads(reports[-1].read_text(encoding="utf-8")) if reports else None)


def test_two_gate_processes_with_different_out_dirs_on_one_pin_give_the_same_json(tmp_path):
    db, md, mon, cand = _fixture(tmp_path)
    pin = tmp_path / "pin"
    r0, rep0 = _gate(db, md, mon, cand, tmp_path / "out0", pin, "--build-pin")
    assert r0.returncode in (0, 1), r0.stderr
    assert rep0["pin"]["db"]["reused"] is False and rep0["pin"]["db"]["cut"]["shop_stock"]["status"] == "cut"
    _move_live(db, 1)                               # the live DB moves on between the runs
    r1, rep1 = _gate(db, md, mon, cand, tmp_path / "out1", pin, seed="1")
    _move_live(db, 2)
    r2, rep2 = _gate(db, md, mon, cand, tmp_path / "out2", pin, seed="2")
    assert r1.returncode == r2.returncode == r0.returncode, (r1.stderr, r2.stderr)
    assert rep1["pin"]["db"]["reused"] and rep1["pin"]["inputs"]["reused"]
    assert rep1["pin"]["db"]["sha256"] == shadow_gate.file_sha256(pin / f"memory-{END:.0f}.sqlite")
    assert rep1["results"][0]["live"]["restock_units"] > 0              # plan ran
    assert comparable(rep1) == comparable(rep2)
    assert not list(pin.glob("gate-*.json")) and not list((tmp_path / "out1").glob("memory-*"))   # --out never pins


def test_missing_pin_is_an_error_and_writes_nothing(tmp_path):
    db, md, mon, cand = _fixture(tmp_path)
    r, rep = _gate(db, md, mon, cand, tmp_path / "out", tmp_path / "pin")
    assert r.returncode == 2 and "--build-pin" in r.stderr and rep is None
    assert not (tmp_path / "pin").exists() or not list((tmp_path / "pin").iterdir())


def test_cut_to_end_cuts_every_table_and_lists_what_it_cannot(tmp_path):
    db, *_ = _fixture(tmp_path)
    c = sqlite3.connect(db)
    c.execute("UPDATE shops SET ts=? WHERE mine=1", (END + 50,))       # our own row, resynced after end
    c.execute("INSERT INTO shops(shop_url, name, mine, ts) VALUES ('http://old', 'o', 0, ?)", (END - 50,))
    c.execute("INSERT INTO shops(shop_url, name, mine, ts) VALUES ('http://new', 'n', 0, ?)", (END + 50,))
    c.execute("INSERT INTO shop_stock VALUES ('http://new', 'n', 'pk', 'G1', 4, ?)", (END + 50,))
    meta = {"unmet_o-a": {"category": "x", "ts": END - 5}, "unmet_o-b": {"category": "x", "ts": END + 5},
            "restock_adjust": {"ts": END + 5, "items": {}}, "harsh_at": END + 9, "kpi_at": END - 9,
            "report_subjects": {"a": END - 1, "b": END + 1}, "explore_spent": 1234.0, "ledger_seq": 77}
    c.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", [(k, json.dumps(v)) for k, v in meta.items()])
    c.execute("INSERT INTO purchases(order_id, ts, broken_at) VALUES ('o1', ?, ?)", (END - 100, END + 100))
    c.commit()
    c.close()
    rep = {}
    path = twin.snapshot_db(db, tmp_path, end=END, cut_report=rep)
    c = sqlite3.connect(path)
    try:
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            if any(col[1] == "ts" for col in c.execute(f'PRAGMA table_info("{t}")')):
                assert c.execute(f'SELECT COUNT(*) FROM "{t}" WHERE ts > ?', (END,)).fetchone()[0] == 0, t
        assert c.execute("SELECT shop_url FROM shops ORDER BY shop_url").fetchall() == [("http://old",), (SHOP,)]
        assert c.execute("SELECT COUNT(*) FROM shop_stock").fetchone()[0] == 0
        got = {k: json.loads(v) for k, v in c.execute("SELECT k, v FROM meta")}
        assert set(got) == {"unmet_o-a", "kpi_at", "report_subjects", "explore_spent", "ledger_seq"}
        assert got["report_subjects"] == {"a": END - 1}
        assert c.execute("SELECT broken_at FROM purchases WHERE order_id='o1'").fetchone()[0] is None
    finally:
        c.close()
    assert rep["shop_stock"]["status"] == "cut" and rep["shops"]["status"] == "partly cut"
    assert rep["meta"]["status"] == "partly cut" and set(rep["meta"]["uncut_keys"]) == {"explore_spent", "ledger_seq"}
    assert rep["meta"]["deleted_keys"] == {"harsh_at": 1, "restock_adjust": 1, "unmet_*": 1}
    assert rep["purchases"]["uncut_columns"] == ["paid", "ledger", "reason"]
    assert rep["settles"]["status"] == "cut"
    assert all(v["status"] in ("cut", "partly cut", "not cut") for v in rep.values())
