"""Quotes run while the harness chooses, quote_shops at its hard_limits maximum (a person, 11/06 15:24: "as parallel as
possible"). The market gives one open order per buyer, 20 s apart at the earliest, so this is the parallelism there is."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hard_limits                 # noqa: E402
import market_buyer                # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def shop(n, sku="H1", lp=10000):
    return {"name": n, "url": f"https://{n}.example", "reviews": [], "usable": [{"sku": sku, "name": "청소기", "list_price": lp}]}


def open_task(n):
    return {"id": f"t-{n}", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}


class Pinned(unittest.TestCase):
    def test_quote_shops_stays_at_the_hard_limit_and_the_prefetch_stays_on(self):
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            shipped = json.load(f)["buyer"]["quote_shops"]
        self.assertEqual(shipped, 10)       # the bound was opened to 100 on 11/11 (a person); the shipped value stays
        self.assertEqual(hard_limits.BOUNDS["buyer.quote_shops"], (1, 100))
        self.assertTrue(market_buyer.PREFETCH_QUOTES)
        with open(os.path.join(HERE, "meta", "terra.md"), encoding="utf-8") as f:
            terra = f.read()
        self.assertIn("Do not lower the parallelism", terra)
        self.assertIn("never cut quote_shops or turn the prefetch off", terra)


class BestQuoteWithPrefetch(unittest.TestCase):
    def run_quotes(self, asks, chosen, prefetched, others, k=5, delay=0.0, wait=5):
        seen, lock, live, peak = [], threading.Lock(), [0], [0]
        cancelled = []

        def fake_quote(s, label, key, order, out, ctl=None):
            with lock:
                seen.append(s["name"])
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(delay)
            q = {"ask": asks.get(s["name"]), "text": "", "url": s["url"], "task": open_task(s["name"]), "refused": None}
            out[s["name"]] = q
            if ctl is not None and ctl.get("done") and ctl.get("winner") != s["name"]:
                market_buyer.cancel_quote(q, s["name"])
            with lock:
                live[0] -= 1
        with mock.patch.object(market_buyer, "quote", fake_quote), mock.patch.object(market_buyer, "log"), \
                mock.patch.object(market_buyer, "buyer_rpc", lambda url, m, p: cancelled.append(p["id"])), \
                mock.patch.object(market_buyer, "STARTED", time.time()):
            pre = {"got": {}, "ctl": market_buyer.quote_ctl()}
            pre["threads"] = market_buyer.start_quotes(prefetched, {"kind": "goods"}, 20000, lambda s: None,
                                                       pre["got"], pre["ctl"])
            best, got = market_buyer.best_quote(chosen, others, {"kind": "goods"}, {}, 20000, lambda s: None,
                                                {"quote_shops": k}, wait=wait, pre=pre)
            time.sleep(0.1)
        return best, got, seen, peak[0], cancelled

    def test_prefetched_shops_are_not_asked_again_and_the_first_batch_is_quote_shops(self):
        s = [shop(n) for n in "abcdefgh"]
        best, got, seen, peak, _ = self.run_quotes({"c": 6000}, s[0], s[1:5], s[1:])
        self.assertEqual(sorted(seen), list("abcde"))     # b-e while choosing, then the chosen a; nobody twice
        self.assertLessEqual(peak, 5)                     # never more at once than quote_shops
        self.assertEqual(best["name"], "c")

    def test_the_chosen_shop_among_the_prefetched_tops_up_the_batch_with_the_next(self):
        s = [shop(n) for n in "abcdefgh"]
        best, got, seen, peak, _ = self.run_quotes({"b": 6000}, s[1], s[1:5], [x for x in s if x is not s[1]])
        self.assertEqual(sorted(seen), list("abcde"))     # b-e prefetched, b chosen: a fills the fifth slot
        self.assertEqual(seen.count("b"), 1)
        self.assertLessEqual(peak, 5)

    def test_only_the_kept_talk_stays_open_so_one_order_can_never_close_twice(self):
        s = [shop(n) for n in "abcdefgh"]
        asks = {n: 6000 + i for i, n in enumerate("abcdefgh")}     # all within the .33 cap
        best, got, seen, peak, cancelled = self.run_quotes(asks, s[0], s[1:5], s[1:], delay=0.05)
        open_ = [n for n, q in got.items() if market_buyer.resumable(q)]
        self.assertEqual(open_, [best["name"]])
        self.assertEqual(sorted(cancelled), sorted(f"t-{n}" for n in got if n != best["name"]))

    def test_a_prefetched_quote_answering_after_the_choice_closes_itself(self):
        s = [shop(n) for n in "abc"]
        slow = threading.Event()

        def fake_quote(sh, label, key, order, out, ctl=None):
            if sh["name"] == "c":
                slow.wait(2)
            q = {"ask": 7000, "text": "", "url": sh["url"], "task": open_task(sh["name"]), "refused": None}
            out[sh["name"]] = q
            if ctl is not None and ctl.get("done") and ctl.get("winner") != sh["name"]:
                market_buyer.cancel_quote(q, sh["name"])
        cancelled = []
        with mock.patch.object(market_buyer, "quote", fake_quote), mock.patch.object(market_buyer, "log"), \
                mock.patch.object(market_buyer, "buyer_rpc", lambda url, m, p: cancelled.append(p["id"])), \
                mock.patch.object(market_buyer, "STARTED", time.time()):
            pre = {"got": {}, "ctl": market_buyer.quote_ctl()}
            pre["threads"] = market_buyer.start_quotes(s[1:], {}, 20000, lambda x: None, pre["got"], pre["ctl"])
            best, got = market_buyer.best_quote(s[0], s[1:], {}, {}, 20000, lambda x: None, {"quote_shops": 3},
                                                wait=0.3, pre=pre)
            self.assertNotIn("c", got)                    # c was left out: too slow
            slow.set()
            time.sleep(0.2)
        self.assertIn("t-c", cancelled)
        self.assertFalse(market_buyer.resumable(got["c"]))
        self.assertNotEqual(best["name"], "c")


class MainOverlap(unittest.TestCase):
    CATALOG = {"H1": {"list_price": 10000, "kind": "goods"}}
    ORDER = {"order": "o-1", "item": "청소기", "budget": 20000, "acceptable": ["H1"], "kind": "goods", "category": "가전>청소기"}

    def setUp(self):   # these runs are about the agent's choice racing the quotes, whatever pricing.json's buyer_choose says
        p = mock.patch.dict(os.environ, {"BUYER_CHOOSE_ENGINE": "agent"})
        p.start()
        self.addCleanup(p.stop)

    def run_main(self, shops, hopeless=False, choose_s=0.5, quote_s=0.5, choose_fails=False, asks=None, slower=None, deal=None, synchronize_overlap=False):
        printed, events, cancelled = [], [], []
        self.cancelled = cancelled
        lock = threading.Lock()
        four_started = threading.Event()
        choice_end = threading.Event()
        pre_count = [0]

        def note(what):
            with lock:
                events.append((what, time.time()))

        def fake_quote(s, label, key, order, out, ctl=None):
            note(f"quote {s['name']}")
            if synchronize_overlap:
                with lock:
                    pre_count[0] += 1
                    if pre_count[0] == 4:
                        four_started.set()
            time.sleep(quote_s + (slower or {}).get(s["name"], 0))
            if synchronize_overlap and not choice_end.wait(0.8):
                raise AssertionError("choice must overlap quotes")
            q = {"ask": (asks or {}).get(s["name"], 6600 if s["name"] == "c" else 9000), "text": "", "url": s["url"],
                 "task": open_task(s["name"]), "refused": None}
            with ctl["lock"]:            # as quote() does: publish closed when the choice was made first
                late = ctl["done"] and ctl["winner"] != s["name"]
                out[s["name"]] = dict(q, closed=True) if late else q
            if late:
                market_buyer.cancel_quietly(q, s["name"])

        def fake_choose(s, m, i, b, hurry=None, stop=None):
            self.stop = stop
            note("choose start")
            if synchronize_overlap and not four_started.wait(0.8):
                raise AssertionError("four quotes must start during choice")
            time.sleep(choose_s)
            note("choose end")
            if synchronize_overlap:
                choice_end.set()
            if choose_fails:
                raise RuntimeError("boom")
            return s[0], "t"

        def neg(*a, **k):
            note("talk")
            if deal is not None:
                return {"state": "TASK_STATE_COMPLETED", "task": None, "history": [("shop asked", deal), ("you offered", deal)],
                        "agreed": deal, "holds": 0, "receipt": {"item": "H1", "price": float(deal), "sig_seller": "s", "warranty_hours": 72}}
            return {"state": "TASK_STATE_REJECTED", "task": None, "receipt": None, "history": [], "agreed": None, "holds": 0}
        with tempfile.TemporaryDirectory() as d:
            sp, op = os.path.join(d, "shops.json"), os.path.join(d, "order.json")
            with open(sp, "w") as f:
                json.dump(shops, f)
            with open(op, "w") as f:
                json.dump(self.ORDER, f)
            with open(os.path.join(d, "catalog.json"), "w") as f:
                json.dump(self.CATALOG, f)
            with mock.patch.object(market_buyer, "MEMORY", os.path.join(d, "m.json")), \
                    mock.patch.object(market_buyer, "STARTED", time.time()), \
                    mock.patch.object(market_buyer, "market_round", lambda: 3), \
                    mock.patch.object(market_buyer, "serves_acp", lambda u: True), \
                    mock.patch.object(market_buyer, "buyer_cfg", lambda: {"quote_shops": 5}), \
                    mock.patch.object(market_buyer, "choose", fake_choose), \
                    mock.patch.object(market_buyer, "quote", fake_quote), \
                    mock.patch.object(market_buyer, "buyer_rpc", lambda url, m, p: cancelled.append(p["id"])), \
                    mock.patch.object(market_buyer, "hopeless", lambda *a, **k: hopeless), \
                    mock.patch.object(market_buyer, "negotiate", neg), \
                    mock.patch.object(market_buyer, "log"), \
                    mock.patch("builtins.print", lambda *a, **k: printed.append(a[0])), \
                    mock.patch.object(sys, "argv", ["x", "--shops", sp, "--item", "청소기", "--budget", "20000", "--order", op]):
                t0 = time.time()
                if choose_fails:
                    with self.assertRaises(RuntimeError):
                        market_buyer.main()
                    time.sleep(0.6)       # the prefetched quotes answer late and close themselves
                    return None, events, cancelled, []
                market_buyer.main()
                took = time.time() - t0
            with open(os.path.join(d, "measure", "orders.jsonl"), encoding="utf-8") as f:
                orders = [json.loads(x) for x in f]
        return json.loads(printed[-1]), events, took, orders

    def test_quotes_start_before_the_choice_ends_and_the_two_overlap(self):
        shops = [dict(shop(n), offers=[{"sku": "H1", "name": "청소기", "qty": 1}]) for n in "abcdef"]
        # Chooser-first case: keep both simulated work durations, but publish
        # quotes only after choice end; quote-first cancellation has its own test.
        out, events, took, orders = self.run_main(shops, synchronize_overlap=True)
        t = {w: ts for w, ts in events}
        quoted = [w for w, _ in events if w.startswith("quote ")]
        self.assertEqual(len(quoted), 5)                              # quote_shops in all, nobody twice
        self.assertEqual(len(set(quoted)), 5)
        early = [ts for w, ts in events if w.startswith("quote ") and ts < t["choose end"]]
        self.assertEqual(len(early), 4)                               # quote_shops - 1 asked while choosing
        self.assertLess(t["choose start"], min(ts for w, ts in events if w.startswith("quote ")) + 0.3)
        self.assertLess(took, 2.5)                                    # side by side (about 1.0 s), not one after the other
        self.assertEqual(out["shop"], "https://c.example")            # the best ask still wins
        # QUOTE_ENOUGH asks with one that fits end the wait: the chosen shop's own later quote is not waited for. How many
        # of the four prefetched (all at 0.5 s) are in by then is the scheduler's call (">= 4" failed 1 run in 9, 11/07)
        quoted = [q["shop"] for q in orders[-1]["quotes"] if q.get("ask") is not None]
        self.assertGreaterEqual(len(quoted), market_buyer.QUOTE_ENOUGH)
        self.assertIn("c", quoted)

    def test_a_prefetched_shop_a_moment_late_is_not_waited_for(self):
        # the flaky case made certain: d answers 0.3 s after a, b, c, which already hold an ask that fits
        shops = [dict(shop(n), offers=[{"sku": "H1", "name": "청소기", "qty": 1}]) for n in "abcdef"]
        out, events, took, orders = self.run_main(shops, slower={"d": 0.3})
        quoted = [q["shop"] for q in orders[-1]["quotes"] if q.get("ask") is not None]
        self.assertEqual(sorted(quoted), ["a", "b", "c"])
        self.assertEqual(out["shop"], "https://c.example")
        self.assertLess(took, 0.75)

    def test_clear_quotes_do_not_wait_for_a_slow_agent(self):
        shops = [dict(shop(n), offers=[{"sku": "H1", "name": "청소기", "qty": 1}]) for n in "abcdef"]
        out, events, took, orders = self.run_main(shops, choose_s=3, quote_s=0.2, asks={"a": 6500, "b": 6800})
        self.assertIn("choose start", [w for w, _ in events])        # the agent still runs
        self.assertLess(took, 1.5)                                    # but its answer is not waited for
        self.assertEqual(out["shop"], "https://a.example")
        self.assertTrue(self.stop.is_set())                           # and it is told to stop: no step after the order moved on

    def test_no_ask_near_my_limit_ends_the_order_without_a_talk(self):
        shops = [dict(shop(n), offers=[{"sku": "H1", "name": "청소기", "qty": 1}]) for n in "abcdef"]
        out, events, took, orders = self.run_main(shops, asks={"c": 8375})       # limit 6699: about 25% over
        self.assertNotIn("talk", [w for w, _ in events])
        self.assertEqual(orders[-1]["state"], "NO_VALUE")
        self.assertIsNone(out["receipt"])
        self.assertIn("t-c", self.cancelled)       # the kept talk is closed too: no shop holds a unit for me

    def test_an_ask_just_over_my_limit_is_still_talked_down(self):
        shops = [dict(shop(n), offers=[{"sku": "H1", "name": "청소기", "qty": 1}]) for n in "abcdef"]
        out, events, took, orders = self.run_main(shops, asks={"c": 6833})       # about 2% over limit6699
        self.assertIn("talk", [w for w, _ in events])

    def test_an_ask_15_percent_over_my_limit_is_talked_down(self):
        # 11/07 05:30-06:00 at 0.10: asks 10-20% over, 25 orders, no talk; before 02:17 they closed 6 of 33 talks
        shops = [dict(shop(n), offers=[{"sku": "H1", "name": "청소기", "qty": 1}]) for n in "abcdef"]
        out, events, took, orders = self.run_main(shops, asks={"c": 7700})
        self.assertIn("talk", [w for w, _ in events])

    def test_an_ask_7_percent_over_my_limit_is_talked_down(self):
        # 11/06 20:05-11/07 02:17: asks 3-6% over closed 66% of talks, 6-10% over 31%; 02:17-05:25 none closed (no talk)
        shops = [dict(shop(n), offers=[{"sku": "H1", "name": "청소기", "qty": 1}]) for n in "abcdef"]
        out, events, took, orders = self.run_main(shops, asks={"c": 7168})
        self.assertIn("talk", [w for w, _ in events])

    def test_a_receipt_over_my_limit_is_not_paid_on_the_racing_path(self):
        # Pro 11/07 03:32: 3 receipts paid 0.9-5% over the limit on 11/06 13:xx; the check before payment holds on today's path
        shops = [dict(shop(n), offers=[{"sku": "H1", "name": "청소기", "qty": 1}]) for n in "abcdef"]
        out, events, took, orders = self.run_main(shops, choose_s=3, quote_s=0.2, asks={"a": 6500, "b": 6800}, deal=7300)
        self.assertIn("talk", [w for w, _ in events])
        self.assertIsNone(out["receipt"])                      # limit 6699: the kit is not handed it
        self.assertFalse(orders[-1]["passed_to_kit"])
        out, events, took, orders = self.run_main(shops, choose_s=3, quote_s=0.2, asks={"a": 6500, "b": 6800}, deal=6500)
        self.assertIsNotNone(out["receipt"])                   # within it: paid

    def test_a_hopeless_order_asks_no_shop_and_skips_the_choice(self):
        shops = [dict(shop(n), offers=[{"sku": "H1", "name": "청소기", "qty": 1}]) for n in "abc"]
        out, events, took, orders = self.run_main(shops, hopeless=True)
        self.assertEqual(events, [])                                  # no quote, no harness, no talk
        self.assertIsNone(out["receipt"])
        self.assertTrue(orders[-1]["gave_up"])
        self.assertEqual(orders[-1]["how"], "gave_up")

    def test_a_choice_that_fails_closes_every_prefetched_talk(self):
        shops = [dict(shop(n), offers=[{"sku": "H1", "name": "청소기", "qty": 1}]) for n in "abcdef"]
        _, events, cancelled, _ = self.run_main(shops, choose_fails=True, choose_s=0.0, quote_s=0.3)
        quoted = sorted(w.split()[1] for w, _ in events if w.startswith("quote "))
        self.assertEqual(len(quoted), 4)
        self.assertEqual(sorted(cancelled), [f"t-{n}" for n in quoted])


class LateAndRace(unittest.TestCase):
    def test_past_quote_late_no_new_shop_is_asked_but_the_prefetched_are_read(self):
        s = [shop(n) for n in "abcdef"]
        seen = []

        def fake_quote(sh, label, key, order, out, ctl=None):
            seen.append(sh["name"])
            out[sh["name"]] = {"ask": 7000 if sh["name"] == "c" else 9000, "text": "", "url": sh["url"],
                               "task": open_task(sh["name"]), "refused": None}
        with mock.patch.object(market_buyer, "quote", fake_quote), mock.patch.object(market_buyer, "log"), \
                mock.patch.object(market_buyer, "buyer_rpc", lambda *a: None), \
                mock.patch.object(market_buyer, "STARTED", time.time() - market_buyer.QUOTE_LATE - 1):
            pre = {"got": {}, "ctl": market_buyer.quote_ctl()}
            pre["threads"] = market_buyer.start_quotes(s[1:4], {}, 20000, lambda x: None, pre["got"], pre["ctl"])
            best, got = market_buyer.best_quote(s[0], s[1:], {}, {}, 20000, lambda x: None, {"quote_shops": 5},
                                                wait=5, pre=pre)
        self.assertEqual(sorted(seen), list("bcd"))       # the chosen a and e, f were not asked
        self.assertEqual(best["name"], "c")

    def test_the_real_quote_lands_closed_when_the_choice_was_made_first(self):
        sh = shop("z")
        card = mock.Mock()
        card.json.return_value = {"supportedInterfaces": [{"protocolBinding": "JSONRPC", "protocolVersion": "1.0",
                                                           "url": "https://z.example/a2a"}]}
        ctl = market_buyer.quote_ctl()
        ctl.update(done=True, winner="a")
        got, cancelled = {}, []
        with mock.patch.object(market_buyer, "serves_acp", lambda u: True), \
                mock.patch.object(market_buyer.httpx, "get", lambda *a, **k: card), \
                mock.patch.object(market_buyer, "buyer_send", lambda url, text: open_task("z")), \
                mock.patch.object(market_buyer, "buyer_message_text", lambda t: "asking 7000"), \
                mock.patch.object(market_buyer, "buyer_metadata_ask", lambda t: 7000), \
                mock.patch.object(market_buyer, "buyer_rpc", lambda url, m, p: cancelled.append(p["id"])), \
                mock.patch.object(market_buyer, "log"):
            market_buyer.quote(sh, "청소기", "H1", {"kind": "goods"}, got, ctl)
        self.assertTrue(got["z"]["closed"])
        self.assertFalse(market_buyer.resumable(got["z"]))
        self.assertEqual(cancelled, ["t-z"])



class EnoughQuotes(unittest.TestCase):
    """best_quote stops waiting once QUOTE_ENOUGH asks are in and one fits: the slowest of 10 shops no longer sets the
    pace (quotes stage median 12.9 s, p90 33.9 s at quote_shops 10, 11/06 22:11-22:42); a late shop closes its own talk."""

    def test_a_slow_shop_is_not_waited_for_when_enough_asks_fit(self):
        s = [{"name": n, "url": f"https://{n}.example", "usable": [{"sku": "H1", "name": "x", "qty": 1, "list_price": 10000}]}
             for n in "abcdz"]
        slow = threading.Event()

        def fake_quote(sh, label, key, order, out, ctl=None):
            if sh["name"] == "z":
                slow.wait(3)
            out[sh["name"]] = {"ask": 6500 if sh["name"] != "z" else 5000, "text": "", "refused": None}
        with mock.patch.object(market_buyer, "quote", fake_quote), mock.patch.object(market_buyer, "log"), \
                mock.patch.object(market_buyer, "cancel_quietly", lambda q, n: None), \
                mock.patch.object(market_buyer, "STARTED", time.time()):
            t0 = time.time()
            best, got = market_buyer.best_quote(s[0], s[1:], {}, {}, 20000, lambda x: None, {"quote_shops": 5}, wait=5)
            took = time.time() - t0
            slow.set()
        self.assertLess(took, 1.5)
        self.assertNotEqual(best["name"], "z")

    def test_with_too_few_asks_it_still_waits(self):
        s = [{"name": n, "url": f"https://{n}.example", "usable": [{"sku": "H1", "name": "x", "qty": 1, "list_price": 10000}]}
             for n in "az"]

        def fake_quote(sh, label, key, order, out, ctl=None):
            if sh["name"] == "z":
                time.sleep(0.5)
            out[sh["name"]] = {"ask": 6500 if sh["name"] == "a" else 5000, "text": "", "refused": None}
        with mock.patch.object(market_buyer, "quote", fake_quote), mock.patch.object(market_buyer, "log"), \
                mock.patch.object(market_buyer, "cancel_quietly", lambda q, n: None), \
                mock.patch.object(market_buyer, "STARTED", time.time()):
            best, _ = market_buyer.best_quote(s[0], s[1:], {}, {}, 20000, lambda x: None, {"quote_shops": 2}, wait=5)
        self.assertEqual(best["name"], "z")          # one ask is not enough: the cheaper slow shop is waited for


class NextBatchEarly(unittest.TestCase):
    """Most of a batch has answered and no ask is within my limit: the next batch is asked now, not after the slowest shop
    (11/06 23:29-00:55: 52 of 97 orders had no fit in the first 10, found 17 s on average into the quotes). Off by default
    since 11/07 06:40 (QUOTE_NEXT_SHARE 1.0); these tests set 0.7 to keep the path working if it is turned back on."""

    def run_batches(self, asks, slow, names="abcdzefghi", k=5, share=0.7):
        s = [{"name": n, "url": f"https://{n}.example", "usable": [{"sku": "H1", "name": "x", "qty": 1, "list_price": 10000}]}
             for n in names]
        seen = []

        def fake_quote(sh, label, key, order, out, ctl=None):
            seen.append(sh["name"])
            time.sleep(slow.get(sh["name"], 0))
            out[sh["name"]] = {"ask": asks.get(sh["name"], 9000), "text": "", "refused": None}
        with mock.patch.object(market_buyer, "quote", fake_quote), mock.patch.object(market_buyer, "log"), \
                mock.patch.object(market_buyer, "cancel_quietly", lambda q, n: None), \
                mock.patch.object(market_buyer, "STARTED", time.time()), \
                mock.patch.object(market_buyer, "QUOTE_NEXT_SHARE", share if share is not None else market_buyer.QUOTE_NEXT_SHARE):
            t0 = time.time()
            best, got = market_buyer.best_quote(s[0], s[1:], {}, {}, 20000, lambda x: None, {"quote_shops": k}, wait=5)
            return best, time.time() - t0, seen

    def test_no_fit_from_most_of_the_batch_asks_the_next_without_the_slow_shop(self):
        best, took, seen = self.run_batches({"e": 6500}, {"z": 3})
        self.assertLess(took, 1.5)
        self.assertEqual(best["name"], "e")
        self.assertIn("e", seen)

    def test_the_slow_shop_of_the_first_batch_still_counts_when_it_lands(self):
        best, took, _ = self.run_batches({"z": 5000}, {"z": 0.3, **{n: 1 for n in "efghi"}})
        self.assertEqual(best["name"], "z")
        self.assertLess(took, 1.5)          # z fitted while the second batch was out: that ends the wait

    def test_a_fast_last_batch_still_waits_for_the_slow_shop_of_the_first(self):
        best, _, _ = self.run_batches({"z": 5000}, {"z": 0.5})
        self.assertEqual(best["name"], "z")

    def test_near_quote_until_the_batch_is_waited_for_as_before(self):
        with mock.patch.object(market_buyer, "QUOTE_UNTIL", 1):
            best, _, seen = self.run_batches({"z": 5000}, {"z": 0.5})
        self.assertEqual(best["name"], "z")
        self.assertNotIn("e", seen)

    def test_by_default_the_slow_shop_of_the_batch_is_waited_for_before_the_next(self):
        # 11/07 06:40: off by default (QUOTE_NEXT_SHARE 1.0) - market steady at 0.44-0.45, my conversion 0.57 -> 0.45 and
        # deals 40 -> 35 an hour after it (00:04-02:01); orders with a 2nd batch found a fitting ask 8 of 31 -> 1 of 35
        best, _, seen = self.run_batches({"z": 5000}, {"z": 0.5}, share=None)
        self.assertEqual(best["name"], "z")
        self.assertNotIn("e", seen)

    def test_with_no_batch_left_the_slow_shop_is_waited_for(self):
        best, _, _ = self.run_batches({"z": 5000}, {"z": 0.5}, names="abcdz")
        self.assertEqual(best["name"], "z")


if __name__ == "__main__":
    unittest.main()
