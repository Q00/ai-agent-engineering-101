"""strategist/collect.py offline: a fake market over httpx.MockTransport, ledger paging, failures, 24 h cache,
and the shop's own files on a temp directory."""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-collect-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402

from strategist import collect  # noqa: E402

NOW = 1_800_000_000.0


class FakeMarket:
    """Serves small synthetic responses; ledger rows have seq 1..n_ledger. fail: {path: status or exception}."""

    def __init__(self, n_ledger=1200, fail=None):
        self.n, self.fail, self.calls = n_ledger, fail or {}, []
        self.forum = [{"id": 1, "author": "a", "text": "hi", "likes": 2, "dislikes": 0, "ts": NOW, "refs": [1, 2]}]

    def ledger(self, after):
        lo = self.n - min(-after, 500) + 1 if after < 0 else after + 1
        hi = self.n if after < 0 else min(self.n, after + 500)
        return [{"seq": s, "ts": NOW - (self.n - s), "kind": "order", "round": 3, "data": {}}
                for s in range(max(1, lo), hi + 1)]

    def handler(self, request):
        path = request.url.path
        self.calls.append((path, dict(request.url.params)))
        f = self.fail.get(path)
        if isinstance(f, Exception):
            raise f
        if callable(f) and f(request):
            return httpx.Response(500)
        if isinstance(f, int):
            return httpx.Response(f)
        body = {
            "/api/board": {"round": 3, "shops": [], "buyers": [], "players": [], "reviews": [], "totals": {}},
            "/api/stats": {"round": 3, "rounds": {"2": {"deals": 9}, "3": {"deals": 1, "results": 2}}},
            "/api/trades": [{"id": 1, "ts": NOW, "shop": "s", "item": "G1", "price": 10}],
            "/api/forum": self.forum,
            "/api/shop-reviews": [{"author": "b", "stars": 5, "review": "ok", "ts": NOW}],
            "/api/demand": [{"category": "식품>김치", "item": "김치", "kind": "food", "skus": 3, "median_price": 9000}],
            "/api/catalog": [{"sku": "G1", "name": "김치", "category": "식품>김치", "list_price": 10000, "kind": "food",
                              "shelf_hours": 720}, {"name": "no sku"}],
        }.get(path)
        if path == "/api/ledger":
            body = self.ledger(int(request.url.params["after"]))
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404)

    def client(self):
        return httpx.Client(base_url="http://market.test", transport=httpx.MockTransport(self.handler))

    def count(self, path):
        return sum(1 for p, _ in self.calls if p == path)


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.mkdtemp(prefix="strat-cache-")

    def fetch(self, m, state):
        with m.client() as c:
            return collect.fetch_public(c, state, NOW, cache_dir=self.cache)

    def test_first_run_takes_the_newest_500(self):
        m, state = FakeMarket(1200), {}
        raw, errors = self.fetch(m, state)
        self.assertEqual(errors, [])
        self.assertEqual(m.count("/api/ledger"), 1)
        self.assertEqual(m.calls[[p for p, _ in m.calls].index("/api/ledger")][1], {"after": "-500"})
        self.assertEqual([r["seq"] for r in raw["ledger"]], list(range(701, 1201)))
        self.assertEqual(state["ledger_seq"], 1200)
        self.assertFalse(raw["ledger_gap"])
        self.assertTrue(raw["ledger_first"])

    def test_pages_from_the_cursor_until_caught_up(self):
        m, state = FakeMarket(2300), {"ledger_seq": 1000}
        raw, _ = self.fetch(m, state)
        afters = [q["after"] for p, q in m.calls if p == "/api/ledger"]
        self.assertEqual(afters, ["1000", "1500", "2000"])
        self.assertEqual([r["seq"] for r in raw["ledger"]], list(range(1001, 2301)))
        self.assertEqual(state["ledger_seq"], 2300)
        self.assertFalse(raw["ledger_gap"])
        self.assertFalse(raw["ledger_first"])

    def test_first_page_past_the_cursor_is_a_gap(self):
        m, state = FakeMarket(5000), {"ledger_seq": 100}
        m.ledger = lambda after: [{"seq": s, "ts": NOW, "kind": "order", "data": {}} for s in range(4501, 5001)
                                  if s > after]            # the market kept only its newest 500 rows
        raw, _ = self.fetch(m, state)
        self.assertTrue(raw["ledger_gap"])
        self.assertEqual(state["ledger_seq"], 5000)

    def test_forty_page_cap_then_jump_to_the_newest_rows(self):
        m, state = FakeMarket(500 * 45 + 10), {"ledger_seq": 0}
        raw, errors = self.fetch(m, state)
        self.assertEqual(errors, [])
        self.assertEqual(m.count("/api/ledger"), 41)
        self.assertLessEqual(len(m.calls), 50)
        self.assertTrue(raw["ledger_gap"])
        self.assertEqual(state["ledger_seq"], 500 * 45 + 10)
        seqs = [r["seq"] for r in raw["ledger"]]
        self.assertEqual(len(seqs), len(set(seqs)))
        self.assertEqual(seqs[:20000], list(range(1, 20001)))
        self.assertEqual(seqs[20000:], list(range(500 * 45 + 10 - 499, 500 * 45 + 11)))

    def test_exactly_forty_full_pages_is_not_a_gap(self):
        m, state = FakeMarket(500 * 40), {"ledger_seq": 0}
        raw, _ = self.fetch(m, state)
        self.assertFalse(raw["ledger_gap"])
        self.assertEqual(len(raw["ledger"]), 20000)
        self.assertEqual(state["ledger_seq"], 20000)

    def test_ledger_error_mid_way_keeps_what_was_read(self):
        m = FakeMarket(2300, fail={"/api/ledger": lambda req: req.url.params["after"] == "1500"})
        state = {"ledger_seq": 1000}
        raw, errors = self.fetch(m, state)
        self.assertEqual(len(raw["ledger"]), 500)
        self.assertEqual(state["ledger_seq"], 1500)
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("ledger: HTTPStatusError: "), errors)
        self.assertIn("board", raw)


class EndpointTest(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.mkdtemp(prefix="strat-cache-")

    def test_one_failure_is_recorded_and_the_others_continue(self):
        m = FakeMarket(fail={"/api/board": 503, "/api/trades": httpx.ConnectError("refused")})
        with m.client() as c:
            raw, errors = collect.fetch_public(c, {}, NOW, cache_dir=self.cache)
        self.assertEqual(len(errors), 2)
        self.assertTrue(errors[0].startswith("board: HTTPStatusError: "), errors)
        self.assertEqual(errors[1], "trades: ConnectError: refused")
        self.assertIs(raw["errors"], errors)
        self.assertNotIn("board", raw)
        self.assertNotIn("trades", raw)
        for k in ("stats", "ledger", "forum", "shop_reviews", "demand", "catalog"):
            self.assertIn(k, raw)

    def test_wrong_json_type_is_an_error(self):
        m = FakeMarket()
        m.forum = "not a list"
        with m.client() as c:
            raw, errors = collect.fetch_public(c, {}, NOW, cache_dir=self.cache)
        self.assertEqual(errors, ["forum: TypeError: unexpected JSON str"])
        self.assertNotIn("forum", raw)

    def test_shapes_stats_round_forum_catalog(self):
        m = FakeMarket()
        m.forum = {"posts": [{"id": 7, "author": "x", "text": "t", "score": 3, "refs": [], "junk": 1}, "bad"]}
        with m.client() as c:
            raw, _ = collect.fetch_public(c, {}, NOW, cache_dir=self.cache)
        self.assertEqual(raw["stats"], {"round": 3, "deals": 1, "results": 2})
        self.assertEqual(raw["forum"], [{"id": 7, "author": "x", "text": "t", "score": 3}])
        self.assertEqual(raw["catalog"], {"G1": ["김치", "식품>김치", 10000, "food", 720]})
        self.assertEqual(("/api/shop-reviews", {"shop": "psyoung82"}), next(c for c in m.calls if c[0] == "/api/shop-reviews"))
        self.assertEqual(("/api/forum", {"limit": "100"}), next(c for c in m.calls if c[0] == "/api/forum"))

    def test_catalog_and_demand_cached_for_24h(self):
        m = FakeMarket()
        for t in (NOW, NOW + 3600, NOW + 23 * 3600):
            with m.client() as c:
                raw, errors = collect.fetch_public(c, {}, t, cache_dir=self.cache)
            self.assertEqual(errors, [])
            self.assertEqual(raw["catalog"]["G1"][2], 10000)
            self.assertEqual(raw["demand"][0]["item"], "김치")
        self.assertEqual((m.count("/api/catalog"), m.count("/api/demand")), (1, 1))
        self.assertTrue((Path(self.cache) / "catalog.json").exists())
        with m.client() as c:
            collect.fetch_public(c, {}, NOW + 25 * 3600, cache_dir=self.cache)
        self.assertEqual((m.count("/api/catalog"), m.count("/api/demand")), (2, 2))

    def test_stale_cache_beats_a_failed_fetch(self):
        with FakeMarket().client() as c:
            collect.fetch_public(c, {}, NOW, cache_dir=self.cache)
        m = FakeMarket(fail={"/api/catalog": 500})
        with m.client() as c:
            raw, errors = collect.fetch_public(c, {}, NOW + 48 * 3600, cache_dir=self.cache)
        self.assertEqual(raw["catalog"], {"G1": ["김치", "식품>김치", 10000, "food", 720]})
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("catalog: HTTPStatusError") and "using the cache from 48 h ago" in errors[0])

    def test_catalog_cached_without_shelf_hours_is_fetched_again(self):
        from memory.store import atomic_write_json
        atomic_write_json(Path(self.cache) / "catalog.json", {"ts": NOW, "data": {"G1": ["김치", "c", 10000, "food"]}})
        m = FakeMarket()
        with m.client() as c:
            raw, errors = collect.fetch_public(c, {}, NOW + 60, cache_dir=self.cache)
        self.assertEqual((m.count("/api/catalog"), raw["catalog"]["G1"][4]), (1, 720))

    def test_without_cache_dir_the_process_remembers(self):
        collect._memo.clear()
        m = FakeMarket()
        for t in (NOW, NOW + 3600):
            with m.client() as c:
                collect.fetch_public(c, {}, t)
        self.assertEqual(m.count("/api/catalog"), 1)
        collect._memo.clear()


class ReadOwnTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="strat-shop-"))

    def write(self, rel, rows):
        p = self.home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(json.dumps(r) + "\n" for r in rows) + '{"truncated', encoding="utf-8")

    def test_window_filter_and_summary_parts(self):
        now = time.time()
        self.write("logs/decisions/seller.jsonl", [{"id": "old", "ts": now - 7200, "kind": "quote"},
                                                   {"id": "new", "ts": now - 60, "kind": "sign"}, "not a row"])
        self.write("logs/decisions/buyer.jsonl", [{"id": "b", "ts": now - 10, "kind": "order"}])
        self.write("logs/decisions/forum.jsonl", [{"id": "f", "ts": now - 10, "kind": "post",
                                                   "choice": {"posted": True}, "why": "posted"}])
        self.write("logs/acp-sessions.jsonl", [{"ts": now - 5000, "event": "create"},
                                               {"ts": now - 5, "event": "refuse", "reason": "stale"}])
        self.write("logs/policy-changes.jsonl", [{"ts": now - i, "reason": f"r{i}"} for i in range(8)])
        cats = {f"c{i}": {"demand": i, "supply": None} for i in range(40)}
        (self.home / "memory").mkdir(parents=True)
        (self.home / "memory" / "summary.json").write_text(json.dumps(
            {"built_at": now, "events": 9, "market": {"categories": cats},
             "seller": {"sold_24h": 4, "stars": [5, 4, None], "regular_cats": list("abcdefghijkl"),
                        "skus": {"big": "x" * 1000}}}))
        own = collect.read_own(self.home, now, window_h=1)
        self.assertEqual([r["id"] for r in own["decisions"]["seller"]], ["new"])
        self.assertEqual([r["id"] for r in own["decisions"]["buyer"]], ["b"])
        self.assertEqual(own["decisions"]["restock"], [])
        self.assertEqual([r["id"] for r in own["decisions"]["forum"]], ["f"])
        self.assertEqual([r["event"] for r in own["acp"]], ["refuse"])
        self.assertEqual(len(own["policy_changes"]), 5)
        s = own["summary"]
        self.assertEqual(len(s["market_categories"]), 30)
        self.assertIn("c39", s["market_categories"])
        self.assertNotIn("c0", s["market_categories"])
        self.assertEqual(s["seller"]["sold_24h"], 4)
        self.assertEqual(s["seller"]["stars_mean"], 4.5)
        self.assertEqual(len(s["seller"]["regular_cats"]), 10)
        self.assertNotIn("skus", s["seller"])

    def test_cut_tail_inside_the_window_is_flagged(self):
        now = time.time()
        pad = "x" * 2000
        self.write("logs/decisions/buyer.jsonl", [{"ts": now - 3000 + i, "kind": "order", "pad": pad}
                                                  for i in range(200)])      # ~400 KB, all inside the hour
        self.write("logs/decisions/seller.jsonl", [{"ts": now - 7200, "kind": "quote", "pad": pad}]
                   + [{"ts": now - 60, "kind": "sign"}])                      # small: read whole
        own = collect.read_own(self.home, now, window_h=1)
        self.assertEqual(own["truncated"], ["buyer"])
        self.assertLess(len(own["decisions"]["buyer"]), 200)

    def test_missing_or_broken_files_never_raise(self):
        own = collect.read_own(self.home / "nothing-here", time.time())
        self.assertEqual(own["summary"], {})
        self.assertEqual(own["truncated"], [])
        self.assertEqual(own["decisions"], {r: [] for r in collect.ROLES})
        (self.home / "memory").mkdir()
        (self.home / "memory" / "summary.json").write_text('{"market": {"categories": [1, 2]}, "seller": 5}')
        own = collect.read_own(self.home, time.time())
        self.assertEqual(own["summary"]["market_categories"], {})
        self.assertNotIn("error", own)


    def test_inventory_read_and_reduced(self):
        market = self.home / ".market"
        market.mkdir()
        (market / "inventory.json").write_text(json.dumps(
            {"balance": 5000000, "bankrupt_line": 500000, "restock": {"open": False},
             "units": [{"unit": "u1", "sku": "G1", "name": "김치", "kind": "food", "cost": 7000, "grade": "A",
                        "supplied_at": 1, "expires_at": 2, "cert": "x" * 500}, "junk"]}), encoding="utf-8")
        (market / "keys.json").write_text("{not even read", encoding="utf-8")
        own = collect.read_own(self.home, time.time())
        self.assertEqual(own["inventory"], {"balance": 5000000, "units": [
            {"unit": "u1", "sku": "G1", "kind": "food", "cost": 7000, "grade": "A", "supplied_at": 1, "expires_at": 2}]})
        self.assertNotIn("inventory_error", own)

    def test_missing_or_broken_inventory(self):
        own = collect.read_own(self.home, time.time())
        self.assertEqual(own["inventory"], {})
        self.assertIn(".market/inventory.json", own["inventory_error"])
        (self.home / ".market").mkdir()
        for text in ("{broken", "[1, 2]", '{"balance": 1}'):
            (self.home / ".market" / "inventory.json").write_text(text, encoding="utf-8")
            own = collect.read_own(self.home, time.time())
            self.assertEqual(own["inventory"], {}, text)
            self.assertIn("inventory_error", own)
            self.assertNotIn("error", own)


def stamp(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


class KitLogTest(unittest.TestCase):
    """logs/market-kit.log: lines stamped HH:MM:SS without a date, reduced to counts."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="strat-kit-"))
        self.log = self.home / "logs" / "market-kit.log"
        self.log.parent.mkdir(parents=True)

    def write(self, lines, mtime):
        self.log.write_text("".join(f"{x}\n" for x in lines), encoding="utf-8")
        os.utime(self.log, (mtime, mtime))

    def test_counts_over_the_window(self):
        now = time.time()
        lines = [(now - 7200, "[supply] 9 x Old for 999, balance 1"),               # before the window
                 (now - 3000, "[kit] market_kit 4, /data"),
                 (now - 2900, "[supply] 3 x 김치 for 30000, balance 900000"),
                 (now - 2800, "[supply] 2 x Lamp for sale for 1500, balance 898500"),
                 (now - 2700, "[supply] {'sku': 'G1', 'qty': 2} refused: 429 {\"wait\": 40}"),
                 (now - 2600, "[supply] {'sku': 'G1', 'qty': 2} refused: 429 {\"wait\": 30}"),
                 (now - 2500, "[supply] {'sku': 'G2', 'qty': 2} refused: 402 {}"),
                 (now - 2400, "[supply] {'sku': 'G3', 'qty': 2} refused: 500 boom"),
                 (now - 2000, "[order 7] lamp, budget 100, round 3"),
                 (now - 1900, "[acp] paid 9000 through https://s1.example/acp, settled at ledger #5"),
                 (now - 1800, "[acp] checkout ships unit u1, warranty 6 h"),
                 (now - 1700, "[acp] checkout refused: 409 {\"error\": \"sold out\"}"),
                 (now - 1600, "[acp] https://s2.example serves no /.well-known/acp.json (404); this deal cannot settle"),
                 (now - 1500, "[acp] checkout refused: no G1 in stock"),                 # our shop's side
                 (now - 1400, "[acp] settled 김치 at 9000, ledger 9"),
                 (now - 1300, "[acp] settle refused: ['line_items']"),
                 (now - 1200, "[buyer] Traceback (most recent call last):"),
                 (now - 1100, "[buyer] {\"error\": \"HTTPStatusError: 530\"}"),             # hook output: not an error
                 (now - 1000, "[kit] order failed: ConnectError: refused"),
                 (now - 900, "[kit] market_kit 4, /data")]
        self.write([f"{stamp(t)} {x}" for t, x in lines], now - 60)
        own = collect.read_own(self.home, now, window_h=1)
        k = own["kit"]
        self.assertEqual((k["lines"], k["starts"], k["orders"]), (len(lines) - 1, 2, 1))
        self.assertEqual((k["supply_bought"], k["supply_units"], k["supply_cost"]), (2, 5, 31500))
        self.assertEqual(k["supply_refused"], {"429_window": 2, "402_balance": 1, "other": 1})
        self.assertEqual((k["paid"], k["paid_total"], k["pay_refused"]), (1, 9000, 2))
        self.assertEqual(k["pay_refused_why"], {'checkout refused: 409 {"error": "sold out"}': 1,
                                                "URL serves no /.well-known/acp.json (404); this deal cannot settle": 1})
        self.assertEqual((k["tracebacks"], k["errors"]), (1, 1))
        self.assertEqual(own["truncated"], [])
        self.assertNotIn("error", own)

    def test_midnight(self):
        now = datetime(2026, 11, 3, 0, 5, 0).timestamp()
        lines = ["22:30:00 [order 1] old", "23:50:00 [order 2] a", "23:59:59 [order 3] b",
                 "00:01:00 [order 4] c", "00:04:59 [order 5] d"]
        self.write(lines, now)
        got = collect._kit_lines(self.log, now, now - 3600, [])
        self.assertEqual(got, ["[order 2] a", "[order 3] b", "[order 4] c", "[order 5] d"])

    def test_a_stale_file_is_placed_from_its_mtime(self):
        now = datetime(2026, 11, 3, 0, 5, 0).timestamp()
        self.write(["00:04:00 [order 1] two days ago"], now - 2 * 86400)
        self.assertEqual(collect._kit_lines(self.log, now, now - 3600, []), [])

    def test_missing_file_never_raises(self):
        own = collect.read_own(self.home, time.time())
        self.assertEqual(own["kit"]["lines"], 0)
        self.assertEqual(own["kit"]["pay_refused_why"], {})
        self.assertNotIn("error", own)
        self.assertEqual(collect._kit_lines(self.home / "nope.log", time.time(), 0, []), [])

    def test_garbage_lines_are_skipped(self):
        now = time.time()
        self.write(["no stamp at all", "99:99:99 [order 1] bad stamp", "\x00\xff binary",
                    f"{stamp(now - 10)} [order 2] good", "  Traceback without a stamp"], now)
        self.log.write_bytes(self.log.read_bytes() + b"\xff\xfe not utf-8\n")
        os.utime(self.log, (now, now))
        self.assertEqual(collect._kit_lines(self.log, now, now - 3600, []), ["[order 2] good"])

    def test_rotated_tail_inside_the_window_is_flagged(self):
        now = time.time()
        pad = "x" * 500
        n = collect.KIT_TAIL_BYTES // 500 + 200                         # more than the tail holds
        self.write([f"{stamp(now - 1800 + i * 1800 / n)} [order {i}] {pad}" for i in range(n)], now)
        truncated = []
        got = collect._kit_lines(self.log, now, now - 3600, truncated)
        self.assertEqual(truncated, ["kit"])
        self.assertLess(len(got), n)
        self.assertTrue(all(x.startswith("[order ") and x.endswith(pad) for x in got))   # no cut first line
        self.log.write_text(f"{stamp(now - 5)} [kit] market_kit 4, /data\n", encoding="utf-8")   # rotated: fresh file
        own = collect.read_own(self.home, now)
        self.assertEqual((own["kit"]["lines"], own["kit"]["starts"], own["truncated"]), (1, 1, []))
        (self.log.parent / "market-kit.20261103-230000.log").write_text("old\n", encoding="utf-8")
        self.assertEqual(collect.read_own(self.home, now)["truncated"], ["kit"])   # its earlier lines are there

    def test_an_out_of_order_stamp_does_not_end_the_window(self):
        now = time.time()
        self.write([f"{stamp(now - 600)} [order 1] a", f"{stamp(now - 7200)} [order 2] odd",
                    f"{stamp(now - 60)} [order 3] b"], now)
        self.assertEqual(collect._kit_lines(self.log, now, now - 3600, []), ["[order 1] a", "[order 3] b"])


if __name__ == "__main__":
    unittest.main()
