"""memory/ingest.py offline: fake ledger pages, kit file fixtures, crash/rerun idempotence, attribution."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

W = Path(__file__).resolve().parents[1]
os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-ingest-"))
sys.path.insert(0, str(W))

from memory import ingest, query, store  # noqa: E402

NOW = time.time()
CATALOG = {
    "G1": {"name": "김치 1kg", "category": "식품>김치", "list_price": 10000, "kind": "food", "shelf_hours": 720},
    "G2": {"name": "의자", "category": "가구/침구>가구>의자", "list_price": 40000, "kind": "goods", "shelf_hours": None},
    "G3": {"name": "우유", "category": "식품>계란/유제품>우유/두유", "list_price": 3000, "kind": "food",
           "shelf_hours": 96},
}
DEMAND = [{"category": "식품>김치", "item": "김치", "kind": "food", "skus": 1, "median_price": 10000,
           "shelf_hours": 720},
          {"category": "가구/침구>가구>의자", "item": "의자", "kind": "goods", "skus": 1, "median_price": 40000,
           "shelf_hours": None}]
SHOP_B = "https://shop-b.example"


def row(seq, kind, data, ts=None, rnd=3):
    return {"seq": seq, "ts": ts if ts is not None else NOW - 3600 + seq, "kind": kind, "round": rnd, "data": data}


class FakeLedger:
    """GET /api/ledger?after=N semantics: at most 500 rows after N; negative N starts N rows before the newest."""

    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def __call__(self, after, timeout):
        self.calls.append(after)
        if after < 0:
            start = max(0, len(self.rows) + after)
            return self.rows[start:start + 500]
        return [r for r in self.rows if r["seq"] > after][:500]


class IngestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        h = Path(self.tmp.name)
        self.mem, self.logs, self.state = h / "memory", h / "logs", h / ".market"
        for p in (self.mem, self.logs / "decisions", self.state):
            p.mkdir(parents=True, exist_ok=True)
        (self.state / "catalog.json").write_text(json.dumps(CATALOG, ensure_ascii=False), encoding="utf-8")
        (self.state / "demand.json").write_text(json.dumps(DEMAND, ensure_ascii=False), encoding="utf-8")
        self.patches = [mock.patch.multiple(ingest, MEM=self.mem, LOGS=self.logs, MARKET=self.state,
                                            EVENTS=self.mem / "events.jsonl", CURSOR=self.mem / "cursor.json",
                                            SUMMARY=self.mem / "summary.json", LOCK=self.mem / "ingest.lock"),
                        mock.patch.object(store, "LOGS", self.logs),
                        mock.patch.object(query, "SUMMARY", self.mem / "summary.json"),
                        mock.patch.dict(os.environ, {"OWN_SHOP_URL": "https://psy.example"})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def append(self, path, rows):
        with open(path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def events(self):
        return [json.loads(x) for x in (self.mem / "events.jsonl").read_text(encoding="utf-8").splitlines()]

    def fixtures(self):
        """Our buyer paid shop B for a chair (G2) that broke; our shop sold kimchi (G1) twice to buyer-01."""
        self.append(self.state / "payments.jsonl", [
            {"order": "o-1", "shop": SHOP_B + "/", "item": "의자", "price": 30000, "paid": True, "ledger": 905},
            {"order": "o-2", "shop": SHOP_B, "item": "G2", "price": 30000, "paid": False,
             "reason": "binding: checkout sku G9 is not the agreed G2"},
            {"order": "o-3", "shop": "https://shop-c.example", "item": "G1", "price": 9000, "paid": False,
             "reason": "no certificate"}])
        self.append(self.logs / "acp-sessions.jsonl", [
            {"ts": NOW - 3000, "event": "create", "sid": "s1", "task": "t1", "sku": "G1", "unit": "u1", "price": 9000,
             "kind": "food", "grade": "A", "hours_left": 500, "cert": True},
            {"ts": NOW - 2990, "event": "complete", "sid": "s1", "task": "t1", "sku": "G1", "unit": "u1",
             "price": 9000, "ledger": 910},
            {"ts": NOW - 2000, "event": "create", "sid": "s2", "task": "t2", "sku": "G2", "unit": "u2", "price": 30000,
             "kind": "goods", "grade": "A", "cert": True},
            {"ts": NOW - 1990, "event": "cancel", "sid": "s2", "task": "t2", "sku": "G2", "unit": "u2"},
            {"ts": NOW - 1500, "event": "create", "sid": "s3", "task": "t3", "sku": "G2", "unit": "u3", "price": 30000,
             "kind": "goods", "grade": "C", "cert": True}])
        self.append(self.logs / "decisions" / "buyer.jsonl", [
            {"id": "d-buyer-1", "ts": NOW - 1000, "role": "buyer", "kind": "order", "ref": {"order": "o-10"},
             "inputs": {"order": {"order": "o-10", "item": "김치", "category": "식품>김치", "budget": 11000,
                                  "kind": "food", "use_within_hours": 12}}},
            {"id": "d-buyer-2", "ts": NOW - 900, "role": "buyer", "kind": "order", "ref": {"order": "o-11"},
             "inputs": {"item": "김치", "budget": 9000, "kind": "food", "use_within_hours": 20}},
            {"id": "d-buyer-3", "ts": NOW - 800, "role": "buyer", "kind": "policy_error", "ref": {}}])
        rows = [row(s, "order", {"order": f"o-x{s}", "buyer": "buyer-09"}) for s in range(1, 900)]
        rows += [row(900, "settle", {"order": "o-a", "buyer": "buyer-01", "item": "G1", "amount": 9500}),
                 row(901, "settle", {"order": "o-b", "buyer": "buyer-01", "item": "G1", "amount": 9400}),
                 row(905, "settle", {"order": "o-1", "buyer": "psyoung82", "item": "G2", "amount": 30000,
                                     "name": "의자", "shop": SHOP_B}),
                 row(910, "settle", {"order": "o-s1", "buyer": "buyer-01", "item": "G1", "amount": 9000}),
                 row(911, "settle", {"order": "o-s2", "buyer": "buyer-01", "item": "G1", "amount": 9000,
                                     "shop": "https://psy.example"}),
                 row(912, "settle", {"order": "o-g", "buyer": "buyer-02", "item": "G2", "amount": 30000}),
                 row(920, "broken", {"buyer": "psyoung82", "item": "G2"}),
                 row(921, "broken", {"buyer": "buyer-02", "item": "G2"}),
                 row(922, "result", {"buyer": "buyer-01", "order": "o-s2", "shop": "https://psy.example",
                                     "stars": 5, "verified": True})]
        return FakeLedger(rows)


class SyncTest(IngestCase):
    def test_first_run_backfills_only_the_newest_500(self):
        ledger = self.fixtures()
        with mock.patch.object(ingest, "fetch", ledger):
            stats = ingest.sync(time.time() + 10, state=self.state)
        newest = ledger.rows[-1]["seq"]
        self.assertEqual(ledger.calls, [-1, -500, newest])           # no walk from 0 through earlier rounds
        cur = json.loads((self.mem / "cursor.json").read_text())
        self.assertEqual(cur["round3_start_seq"], newest)
        self.assertEqual(cur["ledger_seq"], newest)
        self.assertGreater(stats["new_events"], 0)

        ids = [e["id"] for e in self.events()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("e-ledger-905", ids)                            # our purchase
        self.assertIn("e-ledger-910", ids)                            # our sale (by the kit's ledger number)
        self.assertIn("e-ledger-911", ids)                            # our sale (by shop URL)
        self.assertNotIn("e-ledger-900", ids)                         # other shops' sales: counters only
        self.assertNotIn("e-ledger-921", ids)
        self.assertIn("e-pay-o-1", ids)
        self.assertIn("e-order-o-10", ids)
        self.assertNotIn("e-order-", " ".join(i for i in ids if "policy" in i))

        s = query.load_summary()
        b = query.shop_stats(s, SHOP_B)
        self.assertEqual((b["attempts"], b["paid"], b["bind_mismatch"]), (2, 1, 1))
        self.assertEqual(b["broken_confirmed"], 1)
        self.assertEqual(query.shop_stats(s, "https://shop-c.example")["refused"], {"no certificate": 1})
        c = query.category_stats(s, "김치")
        self.assertEqual(c["uwh"], {"n": 2, "median": 16.0})
        self.assertEqual(c["budget_ratio"]["n"], 2)
        self.assertAlmostEqual(c["budget_ratio"]["p50"], 1.0)
        q = query.category_stats(s, "의자")["q"]
        self.assertEqual(q["n"], 2)                                   # two goods settles, two breakages
        self.assertGreater(q["upper"], 0.5)
        k = query.sku_stats(s, "G1")
        self.assertEqual(k["settled"], 2)
        self.assertGreater(k["sales_per_h"], 0)
        self.assertEqual(s["seller"]["customers"], {"buyer-01": 2})
        self.assertEqual(query.regular_categories(s), ["김치"])
        self.assertEqual(query.supply_demand(s)["김치"]["demand"], 6.0)   # 4 public settles + 2 of our orders
        l3 = s["seller"]["l3"]
        self.assertEqual(l3["goods_ab_no_warranty"], {"created": 1, "canceled": 1})
        self.assertEqual(l3["goods_c"], {"created": 1, "dangling": 1})
        self.assertEqual(l3["all"]["completed"], 1)

    def test_crash_before_cursor_save_then_rerun_adds_nothing_twice(self):
        ledger = self.fixtures()
        real = ingest.atomic_write_json

        def crash_on_cursor(path, obj):
            if Path(path).name == "cursor.json":
                raise KeyboardInterrupt("killed")
            return real(path, obj)

        with mock.patch.object(ingest, "fetch", ledger), \
                mock.patch.object(ingest, "atomic_write_json", crash_on_cursor):
            with self.assertRaises(KeyboardInterrupt):
                ingest.sync(time.time() + 10, state=self.state)
        first = self.events()
        self.assertFalse((self.mem / "cursor.json").exists())
        with mock.patch.object(ingest, "fetch", ledger):
            ingest.sync(time.time() + 10, state=self.state)
            ingest.sync(time.time() + 10, state=self.state)           # and once more with nothing new
        ids = [e["id"] for e in self.events()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), {e["id"] for e in first})
        s = query.load_summary()
        self.assertEqual(query.shop_stats(s, SHOP_B)["attempts"], 2)
        self.assertEqual(query.category_stats(s, "의자")["q"]["n"], 2)   # market counters not doubled

    def test_incremental_files_and_ledger(self):
        ledger = self.fixtures()
        with mock.patch.object(ingest, "fetch", ledger):
            ingest.sync(time.time() + 10, state=self.state)
            n = len(self.events())
            self.append(self.state / "payments.jsonl", [{"order": "o-4", "shop": SHOP_B, "item": "G2",
                                                         "price": 1, "paid": True, "ledger": 950}])
            ledger.rows.append(row(950, "settle", {"order": "o-4", "buyer": "psyoung82", "item": "G2",
                                                   "amount": 1, "shop": SHOP_B}))
            ledger.calls.clear()
            ingest.sync(time.time() + 10, state=self.state)
        self.assertEqual(ledger.calls, [922])
        self.assertEqual(len(self.events()), n + 2)
        self.assertEqual(query.shop_stats(query.load_summary(), SHOP_B)["paid"], 2)

    def test_review_rows_become_deal_events(self):
        """Shapes as review.py writes them: ref.order, settled facts in inputs, deal copy in memory/deals/."""
        (self.mem / "deals").mkdir()
        (self.mem / "deals" / "o-1.json").write_text(json.dumps({
            "order": {"order": "o-1"}, "receipt": {"item": "의자", "price": 30000},
            "settled": {"shop": SHOP_B + "/", "sku": "G2", "unit": "u9", "value": 40000, "ledger": 905}}))
        self.append(self.logs / "decisions" / "review.jsonl", [
            {"id": "d-review-1", "ts": NOW - 500, "kind": "review", "ref": {"order": "o-1", "ledger": 905},
             "inputs": {"price": 30000, "shop": SHOP_B, "sku": "G2"}, "choice": {"stars": 5}},
            {"id": "d-review-2", "ts": NOW - 400, "kind": "review", "ref": {"order": "o-7"},
             "inputs": {"price": 8000, "shop": SHOP_B, "sku": "G1", "value": 10000}, "choice": {"stars": 4}}])
        with mock.patch.object(ingest, "fetch", FakeLedger([])):
            ingest.sync(time.time() + 10, state=self.state)
        deals = {e["order"]: e for e in self.events() if e["kind"] == "deal"}
        self.assertEqual((deals["o-1"]["shop"], deals["o-1"]["sku"], deals["o-1"]["stars"]), (SHOP_B, "G2", 5))
        self.assertEqual((deals["o-7"]["sku"], deals["o-7"]["price"], deals["o-7"]["value"]), ("G1", 8000, 10000))

    def test_lock_held_skips(self):
        import fcntl
        with open(self.mem / "ingest.lock", "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with mock.patch.object(ingest, "fetch", side_effect=AssertionError("must not fetch")):
                self.assertEqual(ingest.sync(time.time() + 10, state=self.state), {"skipped": "locked"})

    def test_past_deadline_and_fetch_errors_still_build_the_summary(self):
        self.fixtures()
        with mock.patch.object(ingest, "fetch", side_effect=AssertionError("no time to fetch")):
            t = time.time()
            stats = ingest.sync(time.time() + 0.5, state=self.state)
            self.assertLess(time.time() - t, 2)
        self.assertEqual(stats["pages"], 0)
        self.assertTrue((self.mem / "summary.json").exists())
        with mock.patch.object(ingest, "fetch", side_effect=OSError("network down")):
            stats = ingest.sync(time.time() + 10, state=self.state)
        self.assertEqual(stats["pages"], 0)
        self.assertIn("network down", (self.logs / "hooks" / "ingest.log").read_text(encoding="utf-8"))
        self.assertIsNone(json.loads((self.mem / "cursor.json").read_text()).get("ledger_seq"))


class AttributeTest(unittest.TestCase):
    P = [{"order": "o-1", "sku": "G2", "name": "의자", "shop": "https://a", "ts": 1000.0},
         {"order": "o-2", "sku": "G2", "name": "의자", "shop": "https://b", "ts": 2000.0},
         {"order": "o-3", "sku": "G5", "name": "램프", "shop": "https://a", "ts": 2000.0}]

    def test_confirmed_inferred_none(self):
        a = ingest.attribute_broken({"sku": "G5", "ts": 5000.0}, self.P)
        self.assertEqual((a["status"], a["candidates"], a["shop"]), ("confirmed", ["o-3"], "https://a"))
        a = ingest.attribute_broken({"sku": "G2", "ts": 5000.0}, self.P)
        self.assertEqual((a["status"], sorted(a["candidates"]), a["shop"]), ("inferred", ["o-1", "o-2"], None))
        self.assertEqual(a["shops"], ["https://a", "https://b"])
        self.assertEqual(ingest.attribute_broken({"sku": "G2", "ts": 1000.0 + 8 * 3600}, self.P)["status"], "none")
        self.assertEqual(ingest.attribute_broken({"sku": "G2", "ts": 500.0}, self.P)["status"], "none")
        a = ingest.attribute_broken({"name": "램프", "ts": 3000.0}, self.P)
        self.assertEqual(a["status"], "confirmed")


if __name__ == "__main__":
    unittest.main()
