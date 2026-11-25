"""acp_gate: buyers in pricing.json seller.refuse_buyers are answered "out of stock" at ACP complete; others settle.

    python -m unittest test_acp_gate -v        (from submissions/26622007/week-08)

The ledger is a fake /api/ledger; the kit's AcpShop and acp_shop.AcpCheckout run with a fake market.
"""
import asyncio
import base64
import json
import os
import sys
import tempfile
import unittest
import unittest.mock

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.applications import Starlette
from starlette.testclient import TestClient

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "scripts"))
import acp_gate                    # noqa: E402
import acp_shop                    # noqa: E402
import market_kit as kit           # noqa: E402

BAD = "totato2203-r3"


class FakeLedger:
    """/api/ledger?after=N over a list of rows, PAGE rows a call; counts calls."""

    def __init__(self, rows):
        self.rows, self.calls = rows, 0

    def __call__(self, path, timeout=10):
        self.calls += 1
        after = int(path.split("after=")[1])
        if after == -1:
            return self.rows[-1:]
        return [r for r in self.rows if r["seq"] > after][:acp_gate.PAGE]


def order_row(seq, order, buyer):
    return {"seq": seq, "kind": "order", "data": {"order": order, "buyer": buyer, "expires": 0}}


def ledger(*orders):
    rows = [order_row(i + 1, o, b) for i, (o, b) in enumerate(orders)]
    rows += [{"seq": len(rows) + 1 + i, "kind": "quote", "data": {}} for i in range(3)]
    return FakeLedger(rows)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pricing = os.path.join(self.tmp.name, "pricing.json")
        self.md = os.path.join(self.tmp.name, ".market")
        self.set_refused([BAD])
        self.ledger = ledger(("o-bad", BAD), ("o-good", "buyer-01"))
        self.logs = []
        self.gate = acp_gate.Gate(acp_gate.OrderBuyers(self.ledger), self.pricing, self.md, self.logs.append)
        self.gate.orders._read()                                   # what the background thread does first

    def tearDown(self):
        self.tmp.cleanup()

    def set_refused(self, names, key="seller"):
        with open(self.pricing, "w", encoding="utf-8") as f:
            json.dump({"tiers": [], key: {"refuse_buyers": names}} if names is not None else {"tiers": []}, f)

    def refused_lines(self):
        p = os.path.join(self.md, "measure", "refused.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [json.loads(line) for line in f]


class GateTest(Base):
    def test_refuses_the_listed_buyer_and_records_it(self):
        s = {"id": "cs_1", "task": "t-1", "unit": "u-1", "line_items": [{"id": "G1", "quantity": 1}],
             "totals": [{"type": "total", "amount": 77000}]}
        self.assertEqual(self.gate.refusal("o-bad", s), acp_gate.OUT_OF_STOCK)
        [rec] = self.refused_lines()
        self.assertEqual((rec["buyer"], rec["order"], rec["item"], rec["price"], rec["task"]),
                         (BAD, "o-bad", "G1", 77000, "t-1"))
        self.assertIn("ts", rec)
        self.assertTrue(any("REFUSED buyer totato2203-r3 order o-bad" in line for line in self.logs))

    def test_other_buyers_pass(self):
        self.assertIsNone(self.gate.refusal("o-good", {}))
        self.assertIsNone(self.gate.refusal("o-unknown", {}))      # not in the ledger: settle as before
        self.assertEqual(self.refused_lines(), [])

    def test_off_when_the_list_is_empty_or_missing(self):
        self.ledger.calls = 0
        for names in ([], None):
            self.set_refused(names)
            self.assertIsNone(self.gate.refusal("o-bad", {}))
        self.assertEqual(self.ledger.calls, 0)                      # off: the ledger is not even read
        self.assertEqual(acp_gate.refused_buyers(os.path.join(self.tmp.name, "nope.json")), [])

    def test_ledger_failure_never_blocks_a_sale(self):
        def down(path, timeout=10):
            raise OSError("market down")
        ob = acp_gate.OrderBuyers(down)
        ob.seq = 5                                                 # warm before, the market down now
        gate = acp_gate.Gate(ob, self.pricing, self.md, self.logs.append)
        self.assertIsNone(gate.refusal("o-bad", {}))
        self.assertTrue(any("ledger lookup for o-bad failed" in line for line in self.logs))

    def test_reads_forward_and_pages(self):
        rows = [order_row(i + 1, f"o-{i}", "buyer-01") for i in range(1200)] + [order_row(1201, "o-late", BAD)]
        led = FakeLedger(rows)
        ob = acp_gate.OrderBuyers(led)
        self.assertIsNone(ob.buyer("o-late"))                      # cold: a payment never does the first read
        self.assertEqual(led.calls, 0)
        ob._read()                                                 # the background's first pass
        self.assertEqual(ob.buyer("o-late"), BAD)
        self.assertEqual(ob.buyer("o-5"), "buyer-01")              # cached: no new read
        calls = led.calls
        led.rows.append(order_row(1202, "o-new", BAD))
        self.assertEqual(ob.buyer("o-new"), BAD)
        self.assertEqual(led.calls, calls + 1)                     # a payment reads one page from the last row seen
        self.assertIsNone(ob.buyer("o-never"))
        self.assertEqual(led.calls, calls + 2)

    def test_a_payment_waits_at_most_wait_for_a_running_read(self):
        ob = acp_gate.OrderBuyers(FakeLedger([order_row(1, "o-1", BAD)]))
        ob._read()
        ob.lock.acquire()                                          # a slow background page
        try:
            with unittest.mock.patch.object(acp_gate, "WAIT", 0.05):
                self.assertIsNone(ob.buyer("o-2"))                 # gave up waiting: settle as usual
        finally:
            ob.lock.release()

    def test_cold_start_reads_back_a_bounded_window(self):
        rows = [order_row(i + 1, f"o-{i}", "buyer-01") for i in range(acp_gate.BACK + 600)]
        ob = acp_gate.OrderBuyers(FakeLedger(rows))
        ob._read()
        self.assertIsNone(ob.buyer("o-0"))                         # older than BACK rows: not read
        self.assertEqual(ob.buyer(f"o-{acp_gate.BACK + 599}"), "buyer-01")


def receipt(key, item="G1", price=77000, task="t-1", shop="Nocha Furniture"):
    r = {"shop": shop, "item": item, "price": price, "task": task}
    r["sig_seller"] = base64.b64encode(key.sign(acp_shop.canon({k: r[k] for k in acp_shop.RECEIPT_KEYS}))).decode()
    return r


def pay_body(order, amount=77000):
    return {"payment_data": {"order": order, "checkout_mandate": {"mandate": {}, "sig": "c"},
                             "payment_mandate": {"mandate": {"amount": amount}, "sig": "p"}}}


class KitMarket:
    """The kit's Market for AcpShop: Round 2 inventory, and /api/settle answered and counted."""

    def __init__(self):
        self.settles = []

    def call(self, method, path, role="buyer", **kw):
        if path == "/api/settle":
            self.settles.append(kw.get("json"))
            return httpx.Response(200, json={"ledger": 9, "mandate_receipt": {"ledger": 9}})
        return httpx.Response(200, json={"round": 2, "units": []})


class KitAcpShopTest(Base):
    """The production path: the tunneled shop's ACP is the kit's AcpShop with the gate load_gate gives it."""

    def setUp(self):
        super().setUp()
        self.key = Ed25519PrivateKey.generate()
        self.market = KitMarket()
        self.shop = kit.AcpShop(self.key, self.market)
        self.shop.gate = lambda order, s: self.gate.refusal(order, s)

    def run_path(self, path, body):
        class Req:
            method = "POST"
        return asyncio.run(self.shop.handle(Req(), path, json.dumps(body).encode(), "https://x.example"))

    def open_session(self, task):
        r = self.run_path("acp/checkout_sessions", {"line_items": [{"id": "G1", "quantity": 1}],
                                                     "receipt": receipt(self.key, task=task)})
        self.assertEqual(r.status_code, 201)
        return json.loads(r.body)["id"]

    def test_refused_buyer_gets_out_of_stock_and_nothing_settles(self):
        sid = self.open_session("t-bad")
        r = self.run_path(f"acp/checkout_sessions/{sid}/complete", pay_body("o-bad"))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(json.loads(r.body), {"error": acp_gate.OUT_OF_STOCK})
        self.assertEqual(self.market.settles, [])
        self.assertEqual(self.shop.sessions[sid]["status"], "canceled")
        self.assertEqual(len(self.refused_lines()), 1)

    def test_other_buyer_settles(self):
        sid = self.open_session("t-good")
        r = self.run_path(f"acp/checkout_sessions/{sid}/complete", pay_body("o-good"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([s["order"] for s in self.market.settles], ["o-good"])
        self.assertEqual(self.refused_lines(), [])

    def test_a_broken_gate_never_blocks_a_sale(self):
        def broken(order, s):
            raise RuntimeError("boom")
        self.shop.gate = broken
        sid = self.open_session("t-x")
        self.assertEqual(self.run_path(f"acp/checkout_sessions/{sid}/complete", pay_body("o-bad")).status_code, 200)

    def test_load_gate_finds_this_folder(self):
        import threading               # load_gate runs a fresh copy of the file, so patch the thread, not acp_gate
        with unittest.mock.patch.object(threading.Thread, "start") as start:  # no real ledger threads in tests
            g = kit.load_gate(HERE)
        self.assertEqual(start.call_count, 2)                     # the order read and the one-star scan
        self.assertTrue(callable(g))
        self.assertIsNone(kit.load_gate(self.tmp.name))           # a folder without acp_gate.py: no gate


class AcpCheckoutTest(Base):
    """The Render path: acp_shop.AcpCheckout with the gate seller.build passes."""

    def setUp(self):
        super().setUp()
        self.key = Ed25519PrivateKey.generate()
        self.settles = []

        def market(request):
            self.settles.append(json.loads(request.content))
            return httpx.Response(200, json={"ledger": 9, "mandate_receipt": {"ledger": 9}})

        self.acp = acp_shop.AcpCheckout("https://x.example", lambda: self.key, self.logs.append,
                                        transport=httpx.MockTransport(market), gate=self.gate)
        self.client = TestClient(Starlette(routes=self.acp.routes()))

    def session(self, task):
        rec = receipt(self.key, task=task)
        return self.client.post("/acp/checkout_sessions", json={"line_items": [{"id": "G1", "quantity": 1}],
                                                                "receipt": rec}).json()["id"]

    def test_refused_and_passed(self):
        bad = self.session("t-bad")
        r = self.client.post(f"/acp/checkout_sessions/{bad}/complete", json=pay_body("o-bad"))
        self.assertEqual((r.status_code, r.json()), (409, {"error": acp_gate.OUT_OF_STOCK}))
        self.assertEqual(self.client.get(f"/acp/checkout_sessions/{bad}").json()["status"], "canceled")
        good = self.session("t-good")
        self.assertEqual(self.client.post(f"/acp/checkout_sessions/{good}/complete",
                                          json=pay_body("o-good")).status_code, 200)
        self.assertEqual([s["order"] for s in self.settles], ["o-good"])


def result_row(seq, order, buyer, shop, stars, verified=True, seller="Nocha Furniture", rnd=3):
    receipt = {"shop": seller, "price": 1000} if seller else None
    return {"seq": seq, "kind": "result", "round": rnd, "data": {"order": order, "buyer": buyer, "shop": shop,
                                                                  "stars": stars, "verified": verified, "receipt": receipt}}


class OneStarTest(Base):
    US, US2, THEM = "https://a.trycloudflare.com", "https://b.trycloudflare.com/", "https://them.example"

    def set_at(self, at):
        with open(self.pricing, "w", encoding="utf-8") as f:
            json.dump({"tiers": [], "seller": {"refuse_buyers": [BAD], "refuse_one_star_at": at}}, f)

    def rows(self):
        r = [{"seq": 1, "kind": "order", "round": 2, "data": {}}]               # Round 2: before the scan's start
        r += [result_row(2, "o-r2", "grump", self.US, 1, rnd=2)]
        n = 3
        for i, shop in enumerate((self.US, self.US2, self.US)):                # grump: 3 on two of our URLs
            r.append(result_row(n, f"o-g{i}", "grump", shop, 1)); n += 1
        for i in range(2):                                                     # meh: only 2
            r.append(result_row(n, f"o-m{i}", "meh", self.US, 1)); n += 1
        r.append(result_row(n, "o-m2", "meh", self.US, 1, verified=False)); n += 1    # unverified: not counted
        r.append(result_row(n, "o-m3", "meh", self.US, 2)); n += 1                    # 2 stars: not counted
        r.append(result_row(n, "o-m4", "meh", self.US, True)); n += 1                 # stars True is not 1
        for i in range(3):                                                     # other shop's 1-stars: not ours
            r.append(result_row(n, f"o-t{i}", "other", self.THEM, 1, seller="someone")); n += 1
        r.append(result_row(n, "o-g0", "grump", self.US, 1)); n += 1                  # same order twice: one result
        return FakeLedger(r)

    def stars(self, get):
        return acp_gate.OneStars(self.md, get)

    def test_three_verified_one_stars_on_our_urls_refuse_the_buyer(self):
        self.set_at(3)
        st = self.stars(self.rows())
        self.assertFalse(st.scan())
        self.assertEqual(st.counts(), {"grump": 3, "meh": 2})
        self.assertEqual(set(st.load()["urls"]), {self.US, self.US2.rstrip("/")})
        self.assertEqual(acp_gate.refused_buyers(self.pricing, self.md), [BAD, "grump"])
        self.set_at(0)
        self.assertEqual(acp_gate.refused_buyers(self.pricing, self.md), [BAD])
        self.set_refused([BAD])                                                 # no key: off
        self.assertEqual(acp_gate.refused_buyers(self.pricing, self.md), [BAD])

    def test_the_gate_refuses_a_one_star_buyer(self):
        self.set_at(3)
        self.stars(self.rows()).scan()
        self.gate.orders.buyers["o-new"] = "grump"
        self.assertEqual(self.gate.refusal("o-new", {}), acp_gate.OUT_OF_STOCK)
        self.gate.orders.buyers["o-new2"] = "meh"
        self.assertIsNone(self.gate.refusal("o-new2", {}))

    def test_an_exempt_buyer_passes_the_one_star_rule_but_not_refuse_buyers(self):
        self.stars(self.rows()).scan()
        for exempt, refused, out in (([BAD], [], ["grump"]), (["grump"], [BAD], [BAD]),
                                     (["grump", BAD], [BAD], [BAD]), ("grump", [BAD], [BAD, "grump"])):
            with open(self.pricing, "w", encoding="utf-8") as f:   # a string, not a list, exempts no one
                json.dump({"tiers": [], "seller": {"refuse_buyers": refused, "refuse_one_star_at": 3,
                                                   "refuse_exempt": exempt}}, f)
            self.assertEqual(acp_gate.refused_buyers(self.pricing, self.md), out, exempt)
        with open(self.pricing, "w", encoding="utf-8") as f:
            json.dump({"tiers": [], "seller": {"refuse_buyers": [BAD], "refuse_one_star_at": 3,
                                               "refuse_exempt": ["grump", BAD]}}, f)
        self.gate.orders.buyers.update({"o-g": "grump", "o-b": BAD, "o-m": "meh"})
        self.assertIsNone(self.gate.refusal("o-g", {}))                          # exempt: 3 one-stars, still sold to
        self.assertEqual(self.gate.refusal("o-b", {}), acp_gate.OUT_OF_STOCK)    # refuse_buyers wins over exempt
        self.assertIsNone(self.gate.refusal("o-m", {}))

    def test_scan_pages_and_resumes(self):
        led = self.rows()
        with unittest.mock.patch.object(acp_gate, "PAGE", 3):
            st = self.stars(led)
            self.assertTrue(st.scan(max_pages=1))
            while st.scan(max_pages=1):
                pass
        self.assertEqual(st.counts(), {"grump": 3, "meh": 2})
        self.assertEqual(st.load()["after"], led.rows[-1]["seq"])

    def test_a_one_star_seen_before_its_url_still_counts(self):
        r = [{"seq": 1, "kind": "order", "round": 3, "data": {}},
             result_row(2, "o-1", "grump", self.US, 1, seller=None)]           # no receipt name on this one
        r += [result_row(3 + i, f"o-{i + 2}", "grump", self.US, 1) for i in range(2)]
        st = self.stars(FakeLedger(r))
        st.scan()
        self.assertEqual(st.counts(), {"grump": 3})

    def test_an_unverified_receipt_does_not_make_a_url_ours(self):
        r = [{"seq": 1, "kind": "order", "round": 3, "data": {}},
             result_row(2, "o-f", "x", self.THEM, 3, verified=False)]                  # forged: names us, unverified
        r += [result_row(3 + i, f"o-{i}", "victim", self.THEM, 1, seller="someone") for i in range(3)]
        st = self.stars(FakeLedger(r))
        st.scan()
        self.assertEqual((st.load()["urls"], st.counts()), ([], {}))

    def test_odd_rows_are_skipped_and_the_count_goes_on(self):
        r = [{"seq": 1, "kind": "order", "round": 3, "data": {}}]
        odd = [{"receipt": "Nocha Furniture"}, {"receipt": {"shop": ["Nocha Furniture"]}}, {"order": {"o": 1}},
               {"shop": 5}, {"buyer": None}]
        for i, bad in enumerate(odd):
            row = result_row(2 + i, f"o-x{i}", "grump", self.US, 1)
            row["data"].update(bad)
            r.append(row)
        r += ["not a row", {"seq": 20, "kind": "result", "round": 3, "data": "x"}]
        r += [result_row(21 + i, f"o-{i}", "grump", self.US, 1) for i in range(3)]
        led = FakeLedger([x for x in r if isinstance(x, dict)])
        page = led.__call__
        led_rows = led.rows

        def get(path, timeout=10):                                            # the odd string goes in a page too
            out = page(path, timeout)
            return out + ["not a row"] if "after=-1" not in path else out
        st = self.stars(get)
        st.scan()
        self.assertEqual(st.counts(), {"grump": 5})       # 3 good + the two odd receipts; odd order/shop/buyer drop
        self.assertEqual(st.load()["after"], led_rows[-1]["seq"])

    def test_a_failed_page_keeps_what_was_read(self):
        led = self.rows()
        calls = {"n": 0}
        os.makedirs(self.md)
        with open(os.path.join(self.md, acp_gate.OneStars.FILE), "w") as f:      # past the round's start already
            json.dump({"after": 2, "urls": [], "rows": []}, f)

        def get(path, timeout=10):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("ledger down")
            return led(path, timeout)
        with unittest.mock.patch.object(acp_gate, "PAGE", 3):
            st = self.stars(get)
            with self.assertRaises(OSError):
                st.scan()
            self.assertGreater(st.load()["after"], 2)                          # the first page was saved
            while st.scan():
                pass
        self.assertEqual(st.counts(), {"grump": 3, "meh": 2})

    def test_a_broken_file_counts_nothing(self):
        os.makedirs(self.md)
        with open(os.path.join(self.md, acp_gate.OneStars.FILE), "w") as f:
            f.write("{not json")
        self.set_at(3)
        self.assertEqual(acp_gate.refused_buyers(self.pricing, self.md), [BAD])


class ConfigTest(unittest.TestCase):
    def test_shipped_pricing_refuses_only_totato(self):
        # 대표님 11/07 01:23: the 3-one-star rule is off; only totato2203-r3 stays refused
        p = os.path.join(HERE, "pricing.json")
        with tempfile.TemporaryDirectory() as md:
            self.assertEqual(acp_gate.refused_buyers(p, md), [BAD])
            with open(os.path.join(md, acp_gate.OneStars.FILE), "w") as f:
                json.dump({"after": 1, "urls": ["https://a.trycloudflare.com"],
                           "rows": [{"order": f"o{i}", "buyer": b, "shop": "https://a.trycloudflare.com"}
                                    for i, b in enumerate(["q00"] * 5 + ["grump"] * 3)]}, f)
            self.assertEqual(acp_gate.OneStars(md).counts(), {"q00": 5, "grump": 3})
            self.assertEqual(acp_gate.refused_buyers(p, md), [BAD])
        self.assertEqual(acp_gate.one_star_at(p), 0)


class MetaGuardTest(unittest.TestCase):
    """Terra and Astra may not change who is refused: meta_harness undoes a cycle that does."""

    def problems(self, head, now, code=()):
        import meta_harness
        from unittest import mock
        with mock.patch.object(meta_harness, "pricing_at_head", return_value=head), \
                mock.patch.object(meta_harness, "pricing_now", return_value=now), \
                mock.patch.object(meta_harness, "git", return_value=""):
            return meta_harness.limit_problems(list(code))

    def test_shipped_exempts_buyers_who_rate_us_well(self):
        # 11/07 01:1x: the 1-star rule refused shaun0927-labbuyer (272 five-star, 3 one-star) on 157 checkouts, 180.5M
        with open(os.path.join(acp_gate.HERE, "pricing.json"), encoding="utf-8") as f:
            seller = json.load(f)["seller"]
        self.assertIn("shaun0927-labbuyer", seller["refuse_exempt"])
        self.assertIn("gom31-certified-goods", seller["refuse_exempt"])
        self.assertIn("q00", seller["refuse_exempt"])
        self.assertEqual(seller["refuse_buyers"], ["totato2203-r3"])

    def test_seller_settings_and_the_gate_are_the_owners(self):
        head = {"seller": {"refuse_buyers": ["totato2203-r3"], "refuse_one_star_at": 3}}
        self.assertEqual([p for p in self.problems(head, head) if "owner" in p], [])
        exempt = {"seller": {**head["seller"], "refuse_exempt": ["q00"]}}
        for now in ({"seller": {**exempt["seller"], "refuse_exempt": []}},
                    {"seller": {**exempt["seller"], "refuse_exempt": ["q00", "grump"]}}, head):
            self.assertTrue(any("refuse_exempt" in p for p in self.problems(exempt, now)), now)
        for now in ({"seller": {"refuse_buyers": ["totato2203-r3"], "refuse_one_star_at": 0}},
                    {"seller": {"refuse_buyers": [], "refuse_one_star_at": 3}}, {}):
            self.assertTrue(any("owner" in p for p in self.problems(head, now)), now)
        self.assertTrue(any("acp_gate.py" in p for p in self.problems(head, head, ["acp_gate.py"])))


if __name__ == "__main__":
    unittest.main()
