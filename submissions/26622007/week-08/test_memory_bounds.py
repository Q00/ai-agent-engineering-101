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
        with mock.patch.object(task_store_ttl.InMemoryTaskStore, "__init__",
                               lambda self, **kw: setattr(self, "_impl", object())):
            store = task_store_ttl.ExpiringTaskStore()
        self.assertFalse(store.on)
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
        self.assertTrue(cap.get("off"))
        self.assertFalse(self.step(cap, 20 + self.kit.SELLER_DRAIN_WAIT + self.kit.SELLER_CAP_RETRY, 101))
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
