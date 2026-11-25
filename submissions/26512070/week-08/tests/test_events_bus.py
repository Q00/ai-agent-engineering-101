"""core.events and core.bus, each test in its own HARNESS_ROOT.

    py -m unittest discover -s tests      # from submissions/26512070/week-08
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core import atomic, bus, paths  # noqa: E402
from core.events import EventReader, EventWriter  # noqa: E402

GOOD_STRATEGY = {"schema_version": 1, "version": 1, "epoch": "e1", "approved": "user", "phase": "exposure",
                 "priorities": ["p"], "experiment": {}, "delegation": {},
                 "limits": {"cash_floor": 1500000, "max_units_per_sku": 5, "max_stock_share": 0.4,
                            "food_share_max": 0.25,
                            "below_cost": {"allowed": True, "only_in_phase": "exposure",
                                           "max_loss_share_of_cost": 0.1}}}


class Base(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("HARNESS_ROOT")
        os.environ["HARNESS_ROOT"] = tempfile.mkdtemp(prefix="harness-test-")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("HARNESS_ROOT", None)
        else:
            os.environ["HARNESS_ROOT"] = self.old


class EventsTest(Base):
    def test_emit_stamps_context_and_keys(self):
        w = EventWriter("seller", "seller")
        w.context(strategy_version=3, epoch="e3", card_version=1)
        ev = w.emit("deal.closed", {"price": 120}, task="t1", sku="G00001", order=None)
        self.assertEqual((ev["strategy_version"], ev["epoch"], ev["card_version"]), (3, "e3", 1))
        self.assertEqual(ev["keys"], {"task": "t1", "sku": "G00001"})       # None keys are dropped
        self.assertTrue(w.path(ev["ts"]).name.startswith("seller-"))
        line = w.path(ev["ts"]).read_text(encoding="utf-8").strip()
        self.assertEqual(json.loads(line)["event_id"], ev["event_id"])

    def test_unknown_key_is_refused(self):
        with self.assertRaises(ValueError):
            EventWriter("seller", "seller").emit("x", shop="nope")

    def test_ids_are_unique(self):
        w = EventWriter("buyer", "buyer")
        ids = {w.emit("tick")["event_id"] for _ in range(500)}
        self.assertEqual(len(ids), 500)

    def test_reader_resumes_after_commit(self):
        w = EventWriter("seller", "seller")
        for i in range(3):
            w.emit("msg", {"i": i})
        r = EventReader("outer", ["seller"])
        self.assertEqual([e["data"]["i"] for e in r.iter_new()], [0, 1, 2])
        r.commit()
        w.emit("msg", {"i": 3})
        r2 = EventReader("outer", ["seller"])                                 # a restart
        self.assertEqual([e["data"]["i"] for e in r2.iter_new()], [3])

    def test_without_commit_events_come_again(self):
        w = EventWriter("seller", "seller")
        w.emit("msg", {"i": 0})
        list(EventReader("outer", "seller").iter_new())                       # no commit: a crash
        self.assertEqual(len(list(EventReader("outer", "seller").iter_new())), 1)

    def test_unfinished_line_waits(self):
        w = EventWriter("seller", "seller")
        ev = w.emit("msg", {"i": 0})
        p = w.path(ev["ts"])
        with open(p, "ab") as f:
            f.write(b'{"half": ')                                              # a writer in the middle of a line
        r = EventReader("outer", "seller")
        self.assertEqual(len(list(r.iter_new())), 1)
        r.commit()
        with open(p, "ab") as f:
            f.write(b'1}\n')
        r2 = EventReader("outer", "seller")
        self.assertEqual(list(r2.iter_new()), [])                              # a whole line now, but not an event
        self.assertEqual(len(r2.bad), 1)

    def test_two_writers_two_sources(self):
        EventWriter("seller", "seller").emit("a")
        EventWriter("buyer", "buyer").emit("b")
        kinds = sorted(e["kind"] for e in EventReader("outer", ["seller", "buyer"]).iter_new())
        self.assertEqual(kinds, ["a", "b"])


class BusTest(Base):
    def test_status_and_staleness(self):
        st = bus.write_status("inner", kit_version="4", round=3, strategy_version=1, last_hour={"deals": 2})
        self.assertEqual(bus.read_status("inner")["round"], 3)
        self.assertFalse(bus.is_stale(st, 600))
        self.assertTrue(bus.is_stale(st, 600, now=st["ts"] + 601))
        self.assertTrue(bus.is_stale(bus.read_status("outer"), 600))         # never written

    def test_alerts_listed_in_order(self):
        a1 = bus.alert("inner", "warn", "payment_refusals", "5 refusals in 10 minutes")
        a2 = bus.alert("inner", "urgent", "cash_floor", "balance near the floor")
        self.assertEqual([a["event_id"] for a in bus.alerts()], [a1["event_id"], a2["event_id"]])
        self.assertEqual(bus.alerts(since=a2["ts"]), [])

    def test_bad_level_is_refused(self):
        with self.assertRaises(ValueError):
            bus.alert("inner", "panic", "x", "y")

    def test_last_good_copy_on_broken_file(self):
        p = paths.memory() / "strategy" / "active.json"
        atomic.write_json(p, GOOD_STRATEGY)
        obj, fresh, errors = bus.load_validated(p, "strategy", reader="inner")
        self.assertTrue(fresh)
        self.assertEqual(obj["version"], 1)
        broken = dict(GOOD_STRATEGY, version=0)                               # version must be >= 1
        atomic.write_json(p, broken)
        for _ in range(3):                                                    # read on every decision
            obj, fresh, errors = bus.load_validated(p, "strategy", reader="inner")
        self.assertFalse(fresh)
        self.assertEqual(obj["version"], 1)                                   # the last good one
        self.assertEqual(len([a for a in bus.alerts() if a["kind"] == "invalid_file"]), 1)

    def test_missing_file_gives_none_without_alert(self):
        obj, fresh, errors = bus.load_validated(paths.memory() / "facts" / "live.json", "strategy", reader="inner")
        self.assertEqual((obj, fresh), (None, False))
        self.assertEqual(bus.alerts(), [])


if __name__ == "__main__":
    unittest.main()
