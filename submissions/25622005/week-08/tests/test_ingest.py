"""memory/ingest.py offline: fake ledger pages, kit file fixtures, crash/rerun idempotence, attribution, the
incremental summary state."""
import json
import os
import random
import subprocess
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
from tools import negotiation  # noqa: E402

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
                                     "name": "의자", "shop": SHOP_B, "unit": "u3", "grade": "C"}),
                 row(910, "settle", {"order": "o-s1", "buyer": "buyer-01", "item": "G1", "amount": 9000}),
                 row(911, "settle", {"order": "o-s2", "buyer": "buyer-01", "item": "G1", "amount": 9000,
                                     "shop": "https://psy.example"}),
                 row(912, "settle", {"order": "o-g", "buyer": "buyer-02", "item": "G2", "amount": 30000,
                                     "unit": "u9", "grade": "A"}),
                 row(915, "supply", {"shop": "psyoung82", "sku": "G1", "qty": 10, "total": 52000, "units": []}),
                 row(916, "supply", {"shop": "other-shop", "sku": "G1", "qty": 5, "total": 1, "units": []}),
                 row(919, "broken", {"buyer": "buyer-03", "item": "G2", "unit": "u-unknown"}),   # no settle seen: not joined
                 row(920, "broken", {"buyer": "psyoung82", "item": "G2", "unit": "u3"}),
                 row(921, "broken", {"buyer": "buyer-02", "item": "G2", "unit": "u9"}),
                 row(922, "result", {"buyer": "buyer-01", "order": "o-s2", "shop": "https://psy.example",
                                     "stars": 5, "verified": True})]
        return FakeLedger(rows)


class SyncTest(IngestCase):
    def test_hour_profile_and_own_hours_in_render_market(self):
        now = 1_794_013_200.0                                          # 10:00 local on this machine
        h = int(now // 3600)
        market = {"since": (h - 30) * 3600 + 600,                      # the fold began 10 min into hour h-30
                  "sku_hour": {"G1": {str(h - 30): 5, str(h - 29): 50, str(h - 6): 40, str(h - 1): 10, str(h): 3}},
                  "own_sku_hour": {"G1": {str(h - 25): 2, str(h - 1): 4}}}
        m = ingest.render_market({"market": market}, now)
        hod = lambda k: time.strftime("%H", time.localtime(k * 3600))
        self.assertEqual(m["hour_profile"][hod(h - 30)], 40.0)         # the partial first hour (5) is left out: h-6 alone
        self.assertEqual(m["hour_profile"][hod(h - 29)], 50.0)
        self.assertEqual(m["hour_profile"][hod(h - 1)], 10.0)
        self.assertEqual(m["hour_profile"][hod(h - 6)], 40.0)
        self.assertEqual(m["own_sku_hour"], {"G1": {str(h - 1): 4}})   # 24 h window
        self.assertEqual(m["sku_hour_24"], {"G1": {str(h - 6): 40, str(h - 1): 10, str(h): 3}})

    def test_breakage_joins_broken_rows_to_settled_units_by_sku_and_grade(self):
        with mock.patch.object(ingest, "fetch", self.fixtures()):
            ingest.sync(time.time() + 10, state=self.state)
        b = query.load_summary()["market"]["breakage"]
        self.assertEqual({g: (v["n"], v["k"], v["q"]) for g, v in b["grade"].items()}, {"C": (1, 1, 1.0), "A": (1, 1, 1.0)})
        self.assertEqual(b["cat"]["의자"]["C"]["n"], 1)
        self.assertEqual(b["sku"], {})                                   # under BRK_MIN_N sales per row
        s = query.load_summary()
        self.assertEqual(query.break_q(s, "G2", "C", "의자", min_n=1)["level"], "cat")   # the SKU row needs BRK_MIN_N sales
        self.assertEqual(query.break_q(s, "G9", "C", "의자", min_n=1)["level"], "cat")
        self.assertEqual(query.break_q(s, "G9", "A", "없음", min_n=1)["level"], "grade")
        self.assertIsNone(query.break_q(s, "G2", "B", "의자", min_n=1))
        self.assertIsNone(query.break_q(s, "G2", "C", "의자"))            # the default min_n
        self.assertEqual(ingest.render_breakage({"sku": {"G1": {"A": [10, 1]}}, "grade": {"A": [10, 1]}})["sku"]["G1"]["A"],
                         {"n": 10, "k": 1, "q": 0.1, "upper": ingest._wilson_upper(1, 10)})
        m = {"brk_units": {"u-old": ["G1", "x", "A", 0], "u-new": ["G1", "x", "A", 10**9]}}
        ingest.prune_market(m, 10**9)
        self.assertEqual(list(m["brk_units"]), ["u-new"])

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
        self.assertEqual(k["sold_6h"], 2)                             # restock's big-ticket probe
        self.assertEqual(s["seller"]["customers"], {"buyer-01": 2})
        self.assertEqual(query.regular_categories(s), ["김치"])
        self.assertEqual(query.supply_demand(s)["김치"]["demand"], 6.0)   # 4 public settles + 2 of our orders
        l3 = s["seller"]["l3"]
        self.assertEqual(l3["goods_ab_no_warranty"], {"created": 1, "canceled": 1})
        self.assertEqual(l3["goods_c"], {"created": 1, "dangling": 1})
        self.assertEqual(l3["all"]["completed"], 1)
        m = s["market"]                                              # market-wide, all shops, under 1 h folded
        self.assertEqual((m["sku_rate"], m["sku_settles_48h"]), ({"G1": 4.0, "G2": 2.0}, {"G1": 4, "G2": 2}))
        self.assertEqual((query.market_rate(s, "G1"), query.market_rate(s, "G3")), (4.0, 0.0))
        self.assertEqual((query.market_settles_48h(s, "G2"), query.supply_1h(s, "G1"), query.supply_1h(s, "G2")),
                         (2, 15, 0))
        self.assertEqual(m["p_clear"], {"food": None, "goods": None})    # under P_CLEAR_MIN_N settles
        self.assertEqual(m["c_kind"], {"food": 0.52, "goods": None})     # only our own supply row
        self.assertEqual((query.c_kind(s, "food"), query.c_kind(s, "goods")), (0.52, 0.61))
        self.assertIn("e-ledger-915", ids)                            # our supply row stays an event
        self.assertLess(query.market_covered_h(s), 1)

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
        self.assertEqual((query.market_rate(s, "G1"), query.supply_1h(s, "G1")), (4.0, 15))

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


    def cursor(self):
        return json.loads((self.mem / "cursor.json").read_text(encoding="utf-8"))

    def drop_state(self):
        cur = self.cursor()
        del cur["summary_state"]
        (self.mem / "cursor.json").write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")

    def test_second_sync_reads_only_new_events(self):
        ledger = self.fixtures()
        real, calls = ingest.read_new, []

        def spy(path, offset=0):
            calls.append((Path(path).name, offset))
            return real(path, offset)

        with mock.patch.object(ingest, "fetch", ledger):
            ingest.sync(time.time() + 10, state=self.state)
            first = query.load_summary()
            with mock.patch.object(ingest, "read_new", spy):
                ingest.sync(time.time() + 10, state=self.state)
        offsets = [o for name, o in calls if name == "events.jsonl"]
        self.assertEqual(len(offsets), 1)
        self.assertEqual(offsets[0], (self.mem / "events.jsonl").stat().st_size)   # the end: nothing re-read
        again = query.load_summary()
        self.assertEqual({**again, "built_at": 0}, {**first, "built_at": 0})

    def test_first_run_migrates_existing_events(self):
        ledger = self.fixtures()
        with mock.patch.object(ingest, "fetch", ledger):
            ingest.sync(time.time() + 10, state=self.state)
            self.drop_state()
            (self.mem / "summary.json").unlink()
            ingest.sync(time.time() + 10, state=self.state)
        s = query.load_summary()
        full = ingest.build_summary(self.events(), self.cursor(), CATALOG, DEMAND, 6, s["built_at"])
        self.assertEqual(s, json.loads(json.dumps(full, ensure_ascii=False)))

    def test_hook_leaves_a_big_first_fold_to_the_hand_run(self):
        ledger = self.fixtures()
        with mock.patch.object(ingest, "fetch", ledger):
            ingest.sync(time.time() + 10, state=self.state)
        self.drop_state()
        with mock.patch.object(ingest, "MIGRATE_HOOK_BYTES", 10), \
                mock.patch.object(ingest, "fetch", side_effect=AssertionError("must not fetch")):
            self.assertEqual(ingest.sync(time.time() + 10, state=self.state), {"skipped": "migration_pending"})
            self.assertNotIn("summary_state", self.cursor())
            self.assertIn("run python memory/ingest.py",
                          (self.logs / "hooks" / "ingest.log").read_text(encoding="utf-8"))
        with mock.patch.object(ingest, "MIGRATE_HOOK_BYTES", 10), mock.patch.object(ingest, "fetch", ledger):
            stats = ingest.sync(time.time() + 10, state=self.state, migrate=True)
        self.assertEqual(stats["events"], len(self.events()))
        self.assertEqual(query.shop_stats(query.load_summary(), SHOP_B)["attempts"], 2)

    def test_crash_after_append_before_cursor_save(self):
        ledger = self.fixtures()
        with mock.patch.object(ingest, "fetch", ledger):
            ingest.sync(time.time() + 10, state=self.state)
        before = self.cursor()
        self.append(self.state / "payments.jsonl", [{"order": "o-4", "shop": SHOP_B, "item": "G2",
                                                     "price": 1, "paid": True, "ledger": 950}])
        ledger.rows.append(row(950, "settle", {"order": "o-4", "buyer": "psyoung82", "item": "G2",
                                               "amount": 1, "shop": SHOP_B}))
        real = ingest.atomic_write_json

        def crash_on_cursor(path, obj):
            if Path(path).name == "cursor.json":
                raise KeyboardInterrupt("killed")
            return real(path, obj)

        with mock.patch.object(ingest, "fetch", ledger), \
                mock.patch.object(ingest, "atomic_write_json", crash_on_cursor):
            with self.assertRaises(KeyboardInterrupt):
                ingest.sync(time.time() + 10, state=self.state)
        self.assertEqual(self.cursor(), before)                       # events appended, state not saved
        ids = [e["id"] for e in self.events()]
        self.assertIn("e-pay-o-4", ids)
        with mock.patch.object(ingest, "fetch", ledger):
            ingest.sync(time.time() + 10, state=self.state)
            ingest.sync(time.time() + 10, state=self.state)
        self.assertEqual([e["id"] for e in self.events()], ids)      # the tail is folded, not appended again
        s = query.load_summary()
        self.assertEqual(s["events"], len(ids))
        b = query.shop_stats(s, SHOP_B)
        self.assertEqual((b["attempts"], b["paid"]), (3, 2))
        self.assertEqual(query.category_stats(s, "의자")["q"]["n"], 3)   # market counters not doubled

    def test_no_prune_while_ledger_backlog(self):
        ledger = self.fixtures()
        with mock.patch.object(ingest, "fetch", ledger):
            ingest.sync(time.time() + 10, state=self.state)
        self.assertGreater(self.cursor()["summary_state"]["pruned_at"], 0)   # caught up: pruned
        prune = mock.Mock()
        with mock.patch.object(ingest, "pull_ledger", return_value=([], 1, False)), \
                mock.patch.object(ingest, "prune", prune):
            ingest.sync(time.time() + 10, state=self.state)
            prune.assert_not_called()
            cur = self.cursor()
            cur["summary_state"]["pruned_at"] -= 2 * 3600                   # behind for over an hour: prune anyway
            (self.mem / "cursor.json").write_text(json.dumps(cur, ensure_ascii=False), encoding="utf-8")
            ingest.sync(time.time() + 10, state=self.state)
            prune.assert_called_once()

    def order_row(self, oid, tried):
        return {"id": f"d-buyer-{oid}", "ts": NOW - 100, "role": "buyer", "kind": "order", "ref": {"order": oid},
                "inputs": {"order": {"order": oid, "category": "식품>김치", "budget": 11000, "kind": "food"}},
                "tried": tried}

    def test_failed_tries_count_as_attempts(self):
        a, b, c = "https://a.example", "https://b.example", "https://c.example"
        self.append(self.logs / "decisions" / "buyer.jsonl", [self.order_row("o-20", [
            {"shop": a, "why": negotiation.NO_SIGNED_RECEIPT, "receipt_problem": None},
            {"shop": b + "/", "why": "deal", "receipt_problem": "the receipt withholds the unit"},
            {"shop": c, "why": "deal", "receipt_problem": None}])])
        with mock.patch.object(ingest, "fetch", FakeLedger([])):
            ingest.sync(time.time() + 10, state=self.state)
            self.drop_state()                                       # a full rebuild from events does not recount
            ingest.sync(time.time() + 10, state=self.state)
            ingest.sync(time.time() + 10, state=self.state)
        s = query.load_summary()
        self.assertEqual(query.shop_stats(s, a), {"attempts": 1, "paid": 0, "refused": {"no signed receipt": 1},
                                                  "bind_mismatch": 0, "broken_confirmed": 0, "broken_inferred": 0})
        self.assertEqual((query.shop_stats(s, b)["attempts"], query.shop_stats(s, b)["refused"]), (1, {"receipt": 1}))
        self.assertNotIn(c, s["buyer"]["shops"])                    # a deal is counted by its payments row

    def test_paid_try_is_counted_once(self):
        c = "https://c.example"
        self.append(self.logs / "decisions" / "buyer.jsonl", [self.order_row("o-21", [
            {"shop": c, "why": "deal", "receipt_problem": None}])])
        self.append(self.state / "payments.jsonl", [{"order": "o-21", "shop": c, "item": "G1", "price": 9000,
                                                     "paid": True, "ledger": 990}])
        with mock.patch.object(ingest, "fetch", FakeLedger([])):
            ingest.sync(time.time() + 10, state=self.state)
        st = query.shop_stats(query.load_summary(), c)
        self.assertEqual((st["attempts"], st["paid"]), (1, 1))

    def test_no_signed_receipt_matches_negotiation_without_importing_it(self):
        self.assertEqual(ingest.NO_SIGNED_RECEIPT, negotiation.NO_SIGNED_RECEIPT)
        code = ("import sys; from memory import ingest; "
                "print(sorted(m for m in sys.modules if m.startswith(('model', 'tools'))))")
        out = subprocess.run([sys.executable, "-c", code], cwd=W, capture_output=True, text=True, timeout=30)
        self.assertEqual(out.stdout.strip(), "[]", out.stderr)        # model.harness makes a temp dir per import


H = 3600.0
T0 = 1793700000.0


def synthetic(hours, seed=7):
    """Our events at about 180 things an hour, shaped like the live ones: sales with complete, settle and rated
    rows; refusals; buyer orders with payment, deal and purchase rows, some broken as ledger and kit rows."""
    rnd = random.Random(seed)
    shops = [f"https://s{i}.example" for i in range(8)]
    skus = [f"X{i:03d}" for i in range(60)]
    leaves = [f"c{i}" for i in range(10)]
    catalog = {s: {"category": "x>" + rnd.choice(leaves), "name": "n" + s, "kind": rnd.choice(["food", "goods"])}
               for s in skus}
    ev, seq, t, i = [], 10 ** 6, T0, 0
    while t < T0 + hours * H:
        t += rnd.expovariate(1 / 20)
        i += 1
        r = rnd.random()
        if r < 0.35:
            sid, sku = f"s{i}", rnd.choice(skus)
            seq += 1
            ev.append({"id": f"e-acp-{sid}-create", "ts": t, "kind": "acp", "ev": "create", "sid": sid, "sku": sku,
                       "goods_kind": catalog[sku]["kind"], "grade": rnd.choice("ABC"),
                       "hours_left": rnd.choice([2, 50]), "warranty_h": rnd.choice([None, 6])})
            x = rnd.random()
            if x < 0.8:
                ev.append({"id": f"e-acp-{sid}-complete", "ts": t + 5, "kind": "acp", "ev": "complete", "sid": sid,
                           "sku": sku, "price": 900, "ledger": seq})
                if rnd.random() < 0.95:
                    ev.append({"id": f"e-ledger-{seq}", "ts": t + 6, "kind": "sale", "seq": seq, "sku": sku,
                               "buyer": f"b{rnd.randrange(12)}", "amount": 1000})
                    ev.append({"id": f"e-ledger-r{seq}", "ts": t + 60, "kind": "rated", "stars": rnd.randint(1, 5)})
            elif x < 0.9:
                ev.append({"id": f"e-acp-{sid}-cancel", "ts": t + 30, "kind": "acp", "ev": "cancel", "sid": sid})
        elif r < 0.4:
            ev.append({"id": f"e-acp-x{i}-refuse", "ts": t, "kind": "acp", "ev": "refuse"})
        elif r < 0.75:
            o, leaf, sku, shop = f"o{i}", rnd.choice(leaves), rnd.choice(skus), rnd.choice(shops)
            ev.append({"id": f"e-order-{o}", "ts": t, "kind": "order_seen", "order": o, "category": "x>" + leaf,
                       "uwh": rnd.choice([12, 24.0, 48]), "budget": rnd.choice([8000, 10000, 12000]),
                       **({"budget_ratio": round(rnd.uniform(0.5, 1.5), 3)} if rnd.random() < 0.7 else {}),
                       **({"try_fail": [{"shop": shop, "why": "receipt"}]} if rnd.random() < 0.05 else {})})
            if rnd.random() < 0.5:
                paid = rnd.random() < 0.8
                ev.append({"id": f"e-pay-{o}", "ts": t + 20, "kind": "payment", "order": o, "shop": shop, "item": sku,
                           "paid": paid, "reason": None if paid else rnd.choice(["not paying: shelf: x", "binding: y"])})
                if paid:
                    seq += 1
                    ev.append({"id": f"e-deal-{o}", "ts": t + 25, "kind": "deal", "order": o, "shop": shop, "sku": sku,
                               "name": "n" + sku})
                    ev.append({"id": f"e-ledger-{seq}", "ts": t + 22, "kind": "purchase", "seq": seq, "order": o,
                               "sku": sku, "shop": shop})
                    if rnd.random() < 0.08:
                        bt = t + rnd.uniform(60, 6 * H)
                        seq += 1
                        if rnd.random() < 0.6:
                            ev.append({"id": f"e-ledger-{seq}", "ts": bt, "kind": "broken", "seq": seq, "sku": sku})
                        if rnd.random() < 0.6:
                            ev.append({"id": f"e-brokenfile-{int(bt)}-{sku}", "ts": bt + rnd.uniform(-1, 1),
                                       "kind": "broken_file", "name": "n" + sku})
        else:
            ev.append({"id": f"e-dec-{i}", "ts": t, "kind": rnd.choice(["seller_sign", "seller_reject"])})
    ev.sort(key=lambda e: e["ts"])
    return ev, catalog, [{"category": "x>" + c, "median_price": 10000} for c in leaves]


def fold_minutes(ev, catalog, demand, end, on_minute=None):
    """Fold events as they arrive, one minute at a time, pruning every minute like the hook."""
    st, med, j, clock = ingest.new_state(), ingest._medians(demand), 0, T0
    while clock < end:
        clock = min(end, clock + 60)
        while j < len(ev) and ev[j]["ts"] <= clock - 1:
            ingest.fold(st, ev[j], med, clock)
            j += 1
        ingest.prune(st, catalog, clock)
        if on_minute:
            on_minute(st, clock)
    for e in ev[j:]:                                                # breakages dated after the end
        ingest.fold(st, e, med, end)
    return st


class StateTest(unittest.TestCase):
    CURSOR = {"started_at": T0, "ledger_seq": 5,
              "market": {"cat_goods": {"c1": [5, 1]}, "buyer_cats": {"b1": {"c2": 3, "c3": 1}},
                         "cat_hour": {"c2": {str(int((T0 + 36 * H) // H) - 1): 4}}}}

    def test_incremental_matches_full_rebuild(self):
        ev, catalog, demand = synthetic(36)
        now = T0 + 36 * H + 120
        full = ingest.build_summary(ev, self.CURSOR, catalog, demand, 6, now)
        st = fold_minutes(ev, catalog, demand, now)
        self.assertTrue(st["skus"] and st["l3"] and st["attr"] and st["bshops"])   # prune froze each part

        def norm(s):
            s = json.loads(json.dumps(s))
            s["buyer"]["broken"].sort(key=lambda a: a["event"])
            return s
        self.assertLess(len(full["buyer"]["broken"]), 200)          # the [-200:] cut keeps a different tail
        self.assertEqual(norm(ingest.render(st, self.CURSOR, catalog, 6, now)), norm(full))

    def test_state_stays_bounded(self):
        ev, catalog, demand = synthetic(72, seed=3)
        size = {}

        def measure(st, clock):
            if clock - T0 in (48 * H, 72 * H):
                size[clock - T0] = len(json.dumps(st))
        fold_minutes(ev, catalog, demand, T0 + 72 * H, measure)
        self.assertLess(size[72 * H], 1.2 * size[48 * H], size)

    def test_kit_broken_rows_close_together_do_not_raise(self):
        t = NOW - 3600
        ev = [{"id": "e-pay-o-1", "ts": t, "kind": "payment", "order": "o-1", "shop": SHOP_B, "item": "G2",
               "paid": True},
              {"id": "e-pay-o-2", "ts": t, "kind": "payment", "order": "o-2", "shop": "https://shop-c.example",
               "item": "G3", "paid": True},
              {"id": "e-brokenfile-1", "ts": t + 600, "kind": "broken_file", "name": "의자"},
              {"id": "e-brokenfile-2", "ts": t + 601, "kind": "broken_file", "name": "우유"}]
        s = ingest.build_summary(ev, {}, CATALOG, DEMAND, 6, NOW)
        self.assertEqual([(a["event"], a["status"]) for a in s["buyer"]["broken"]],
                         [("e-brokenfile-1", "confirmed"), ("e-brokenfile-2", "confirmed")])
        self.assertEqual(s["buyer"]["shops"][SHOP_B]["broken_confirmed"], 1)
        self.assertEqual(s["buyer"]["shops"]["https://shop-c.example"]["broken_confirmed"], 1)

    def test_kit_and_ledger_breakage_across_prune_counted_once(self):
        t = NOW - 5 * 3600
        st = ingest.new_state()
        for e in ({"id": "e-pay-o-1", "ts": t, "kind": "payment", "order": "o-1", "shop": SHOP_B, "item": "G2",
                   "paid": True},
                  {"id": "e-ledger-920", "ts": t + 600, "kind": "broken", "seq": 920, "sku": "G2"},
                  {"id": "e-brokenfile-x", "ts": t + 600.8, "kind": "broken_file", "name": "의자"}):
            ingest.fold(st, e, {}, t + 700)
        edge = t + 600 + ingest.BROKEN_FINAL_S
        for clock in (edge + 1, edge + 3, edge + 6, edge + 60):     # kit row frozen first, then the ledger row
            ingest.prune(st, CATALOG, clock)
            s = ingest.render(st, {}, CATALOG, 6, clock)
            self.assertEqual(s["buyer"]["shops"][SHOP_B]["broken_confirmed"], 1, clock - edge)
            self.assertEqual([a["event"] for a in s["buyer"]["broken"]], ["e-ledger-920"], clock - edge)
        self.assertEqual(st["broken"], [])


class MarketTest(unittest.TestCase):
    """Per-SKU market folds in cursor.json["market"]: rates, 48 h counts, supply in the rolling hour, p_clear,
    our cost/list; pruned to their windows."""
    CAT = {"F": {"category": "a>f", "list_price": 1000, "kind": "food"},
           "G": {"category": "a>g", "list_price": 10000, "kind": "goods"}}

    def fold(self, market, rows):
        for i, (kind, ts, data) in enumerate(rows):
            ingest.ledger_rows({"seq": i, "ts": ts, "kind": kind, "data": data}, self.CAT, market, {"me"}, set())

    def test_rates_prices_and_windows(self):
        now = T0 + 100 * H + 1800                                     # half way into an hour
        market = {"since": now - 72 * H}
        rows = [("settle", now - 10 * 60, {"item": "F", "amount": 700})] * 30 \
            + [("settle", now - 5.2 * H, {"item": "F", "amount": 900})] * 30 \
            + [("settle", now - 7 * H, {"item": "F", "amount": 100})] * 50 \
            + [("settle", now - 30 * H, {"item": "G", "amount": 7400})] * 4 \
            + [("settle", now - 50 * H, {"item": "G", "amount": 1})] \
            + [("settle", now - 60, {"item": "X", "amount": 1})] \
            + [("supply", now - 50 * 60, {"shop": "other", "sku": "G", "qty": 20, "total": 1})] \
            + [("supply", now - 70 * 60, {"shop": "other", "sku": "G", "qty": 9, "total": 1})] \
            + [("supply", now - 2 * H, {"shop": "me", "sku": "F", "qty": 10, "total": 5000})] \
            + [("supply", now - 3 * H, {"shop": "me", "sku": "F", "qty": 10, "total": 6000})] \
            + [("supply", now - 26 * H, {"shop": "me", "sku": "G", "qty": 1, "total": 1})] \
            + [("supply", now - 60, {"shop": "me", "sku": "G", "qty": 0, "total": 0})]
        self.fold(market, rows)
        ingest.prune_market(market, now)
        m = ingest.render_market({"market": market}, now)
        self.assertEqual(m["sku_rate"], {"F": round(60 / 5.5, 4)})    # 5 full hours + half of this one
        self.assertEqual(m["sku_settles_48h"], {"F": 110, "G": 4})    # 50 h old pruned, X not in the catalog
        self.assertEqual(m["supply_1h"], {"G": 20})
        with mock.patch.object(ingest, "P_CLEAR_MIN_N", 60):
            self.assertEqual(ingest.render_market({"market": market}, now)["p_clear"], {"food": 0.8, "goods": None})
        self.assertEqual(m["c_kind"], {"food": 0.55, "goods": None})  # 26 h old cost pruned
        s = {"built_at": now, "market": m}
        self.assertEqual((query.market_rate(s, "G"), query.market_settles_48h(s, "X"), query.c_kind(s, "goods")),
                         (0.0, 0, 0.61))
        self.assertEqual(query.market_covered_h(s), 72.0)

    def test_price_book_by_grade_and_the_shop_grade_book(self):
        # Patch 3.6: a shop that ships A at 0.65 scores more than one that ships C at 0.5; the book tells them apart
        now = T0 + 100 * H + 1800
        market = {"since": now - 72 * H}
        a, b = "https://a.example", "https://b.example"
        rows = [("settle", now - 60, {"item": "F", "amount": 650, "shop": a, "grade": "A"})] * 3 \
            + [("settle", now - 60, {"item": "F", "amount": 600, "shop": a, "grade": "B"})] \
            + [("settle", now - 60, {"item": "F", "amount": 500, "shop": b, "grade": "C"})] * 2 \
            + [("settle", now - 60, {"item": "F", "amount": 700, "shop": b})]                # no grade: not in it
        self.fold(market, rows)
        m = ingest.render_market({"market": market}, now)
        self.assertEqual(m["price_book"][a]["grade"], {"food:A": {"n": 3, "med": 0.65}, "food:B": {"n": 1, "med": 0.6}})
        s = {"built_at": now, "market": m}
        self.assertEqual(query.shop_grade_book(s, a, "food"),
                         {"A": {"share": 0.65, "n": 3}, "B": {"share": 0.6, "n": 1}, "mix": {"A": 0.75, "B": 0.25}, "n": 4})
        self.assertIsNone(query.shop_grade_book(s, b, "food"))                  # 2 graded sales: under min_n
        self.assertEqual(query.shop_grade_book(s, b, "food", min_n=2)["mix"], {"C": 1.0})
        self.assertIsNone(query.shop_grade_book(s, a, "goods"))
        self.fold(market, [("settle", now - 60, {"item": "F", "amount": 100, "shop": a, "grade": "A"})] * 20)
        self.assertEqual(len(market["price_book"]["grade"][a]["food:A"]), ingest.PB_GRADE_KEEP)   # bounded

    def test_price_book_by_shop_sku_and_category(self):
        now = T0 + 100 * H + 1800
        market = {"since": now - 72 * H}
        a, b = "https://a.example", "https://b.example/"
        rows = [("settle", now - 60, {"item": "F", "amount": 600, "shop": a})] * 2 \
            + [("settle", now - 60, {"item": "F", "amount": 900, "shop": a})] \
            + [("settle", now - 2 * H, {"item": "G", "amount": 7000, "shop": a})] \
            + [("settle", now - 30 * H, {"item": "F", "amount": 100, "shop": b})] * 3 \
            + [("settle", now - 60, {"item": "F", "amount": 800, "shop": b})] \
            + [("settle", now - 60, {"item": "X", "amount": 1, "shop": b})] \
            + [("settle", now - 60, {"item": "F", "amount": 500})]              # no shop: not in the book
        self.fold(market, rows)
        ingest.prune_market(market, now)
        book = ingest.render_market({"market": market}, now)["price_book"]
        self.assertEqual(book[a]["sku"], {"F": {"n": 3, "med": 0.6}, "G": {"n": 1, "med": 0.7}})
        self.assertEqual(book[a]["cat"], {"f": {"n": 3, "med": 0.6}, "g": {"n": 1, "med": 0.7}})
        self.assertEqual(book["https://b.example"]["sku"], {"F": {"n": 1, "med": 0.8}})   # 30 h old pruned
        s = {"built_at": now, "market": ingest.render_market({"market": market}, now)}
        self.assertEqual(query.shop_price(s, a, "F", "f"), {"share": 0.6, "n": 3, "level": "sku"})
        self.assertEqual(query.shop_price(s, a + "/", "H", "a>f"), {"share": 0.6, "n": 3, "level": "cat"})
        self.assertIsNone(query.shop_price(s, a, "G", "g"))                   # one sale each: too few
        self.assertIsNone(query.shop_price(s, "https://c.example", "F", "f"))
        self.assertIsNone(query.shop_price({"market": {}}, a, "F", "f"))
        self.fold(market, [("settle", now - 60, {"item": "F", "amount": 100, "shop": a})] * 10)
        self.assertEqual(len(market["price_book"]["sku"][a]["F"]), ingest.PB_SKU_KEEP)      # bounded
        m = ingest.render_market({"market": market}, now)
        self.assertEqual(len(m["sku_seqs"]["F"]), ingest.SEQ_KEEP)                      # newest settle seqs
        self.assertEqual(query.sku_seqs({"market": m}, "F"), m["sku_seqs"]["F"])
        self.assertEqual(query.sku_price_book({"market": m}, "F")[a]["n"], 4)
        self.assertEqual(query.sku_seqs({}, "F"), [])

    def test_fresh_fold_divides_by_the_hours_folded(self):
        now = T0 + 100 * H + 1800
        market = {"since": now - 1800}
        self.fold(market, [("settle", now - 60, {"item": "F", "amount": 700})] * 6)
        self.assertEqual(ingest.render_market({"market": market}, now)["sku_rate"], {"F": 6.0})   # floor 1 h

    def test_odd_rows_neither_raise_nor_poison_counters(self):
        now = T0 + 100 * H + 1800
        market, cat = {"since": now - 72 * H}, dict(self.CAT, B="not a dict")
        rows = [("settle", now - 60, {"item": ["F"], "amount": 700}),          # unhashable sku
                ("settle", now - 60, {"item": "B", "amount": 700}),            # broken catalog entry
                ("settle", now - 60, {"item": "F", "amount": float("nan")}),   # counted, no paid/list
                ("settle", now - 60, {"item": "F", "amount": -5000}),          # paid/list clamped to 0
                ("supply", now - 60, {"shop": "me", "sku": {"x": 1}, "qty": 3, "total": 1}),
                ("supply", now - 60, {"shop": "me", "sku": "F", "qty": True, "total": 1}),
                ("supply", now - 60, {"shop": "me", "sku": "F", "qty": 2, "total": float("inf")})]
        for i, (kind, ts, data) in enumerate(rows):
            ingest.ledger_rows({"seq": i, "ts": ts, "kind": kind, "data": data}, cat, market, {"me"}, set())
        ingest.ledger_rows({"seq": 99, "ts": float("nan"), "kind": "settle", "data": {"item": "X"}},
                           cat, market, {"me"}, set())                   # NaN ts: no crash
        self.assertEqual(market["clear_hour"]["food"][str(int((now - 60) // H))], {"0.00": 1})
        self.assertEqual(market["sku_hour"], {"F": {str(int((now - 60) // H)): 2}})
        self.assertNotIn("own_cost", market)                          # infinite total ignored
        m = ingest.render_market({"market": market}, now)
        self.assertEqual(m["supply_1h"], {"F": 2})
        self.assertEqual(ingest.render_market({"market": dict(market, since="x")}, now)["sku_rate"], {"F": 2.0})

    def test_missing_market_data_is_none(self):
        for s in ({}, {"market": {"categories": {}}}, {"market": {"sku_rate": [1]}}):
            self.assertIsNone(query.market_rate(s, "F"))
            self.assertIsNone(query.market_settles_48h(s, "F"))
            self.assertIsNone(query.supply_1h(s, "F"))
            self.assertIsNone(query.p_clear(s, "food"))
            self.assertEqual(query.c_kind(s, "food"), 0.51)
            self.assertEqual(query.market_covered_h(s), 0.0)

    def test_market_counters_stay_bounded(self):
        rnd, market, size = random.Random(5), {"since": T0}, {}
        cat = {f"S{i}": {"category": f"a>c{i % 9}", "list_price": 1000, "kind": rnd.choice(["food", "goods"])}
               for i in range(300)}
        skus = list(cat)
        for hour in range(96):
            rows = [{"seq": 0, "ts": T0 + hour * H + rnd.uniform(0, H), "kind": rnd.choice(["settle"] * 4 + ["supply"]),
                     "data": {"item": rnd.choice(skus), "amount": rnd.randrange(500, 1500), "shop": rnd.choice(["me", "x"]),
                              "qty": rnd.randrange(1, 20), "total": rnd.randrange(5000, 9000)}} for _ in range(400)]
            for r in sorted(rows, key=lambda r: r["ts"]):
                r["data"]["sku"] = r["data"]["item"]
                ingest.ledger_rows(r, cat, market, {"me"}, set())
            ingest.prune_market(market, T0 + (hour + 1) * H)
            if hour + 1 in (72, 96):
                size[hour + 1] = len(json.dumps(market))
        self.assertLess(size[96], 1.1 * size[72], size)


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
