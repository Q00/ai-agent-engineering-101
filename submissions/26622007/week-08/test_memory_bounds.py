"""The seller's and restock's memory stays bounded (W-20261115-0900-AX메모리): the ledger rows share their strings, the
seller reads only the rows break_stats needs, old A2A tasks and silent talks are forgotten, and the kit restarts a seller
over its memory cap only through a drain (never a kill)."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "scripts"))

import market_model                 # noqa: E402
import task_store_ttl               # noqa: E402

CATALOG = {"G1": {"kind": "goods", "list_price": 1000}, "G2": {"kind": "goods", "list_price": 2000},
           "F1": {"kind": "food", "list_price": 500}}


def ledger():
    rows, seq = [], 0
    def add(**r):
        nonlocal seq
        seq += 1
        rows.append({"seq": seq, **r})
    t = 1_000_000.0
    for i in range(40):
        sku = ("G1", "G2", "F1")[i % 3]
        add(k="result", ts=t + i, buyer=f"b{i % 4}", order=f"o{i}", shop="https://s1", seller="Shop One")
        add(k="settle", ts=t + i, buyer=f"b{i % 4}", order=f"o{i}", sku=sku, amount=900, grade="AB"[i % 2],
            honest=True, shop="https://s1", warranty=None, unit=f"u{i}")
    add(k="broken", ts=t + 100, buyer="b0", order="o0", sku="G1")
    add(k="broken", ts=t + 101, buyer="b1", sku="G2")                  # no order: the buyer's latest G2
    add(k="supply", ts=t + 102, sku="G1")
    add(k="result", ts=t + 7 * 3600 + 30, buyer="b9", order="o99", shop="https://s2", seller=None)   # the latest row
    return rows


class LedgerRowsTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.md = self.td.name
        rows = ledger()
        with open(os.path.join(self.md, market_model.ROWS), "w", encoding="utf-8") as f:
            for r in rows + rows[:5]:                                   # a crash re-adds a run: each seq once
                f.write(json.dumps(r) + "\n")
            f.write("{not json\n")
        self.rows = rows

    def tearDown(self):
        self.td.cleanup()

    def test_rows_are_the_same_and_share_their_strings(self):
        got = market_model.load_rows(self.md)
        self.assertEqual(got, self.rows)
        settles = [r for r in got if r["k"] == "settle"]
        self.assertIs(settles[0]["shop"], settles[1]["shop"])
        self.assertIs(next(iter(got[0])), next(iter(got[1])))           # the key "k" is one string

    def test_break_rows_give_the_same_break_stats(self):
        for mature in (False, True):
            full = market_model.break_stats(market_model.load_rows(self.md), CATALOG, mature_only=mature)
            slim = market_model.break_stats(market_model.load_break_rows(self.md), CATALOG, mature_only=mature)
            self.assertEqual(slim, full)
        slim = market_model.load_break_rows(self.md)
        self.assertNotIn("result", {r["k"] for r in slim})
        self.assertEqual(slim[-1], {"k": "last", "ts": self.rows[-1]["ts"]})
        self.assertEqual(market_model.link_broken(slim), market_model.link_broken(market_model.load_rows(self.md)))

    def test_odd_lines_are_left_out(self):
        with open(os.path.join(self.md, market_model.ROWS), "a", encoding="utf-8") as f:
            f.write("[1, 2]\n7\n" + json.dumps({"seq": 9001, "ts": "late", "k": "settle", "sku": "G1"}) + "\n"
                    + json.dumps({"seq": 9002, "ts": 1.0}) + "\n")
        slim = market_model.load_break_rows(self.md)
        self.assertEqual(slim[-1], {"k": "last", "ts": self.rows[-1]["ts"]})   # a string ts is not a time
        self.assertIn(9001, {r.get("seq") for r in slim})
        self.assertNotIn(9002, {r.get("seq") for r in slim})                    # no k
        self.assertEqual(len(market_model.load_rows(self.md)), len(self.rows) + 2)

    def test_missing_file(self):
        self.assertEqual(market_model.load_break_rows(os.path.join(self.md, "none")), [])
        self.assertEqual(market_model.load_rows(os.path.join(self.md, "none")), [])


MINE = "Nocha Furniture"
STOCK_CATALOG = {k: {**v, "category": f"c/{k}"} for k, v in CATALOG.items()}


def stock_ledger():
    """ledger() and three sales of mine: each settle names that day's tunnel URL, its result row my shop's name."""
    rows = ledger()
    seq, t = rows[-1]["seq"], 1_000_000.0
    for i in range(3):
        seq += 1
        rows.append({"seq": seq, "k": "settle", "ts": t + 200 + i, "buyer": "b2", "order": f"m{i}", "sku": "G1",
                     "amount": 950, "grade": "A", "honest": True, "shop": f"https://tunnel-{i}", "warranty": 12,
                     "unit": f"mu{i}"})
        seq += 1
        rows.append({"seq": seq, "k": "result", "ts": t + 200 + i, "buyer": "b2", "order": f"m{i}",
                     "shop": f"https://tunnel-{i}", "seller": MINE})
    rows.append({"seq": seq + 1, "k": "broken", "ts": t + 300, "buyer": "b2", "order": "m0", "sku": "G1"})
    return rows


class StockRowsTests(unittest.TestCase):
    """restock.py read every row (2.4 M, RSS 2.7-2.8 GB each run on 11/15 12:03-12:04) for what only needs the settle,
    broken and supply rows and the result rows of my own shop."""
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.md = self.td.name
        self.env = mock.patch.dict(os.environ, {"SHOP_CARD_NAME": MINE})
        self.env.start()
        os.environ.pop("SHOP_PUBLIC_URL", None)
        self.write(stock_ledger())

    def tearDown(self):
        self.env.stop()
        self.td.cleanup()

    def write(self, rows):
        with open(os.path.join(self.md, market_model.ROWS), "w", encoding="utf-8") as f:
            for r in rows + rows[:5]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.write("{not json\n")

    def same(self, full, slim):
        import decision_memory
        shops = market_model.my_shops()
        for mature in (False, True):
            self.assertEqual(market_model.break_stats(slim, STOCK_CATALOG, mature_only=mature),
                             market_model.break_stats(full, STOCK_CATALOG, mature_only=mature))
        self.assertEqual(market_model.estimate(slim, STOCK_CATALOG, {"b0"}, shops, now=1_030_000.0),
                         market_model.estimate(full, STOCK_CATALOG, {"b0"}, shops, now=1_030_000.0))
        self.assertEqual(market_model.link_broken(slim), market_model.link_broken(full))
        self.assertEqual(decision_memory.my_sales(slim, shops), decision_memory.my_sales(full, shops))
        kept = lambda rows: [r for r in rows if r["k"] in ("settle", "broken", "supply")]
        self.assertEqual(kept(slim), kept(full))      # restock.pending, opening.revise, measure and the forums read these

    def test_the_stock_rows_give_the_same_model_breaks_and_sales(self):
        import decision_memory
        full, slim = market_model.load_rows(self.md), market_model.load_stock_rows(self.md)
        self.same(full, slim)
        self.assertEqual(len(decision_memory.my_sales(slim, market_model.my_shops())), 3)
        self.assertEqual({r["seller"] for r in slim if r["k"] == "result"}, {MINE})
        self.assertEqual(slim[-1], {"k": "last", "ts": max(r["ts"] for r in full)})   # an other shop's result row
        self.assertEqual(len(full) - len(slim), 41 - 1)     # 41 result rows of another shop out, the "last" row in

    def test_a_settle_without_its_shop_keeps_the_result_that_names_it(self):
        rows = stock_ledger()
        seq = rows[-1]["seq"]
        rows.append({"seq": seq + 1, "k": "result", "ts": 1_000_400.0, "buyer": "b3", "order": "n1",
                     "shop": "https://s3", "seller": "Shop Three"})     # before its settle
        for n, order in ((2, "n1"), (3, "n2")):
            rows.append({"seq": seq + n, "k": "settle", "ts": 1_000_401.0, "buyer": "b3", "order": order, "sku": "G2",
                         "amount": 1500, "grade": "B", "honest": True, "shop": None, "warranty": 12})
        rows.append({"seq": seq + 4, "k": "result", "ts": 1_000_402.0, "buyer": "b3", "order": "n2",
                     "shop": "https://s4", "seller": "Shop Four"})      # after it
        self.write(rows)
        full, slim = market_model.load_rows(self.md), market_model.load_stock_rows(self.md)
        self.same(full, slim)
        self.assertEqual({r["order"] for r in slim if r["k"] == "result"} - {"m0", "m1", "m2"}, {"n1", "n2"})

    def test_a_run_added_again_keeps_my_result_last(self):
        """Review 333282d M1: another shop's result, then mine for the same order, then the first line again (a sync run
        repeated after a crash). load_rows drops the repeat; it must not come back as the order's last result."""
        rows = stock_ledger()
        seq = rows[-1]["seq"]
        other = {"seq": seq + 1, "k": "result", "ts": 1_000_500.0, "buyer": "b2", "order": "x1", "shop": "https://s5",
                 "seller": "Shop Five"}
        rows += [other,
                 {"seq": seq + 2, "k": "settle", "ts": 1_000_501.0, "buyer": "b2", "order": "x1", "sku": "G1", "amount": 990,
                  "grade": "A", "honest": True, "shop": "https://tunnel-9", "warranty": 12},
                 {"seq": seq + 3, "k": "result", "ts": 1_000_502.0, "buyer": "b2", "order": "x1", "shop": "https://tunnel-9",
                  "seller": MINE}]
        with open(os.path.join(self.md, market_model.ROWS), "w", encoding="utf-8") as f:
            for r in rows + [other] + rows[-2:]:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        import decision_memory
        full, slim = market_model.load_rows(self.md), market_model.load_stock_rows(self.md)
        self.same(full, slim)
        self.assertIn(seq + 2, {s["seq"] for s in decision_memory.my_sales(slim, market_model.my_shops())})

    def test_the_late_pass_reads_only_the_result_rows_it_misses(self):
        rows = stock_ledger()
        seq = rows[-1]["seq"]
        rows += [{"seq": seq + 1, "k": "result", "ts": 1_000_400.0, "buyer": "b3", "order": "n1", "shop": "https://s3",
                  "seller": "Shop Three"},
                 {"seq": seq + 2, "k": "settle", "ts": 1_000_401.0, "buyer": "b3", "order": "n1", "sku": "G2",
                  "amount": 1500, "grade": "B", "honest": True, "shop": None, "warranty": 12}]
        self.write(rows)
        calls = []
        real = market_model._load_some
        def spy(md, keep, last_row=True):
            out = real(md, keep, last_row)
            calls.append([r["k"] for r in out])
            return out
        with mock.patch.object(market_model, "_load_some", side_effect=spy):
            slim = market_model.load_stock_rows(self.md)
        self.assertEqual(calls[1], ["result"])                         # the second pass holds the one row it needs
        self.assertEqual(slim[-1]["k"], "last")
        self.same(market_model.load_rows(self.md), slim)

    def test_the_agent_tools_answer_as_with_the_whole_ledger(self):
        """Review 333282d I1: the restock, forum and buyer agents' tools read the whole ledger in a child process."""
        import re
        import subprocess
        with open(os.path.join(self.md, "catalog.json"), "w", encoding="utf-8") as f:
            json.dump({k: {**v, "name": k, "kind": v["kind"]} for k, v in STOCK_CATALOG.items()}, f)
        with open(os.path.join(self.md, "inventory.json"), "w", encoding="utf-8") as f:
            json.dump({"units": []}, f)
        with open(os.path.join(self.md, "loss_state.json"), "w", encoding="utf-8") as f:
            json.dump({"mine": {"81": {}, "83": {}}}, f)
        with open(os.path.join(self.md, "forum.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "p1", "ts": 5, "refs": [1, 2, 80, 9999, {"seq": 4}]}], f)
        tools = os.path.join(HERE, "tools")
        names = sorted(n for n in os.listdir(tools) if os.path.exists(os.path.join(tools, n, "tool.py")))
        old = os.path.join(self.md, "old")
        env = {**os.environ, "MARKET_DIR": self.md, "PYTHONPATH": HERE, "PYTHONDONTWRITEBYTECODE": "1",
               "BUYER_MEMORY": os.path.join(self.md, "none.json")}
        def run(path):
            p = subprocess.run([sys.executable, path], input=json.dumps({"limit": 50}), text=True, capture_output=True,
                               env=env, cwd=HERE, timeout=60)
            return p.returncode, p.stdout
        checked = 0
        for name in names:
            with open(os.path.join(tools, name, "tool.py"), encoding="utf-8") as f:
                code = f.read()
            self.assertNotIn("load_rows(", code, name)
            if not re.search(r"load_(break|stock)_rows\(|load_rows_where\(", code):
                continue
            was = re.sub(r"load_rows_where\(md, [^\n]*\)$", "load_rows(md)", code, flags=re.M)
            was = re.sub(r"load_(break|stock)_rows\(md\)", "load_rows(md)", was)
            os.makedirs(os.path.join(old, name), exist_ok=True)
            with open(os.path.join(old, name, "tool.py"), "w", encoding="utf-8") as f:
                f.write(was)
            new_out, old_out = run(os.path.join(tools, name, "tool.py")), run(os.path.join(old, name, "tool.py"))
            self.assertEqual(new_out[0], 0, name)
            self.assertEqual(json.loads(new_out[1]), json.loads(old_out[1]), name)
            checked += 1
        self.assertEqual(checked, 9)

    def test_missing_file(self):
        self.assertEqual(market_model.load_stock_rows(os.path.join(self.md, "none")), [])

    def test_restock_reads_the_stock_rows_once_for_all_its_readers(self):
        import decision_memory, forum, restock, shop_forum
        with open(os.path.join(self.md, "inventory.json"), "w", encoding="utf-8") as f:
            json.dump({"round": market_model.ECON_ROUND, "units": []}, f)
        restock._ROWS.clear()
        try:
            with mock.patch.object(market_model, "load_rows", side_effect=AssertionError("the whole ledger")), \
                    mock.patch.object(market_model, "load_stock_rows", wraps=market_model.load_stock_rows) as load:
                rows = restock.rows_of(self.md)
                decision_memory.outcomes(self.md, {"units": []}, rows=rows)
                shop_forum.maybe_run(self.md, rows=rows)
                forum.maybe_run(self.md, decide=lambda *a, **k: None, rows=rows)
                restock.read_breaks(self.md, STOCK_CATALOG)
            self.assertEqual(load.call_count, 1)
            self.assertIs(restock.rows_of(self.md), rows)
        finally:
            restock._ROWS.clear()

    def test_a_changed_file_drops_the_old_rows_before_it_is_read_again(self):
        import restock
        restock._ROWS.clear()
        try:
            restock.rows_of(self.md)
            with open(os.path.join(self.md, market_model.ROWS), "a", encoding="utf-8") as f:
                f.write(json.dumps({"seq": 999, "k": "settle", "ts": 2_000_000.0, "sku": "G1", "shop": "x"}) + "\n")
            held = []
            real = market_model.load_stock_rows
            def load(md):
                held.append(len(restock._ROWS))            # one copy at a time: the old one is gone
                return real(md)
            with mock.patch.object(market_model, "load_stock_rows", side_effect=load):
                self.assertEqual(restock.rows_of(self.md)[-1], {"k": "last", "ts": 2_000_000.0})
            self.assertEqual(held, [0])
        finally:
            restock._ROWS.clear()


class ExpiringTaskStoreTests(unittest.TestCase):
    def test_old_tasks_go_and_new_ones_stay(self):
        from a2a.server.context import ServerCallContext
        from a2a.types.a2a_pb2 import Task, TaskState
        now = [0.0]
        store = task_store_ttl.ExpiringTaskStore(terminal_ttl=100, idle_ttl=1000, sweep_every=10, clock=lambda: now[0])
        ctx = ServerCallContext()

        def task(tid, state):
            t = Task(id=tid, context_id="c")
            t.status.state = state
            return t

        async def run():
            await store.save(task("done", TaskState.TASK_STATE_COMPLETED), ctx)
            await store.save(task("talk", TaskState.TASK_STATE_INPUT_REQUIRED), ctx)
            now[0] = 150
            await store.save(task("new", TaskState.TASK_STATE_INPUT_REQUIRED), ctx)    # sweeps
            self.assertIsNone(await store.get("done", ctx))
            self.assertIsNotNone(await store.get("talk", ctx))
            now[0] = 1100
            self.assertEqual(store.sweep(), 1)                                         # "talk", silent 1100 s
            self.assertIsNone(await store.get("talk", ctx))
            self.assertIsNotNone(await store.get("new", ctx))
            self.assertEqual(store.size(), 1)
            await store.delete("new", ctx)
            self.assertEqual((store.size(), store.saved), (0, {}))
        asyncio.run(run())

    def test_a_new_save_renews_the_task_and_owners_stay_apart(self):
        from a2a.server.context import ServerCallContext
        from a2a.types.a2a_pb2 import Task, TaskState
        now = [0.0]
        store = task_store_ttl.ExpiringTaskStore(terminal_ttl=100, idle_ttl=1000, sweep_every=10 ** 9,
                                                 clock=lambda: now[0], owner_resolver=lambda c: c.state.get("who", "a"))
        a, b = ServerCallContext(), ServerCallContext(state={"who": "b"})
        t = Task(id="t", context_id="c")
        t.status.state = TaskState.TASK_STATE_INPUT_REQUIRED

        async def run():
            await store.save(t, a)
            await store.save(t, b)
            now[0] = 900
            await store.save(t, a)                       # a's talk goes on
            now[0] = 1500
            self.assertEqual(store.sweep(), 1)          # b's only
            self.assertIsNotNone(await store.get("t", a))
            self.assertIsNone(await store.get("t", b))
        asyncio.run(run())

    def test_without_the_sdk_internals_it_stores_as_the_sdk_does(self):
        from a2a.server.context import ServerCallContext
        from a2a.types.a2a_pb2 import Task
        import contextlib
        import io
        err = io.StringIO()
        with mock.patch.object(task_store_ttl.InMemoryTaskStore, "__init__",
                               lambda self, **kw: setattr(self, "_impl", object())), contextlib.redirect_stderr(err):
            store = task_store_ttl.ExpiringTaskStore()
        self.assertFalse(store.on)
        self.assertIn("task TTL is off", err.getvalue())                  # a2a-sdk changed: the 5-6.7 GB come back
        async def run():
            with mock.patch.object(task_store_ttl.InMemoryTaskStore, "save", mock.AsyncMock()) as save:
                await store.save(Task(id="t"), ServerCallContext())
                save.assert_awaited_once()
            self.assertEqual(store.saved, {})
        asyncio.run(run())


class SweepStaleTests(unittest.TestCase):
    def test_only_silent_talks_without_their_unit_go(self):
        import seller
        from shop_stock import StockBook
        shop = seller.Seller(None, "fixture", stock=StockBook(lambda: dict(round=3, units=[])))
        old = 10_000.0
        ttl = task_store_ttl.IDLE_TTL
        shop.open = {t: {"unit": f"u-{t}", "history": []} for t in ("stale", "holds", "agreed", "late", "fresh")}
        shop.open["catalog"] = {"id": "x", "history": []}                # no unit: goes too once silent
        shop.open["untouched"] = {"unit": "u-x", "history": []}         # never seen by a turn: its clock starts now
        shop.touched = {t: old for t in shop.open if t != "untouched"}
        shop.touched["fresh"] = old + ttl - 1
        shop.touched["gone-elsewhere"] = old                             # a task not open any more
        shop.stock.held["u-holds"], shop.stock.owner["u-holds"] = old, "holds"
        shop.stock.agreed["u-agreed"] = {"task": "agreed", "at": old}
        shop.stock.late["u-late"] = {"task": "late", "at": old}
        with mock.patch.object(seller, "log"):
            self.assertEqual(shop.sweep_stale(old + ttl), 2)
        self.assertEqual(set(shop.open), {"holds", "agreed", "late", "fresh", "untouched"})
        self.assertNotIn("gone-elsewhere", shop.touched)
        self.assertIn("fresh", shop.touched)

    def test_a_drain_forgets_stale_talks_after_the_hold_and_gets_quiet(self):
        """The real Drain.census with the real sweep: stale talks made within the last hours no longer block quiet."""
        import seller
        import seller_drain
        from shop_stock import HOLD_SECONDS, StockBook
        shop = seller.Seller(None, "fixture", stock=StockBook(lambda: dict(round=3, units=[])))
        now = 50_000.0
        for i in range(30):                                              # silent 5 min to 5 h, their holds ran out
            shop.open[f"s{i}"] = {"unit": f"u{i}", "history": []}
            shop.touched[f"s{i}"] = now - HOLD_SECONDS - i * 600
        shop.open["live"] = {"unit": "u-live", "history": []}
        shop.touched["live"] = now - 10
        shop.stock.held["u-live"], shop.stock.owner["u-live"] = now - 10, "live"
        drain = seller_drain.Drain(root=tempfile.mkdtemp(), pid=1)
        c = drain.census(shop, now, refresh=False)
        self.assertEqual((c["open"], c["stale"], c["quiet"]), (1, 30, False))
        with mock.patch.object(seller, "log"):
            shop.sweep_stale(now)                                        # not draining: 6 h, nothing goes
            self.assertEqual(len(shop.open), 31)
            shop.sweep_stale(now, draining=True)
        c = drain.census(shop, now, refresh=False)
        self.assertEqual((c["open"], c["stale"]), (1, 0))
        del shop.stock.held["u-live"], shop.stock.owner["u-live"]          # the live talk lets its unit go
        with mock.patch.object(seller, "log"):
            shop.sweep_stale(now + HOLD_SECONDS, draining=True)
        c = drain.census(shop, now + HOLD_SECONDS, refresh=False)
        self.assertTrue(c["quiet"])

    def test_the_sweep_loop_runs_and_survives_a_failure(self):
        import seller
        from shop_stock import StockBook
        shop = seller.Seller(None, "fixture", stock=StockBook(lambda: dict(round=3, units=[])))
        calls = []
        def sweep(now=None, draining=False):
            calls.append(draining)
            if len(calls) == 1:
                raise RuntimeError("boom")
        async def run():
            with mock.patch.object(shop, "sweep_stale", sweep), mock.patch.object(seller, "log"), \
                    mock.patch.object(seller.DRAIN, "active", return_value=True):
                t = asyncio.create_task(shop.sweep_loop(every=0))
                with mock.patch.object(seller.seller_drain, "TICK", 0):
                    await asyncio.sleep(0.05)
                t.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await t
        with mock.patch.object(seller.seller_drain, "TICK", 0):
            asyncio.run(run())
        self.assertGreaterEqual(len(calls), 2)
        self.assertTrue(all(calls))


class ShopMemoryParseOnceTests(unittest.TestCase):
    def test_record_and_load_parse_the_file_once_while_it_is_unchanged(self):
        import shop_memory
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "m.json")
            with open(target, "w", encoding="utf-8") as f:
                json.dump({"negotiations": [{"task": "t0", "result": "deal", "sku": "G1", "price": 7}]}, f)
            real, calls = json.load, []
            def counting(f, *a, **kw):
                calls.append(1)
                return real(f, *a, **kw)
            with mock.patch.object(shop_memory, "path", return_value=target), \
                    mock.patch.object(shop_memory.json, "load", counting):
                shop_memory._DOC.clear()
                self.assertEqual(shop_memory.paid_prices("G1"), [7])
                shop_memory.record({"task": "t1", "result": "deal", "sku": "G1", "price": 9})
                shop_memory.record({"task": "t2", "result": "canceled"})
                self.assertEqual(shop_memory.paid_prices("G1"), [7, 9])
                self.assertEqual(len(calls), 1)
                with open(target, encoding="utf-8") as f:          # what is on disk is what load() gives
                    self.assertEqual(real(f), shop_memory.load())
                with open(target, "w", encoding="utf-8") as f:      # another writer: read again
                    json.dump({"negotiations": [{"task": "x", "result": "deal", "sku": "G1", "price": 1}]}, f)
                os.utime(target, ns=(1, 1))
                self.assertEqual(shop_memory.paid_prices("G1"), [1])
                self.assertEqual(len(calls), 2)

    def test_readers_beside_record_do_not_parse_again(self):
        import shop_memory
        import threading
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "m.json")
            with open(target, "w", encoding="utf-8") as f:
                json.dump({"negotiations": [{"task": f"t{i}", "result": "deal", "sku": "G1", "price": i}
                                            for i in range(2000)]}, f)
            real, calls, stop, errors = json.load, [], threading.Event(), []
            def counting(f, *a, **kw):
                calls.append(1)
                return real(f, *a, **kw)
            def read():
                try:
                    while not stop.is_set():
                        shop_memory.paid_prices("G1")
                        shop_memory.recent("x")
                except Exception as e:      # noqa: BLE001
                    errors.append(e)
            with mock.patch.object(shop_memory, "path", return_value=target), \
                    mock.patch.object(shop_memory.json, "load", counting):
                shop_memory._DOC.clear()
                readers = [threading.Thread(target=read) for _ in range(3)]
                for t in readers:
                    t.start()
                for i in range(60):
                    shop_memory.record({"task": f"n{i}", "result": "deal", "sku": "G1", "price": 10_000 + i})
                stop.set()
                for t in readers:
                    t.join()
                self.assertEqual((errors, len(calls)), ([], 1))
                with open(target, encoding="utf-8") as f:
                    on_disk = real(f)
                self.assertEqual(on_disk, shop_memory.load())
                self.assertEqual(len(on_disk["negotiations"]), 2060)

    def test_a_file_replaced_by_another_writer_is_read_again(self):
        import shop_memory
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "m.json")
            body = json.dumps({"negotiations": [{"task": "a", "result": "deal", "sku": "G1", "price": 1}]})
            with open(target, "w", encoding="utf-8") as f:
                f.write(body)
            with mock.patch.object(shop_memory, "path", return_value=target):
                shop_memory._DOC.clear()
                self.assertEqual(shop_memory.paid_prices("G1"), [1])
                st = os.stat(target)
                with open(target + ".x", "w", encoding="utf-8") as f:      # same size and mtime, a new inode
                    f.write(body.replace('"price": 1', '"price": 2'))
                os.utime(target + ".x", ns=(st.st_atime_ns, st.st_mtime_ns))
                os.replace(target + ".x", target)
                self.assertEqual(shop_memory.paid_prices("G1"), [2])

    def test_a_failed_write_reads_the_file_again(self):
        import shop_memory
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "m.json")
            with open(target, "w", encoding="utf-8") as f:
                json.dump({"negotiations": []}, f)
            with mock.patch.object(shop_memory, "path", return_value=target):
                shop_memory._DOC.clear()
                with mock.patch.object(shop_memory.os, "replace", side_effect=OSError("disk")):
                    with self.assertRaises(OSError):
                        shop_memory.record({"task": "t1", "result": "deal"})
                self.assertEqual(shop_memory.load(), {"negotiations": []})


class KitSellerCapTests(unittest.TestCase):
    def setUp(self):
        import market_kit
        self.kit = market_kit
        self.td = tempfile.TemporaryDirectory()
        self.md = Path(self.td.name)
        self.proc = mock.Mock(pid=4242)
        self.logs = []
        for name in ("SELLER_CAP_GRACE", "SELLER_CAP_SUSTAIN"):
            p = mock.patch.object(market_kit, name, 0)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.td.cleanup()

    def step(self, cap, now, held):
        with mock.patch.object(self.kit, "footprint", return_value=held):
            return self.kit.seller_cap(cap, self.proc, self.md, now, cap_bytes=100, log=self.logs.append)

    def status(self, **c):
        base = {"pid": 4242, "at": 0, "inflight": 0, "open": 0, "stale": 0, "held": 0, "agreed": 0, "late": 0}
        (self.md / "seller_drain_status.json").write_text(json.dumps({**base, **c}))

    def test_under_the_cap_nothing_happens(self):
        cap = {"since": None}
        self.assertFalse(self.step(cap, 10, 100))
        self.assertFalse(self.step(cap, 10, None))
        self.assertFalse((self.md / "seller.drain").exists())

    def test_over_the_cap_drains_then_restarts_once_quiet_and_flushed(self):
        cap = {"since": None}
        self.assertFalse(self.step(cap, 10, 101))
        self.assertEqual((self.md / "seller.drain").read_text(), "4242")
        self.status(at=9, quiet=True, flushed=True)                    # before the drain began: not this drain's
        self.assertFalse(self.step(cap, 20, 101))
        self.status(at=15, quiet=True, flushed=False)
        self.assertFalse(self.step(cap, 25, 101))
        self.status(at=28, quiet=True, flushed=True, pid=1)            # another process
        self.assertFalse(self.step(cap, 30, 101))
        self.status(at=12, quiet=True, flushed=True)                   # too old by now
        self.assertFalse(self.step(cap, 12 + self.kit.SELLER_STATUS_FRESH + 1, 101))
        self.status(at=40, quiet=True, flushed=True, late=1)           # a counter left
        self.assertFalse(self.step(cap, 41, 101))
        self.status(at=50, quiet=True, flushed=True)
        self.assertTrue(self.step(cap, 52, 101))
        self.assertIsNone(cap["since"])

    def test_no_drain_in_the_grace_or_before_the_footprint_stays_over(self):
        """11/15 09:43 and 09:54: a new seller peaked at 3.1-3.3 GB in its first minutes and fell to 1.2 GB."""
        with mock.patch.object(self.kit, "SELLER_CAP_GRACE", 3600), mock.patch.object(self.kit, "SELLER_CAP_SUSTAIN", 1800), \
                mock.patch.object(self.kit, "SELLER_CAP_GAP", 10 ** 9):    # these steps stand for checks a minute apart
            cap = {"since": None, "started": 0}
            self.assertFalse(self.step(cap, 3599, 10 ** 6))                 # in the grace
            self.assertNotIn("over", cap)
            self.step(cap, 3600, 101)                                      # over: the clock starts
            self.step(cap, 4000, 100)                                      # back under: it starts again
            self.step(cap, 4100, 101)
            self.step(cap, 4100 + 1799, 101)
            self.assertFalse((self.md / "seller.drain").exists())
            self.step(cap, 4100 + 1800, 101)
            self.assertTrue((self.md / "seller.drain").exists())

    def test_two_cap_restarts_within_six_hours_rest_the_cap_until_the_first_is_six_hours_old(self):
        import subprocess
        cap = {"since": 5}
        for now in (100, 100 + 3600):
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
            cap["marker"] = str(proc.pid)
            with mock.patch.object(self.kit, "seller_cap", return_value=True):
                self.assertTrue(self.kit.cap_restart(cap, proc, self.md, now, log=self.logs.append, wait=10))
        self.assertIn("cap rests", self.logs[-1])
        cap = self.kit.new_cap(cap, 3700)                                  # the next seller keeps the count
        self.assertEqual((cap["restarts"], cap["started"], cap["since"]), ([100, 3700], 3700, None))
        self.assertFalse(self.step(cap, 3800, 10 ** 6))                    # resting: no drain, a warning
        self.assertFalse((self.md / "seller.drain").exists())
        self.assertIn("resting", self.logs[-1])
        n = len(self.logs)
        self.step(cap, 3900, 10 ** 6)                                      # warned once per RETRY
        self.assertEqual(len(self.logs), n)
        self.step(cap, 100 + 6 * 3600 - 1, 10 ** 6)
        self.assertFalse((self.md / "seller.drain").exists())
        self.step(cap, 100 + 6 * 3600, 10 ** 6)                            # the first restart left the window
        self.assertTrue((self.md / "seller.drain").exists())

    def test_new_cap_drops_the_sellers_own_state(self):
        cap = {"since": 9, "retry_at": 9, "over": 9, "seen": True, "marker": "1", "no_status": True, "warned": 9}
        self.assertEqual(self.kit.new_cap(cap, 50), {"since": None, "started": 50})

    def test_the_over_clock_starts_again_after_a_check_skipped(self):
        with mock.patch.object(self.kit, "SELLER_CAP_SUSTAIN", 1800), mock.patch.object(self.kit, "SELLER_CAP_GAP", 10 ** 9):
            cap = {"since": None, "started": 0}
            self.step(cap, 10, 101)
            (self.md / "seller.drain").write_text("999")                # an owner's drain for a while
            self.step(cap, 1000, 101)
            (self.md / "seller.drain").unlink()
            self.step(cap, 1900, 101)
            self.assertFalse((self.md / "seller.drain").exists())
            self.step(cap, 1900 + 1800, 101)
            self.assertTrue((self.md / "seller.drain").exists())

    def test_the_over_clock_starts_again_after_a_gap_without_checks(self):
        """A watchdog lap held up by a tunnel or seller replacement (wait_card up to 120 s) measured nothing meanwhile."""
        with mock.patch.object(self.kit, "SELLER_CAP_SUSTAIN", 1800):
            cap = {"since": None, "started": 0}
            for now in range(10, 1210, 60):
                self.step(cap, now, 101)
            self.step(cap, 1150 + self.kit.SELLER_CAP_GAP + 1, 101)       # nothing measured for longer than the gap
            for now in range(1150 + self.kit.SELLER_CAP_GAP + 61, 1150 + self.kit.SELLER_CAP_GAP + 1800, 60):
                self.step(cap, now, 101)
            self.assertFalse((self.md / "seller.drain").exists())
            self.step(cap, 1150 + self.kit.SELLER_CAP_GAP + 1 + 1800, 101)
            self.assertTrue((self.md / "seller.drain").exists())

    def test_every_line_on_the_cap_resting_or_off_matches_one_watch_pattern(self):
        import re
        import subprocess
        watch = re.compile(self.kit.CAP_WATCH)
        cap = {"since": None, "no_status": True}
        self.step(cap, 10, 101)                                            # off for this seller
        cap = {"since": None, "restarts": [5, 6]}
        self.step(cap, 10, 101)                                            # resting
        cap = {"since": None}
        self.step(cap, 10, 101)
        self.status(pid=99, at=500)
        self.step(cap, 11 + self.kit.SELLER_DRAIN_WAIT, 101)              # its status never named it
        cap = {"since": 5, "restarts": [50]}
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        with mock.patch.object(self.kit, "seller_cap", return_value=True):
            self.kit.cap_restart(cap, proc, self.md, 100, log=self.logs.append, wait=10)      # rests
        hits = [line for line in self.logs if watch.search(line)]
        self.assertEqual(len(hits), 4, self.logs)
        self.assertFalse([line for line in self.logs if "cap" in line and "draining" not in line
                          and "drained" not in line and "did not drain" not in line and not watch.search(line)])

    def test_the_watchdog_starts_a_new_seller_after_a_cap_restart_with_the_count_kept(self):
        class Stop(BaseException):
            pass
        sleeps = []
        def sleep(s):
            sleeps.append(s)
            if len(sleeps) > 1:
                raise Stop
        gone = mock.Mock(pid=1, returncode=0)
        gone.poll.return_value = 0
        fresh = mock.Mock(pid=2)
        procs = [gone, None]
        def cap_restart(cap, proc, md, now, **kw):
            cap["restarts"] = [now]
            (md / "seller.drain").write_text(str(proc.pid))
            cap["marker"] = str(proc.pid)
            return True
        a = mock.Mock(tunnel=False)
        agent = {"seller": {"cmd": "python seller.py --port {port}"}}
        d = Path(self.td.name)
        (d / ".market").mkdir()
        with mock.patch.object(self.kit.time, "sleep", side_effect=sleep), \
                mock.patch.object(self.kit.time, "time", return_value=500.0), \
                mock.patch.object(self.kit, "cap_restart", side_effect=cap_restart), \
                mock.patch.object(self.kit, "start", return_value=fresh) as start, \
                mock.patch.object(self.kit, "pump") as pump, \
                mock.patch.object(self.kit, "wait_card") as wait_card, \
                mock.patch.object(self.kit, "new_cap", wraps=self.kit.new_cap) as new_cap, \
                mock.patch.object(self.kit, "log", side_effect=self.logs.append):
            with self.assertRaises(Stop):
                self.kit.watchdog(a, agent, d, None, {}, procs, None, 7000, 7001, {}, every=60)
        self.assertIs(procs[0], fresh)
        start.assert_called_once_with("python seller.py --port 7000", d)
        pump.assert_called_once_with(fresh, "[seller]")
        wait_card.assert_called_once_with("http://127.0.0.1:7000")
        self.assertEqual(new_cap.call_args.args[0]["restarts"], [500.0])  # the count goes to the new seller's cap
        self.assertFalse((d / ".market" / "seller.drain").exists())        # the kit's marker went before the start
        self.assertIn("exited (code 0)", self.logs[0])

    def test_an_owners_marker_is_left_alone(self):
        (self.md / "seller.drain").write_text("4242")                   # seller_drain_wait.py, for a deploy
        cap = {"since": None}
        self.assertFalse(self.step(cap, 10, 101))
        self.assertIsNone(cap["since"])
        self.kit.drop_marker(cap, self.md)
        self.assertTrue((self.md / "seller.drain").exists())
        self.kit.drop_marker({"marker": "4242"}, self.md)              # the kit's own goes
        self.assertFalse((self.md / "seller.drain").exists())

    def test_a_marker_of_a_gone_seller_goes_at_the_start(self):
        (self.md / "seller.drain").write_text(str(os.getpid()))
        self.kit.stale_marker(self.md)
        self.assertTrue((self.md / "seller.drain").exists())
        import subprocess
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        (self.md / "seller.drain").write_text(str(p.pid))
        self.kit.stale_marker(self.md)
        self.assertFalse((self.md / "seller.drain").exists())

    def test_a_seller_that_never_names_its_pid_turns_the_cap_off(self):
        cap = {"since": None}
        self.step(cap, 10, 101)
        self.status(pid=99, at=500)
        self.step(cap, 11 + self.kit.SELLER_DRAIN_WAIT, 101)
        self.assertTrue(cap.get("no_status"))
        self.assertFalse(self.step(cap, 20 + self.kit.SELLER_DRAIN_WAIT + self.kit.SELLER_CAP_RETRY, 101))
        self.assertIn("never named it", self.logs[-1])
        self.assertFalse((self.md / "seller.drain").exists())

    def test_cap_restart_terms_a_quiet_seller_and_never_kills(self):
        import subprocess
        quick = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        stubborn = subprocess.Popen([sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, "
                                     "signal.SIG_IGN); print('up', flush=True); time.sleep(30)"],
                                    start_new_session=True, stdout=subprocess.PIPE, text=True)
        stubborn.stdout.readline()
        try:
            for proc, wait, exited in ((quick, 10, True), (stubborn, 1, False)):
                cap = {"since": 5, "marker": str(proc.pid)}
                (self.md / "seller.drain").write_text(str(proc.pid))
                with mock.patch.object(self.kit, "seller_cap", return_value=True):
                    self.assertEqual(self.kit.cap_restart(cap, proc, self.md, 100, log=self.logs.append, wait=wait),
                                     exited)
                self.assertFalse((self.md / "seller.drain").exists())
                self.assertEqual(proc.poll() is not None, exited)
            self.assertEqual(cap["retry_at"], 100 + self.kit.SELLER_CAP_RETRY)
            self.assertIn("no kill", self.logs[-1])
        finally:
            for proc in (quick, stubborn):
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            stubborn.stdout.close()

    def test_a_drain_too_long_is_given_up_and_tried_later(self):
        cap = {"since": None}
        self.step(cap, 10, 101)
        self.status(at=500, quiet=False, flushed=False, late=2, stale=0)
        self.assertFalse(self.step(cap, 10 + self.kit.SELLER_DRAIN_WAIT + 1, 101))
        self.assertFalse((self.md / "seller.drain").exists())
        self.assertIn("late 2", self.logs[-1])
        self.assertFalse(self.step(cap, 20 + self.kit.SELLER_DRAIN_WAIT, 101))     # waits for retry_at
        self.assertFalse((self.md / "seller.drain").exists())
        self.step(cap, 11 + self.kit.SELLER_DRAIN_WAIT + self.kit.SELLER_CAP_RETRY, 101)
        self.assertTrue((self.md / "seller.drain").exists())

    def test_cap_zero_is_off(self):
        cap = {"since": None}
        with mock.patch.object(self.kit, "footprint", return_value=10 ** 12):
            self.assertFalse(self.kit.seller_cap(cap, self.proc, self.md, 10, cap_bytes=0, log=self.logs.append))
        self.assertFalse((self.md / "seller.drain").exists())

    def test_footprint_reads_this_process(self):
        held = self.kit.footprint(os.getpid())
        self.assertIsInstance(held, int)
        self.assertGreater(held, 1 << 20)

    def test_tunnel_runs_in_its_own_session(self):
        seen = {}

        class Proc:
            stdout = iter(["INF https://abc-def.trycloudflare.com\n"])
            def poll(self):
                return None

        def popen(cmd, **kw):
            seen.update(kw)
            return Proc()
        with mock.patch.object(self.kit.subprocess, "Popen", popen):
            _, url = self.kit.open_tunnel(1234)
        self.assertEqual(url, "https://abc-def.trycloudflare.com")
        if os.name != "nt":
            self.assertTrue(seen.get("start_new_session"))


if __name__ == "__main__":
    unittest.main()
