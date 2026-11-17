"""Week 07 tests for review-aware shop selection and durable buyer memory."""

from __future__ import annotations

import json
import unittest
from unittest import mock
from pathlib import Path
from tempfile import TemporaryDirectory

from market_buyer import MemoryStore, choose_shop


class MarketBuyerTests(unittest.TestCase):
    def test_prefers_honest_reviews_when_shops_otherwise_match(self) -> None:
        shops = [
            {
                "name": "bad-shop",
                "url": "http://bad.test",
                "reviews": [
                    {"stars": 1, "review": "Receipt price was higher than agreed."},
                    {"stars": 2, "review": "Overcharged on the receipt, avoid."},
                ],
            },
            {
                "name": "good-shop",
                "url": "http://good.test",
                "reviews": [
                    {"stars": 5, "review": "Fair price, receipt matched."},
                    {"stars": 4, "review": "Quick deal at a fair price."},
                ],
            },
        ]
        chosen, reason = choose_shop(shops, [])
        self.assertEqual(chosen["name"], "good-shop")
        self.assertIn("review", reason.lower())

    def test_successful_memory_breaks_tie_between_unreviewed_shops(self) -> None:
        shops = [
            {"name": "new-shop", "url": "http://new.test", "reviews": []},
            {"name": "known-shop", "url": "http://known.test", "reviews": []},
        ]
        memories = [
            {"shop": "known-shop", "item": "lamp", "price": 105, "outcome": "deal"}
        ]
        no_explore = type("NoExplore", (), {"random": lambda self: 1.0})()   # exploration off
        chosen, _ = choose_shop(shops, memories, rng=no_explore)
        self.assertEqual(chosen["name"], "known-shop")

    def test_memory_store_appends_json_lines(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "memory.jsonl"
            store = MemoryStore(path)
            record = {"shop": "good-shop", "item": "lamp", "price": 105, "outcome": "deal"}
            store.append(record)
            self.assertEqual(store.load(), [record])
            self.assertEqual(json.loads(path.read_text().strip()), record)


if __name__ == "__main__":
    unittest.main()


class FallbackTests(unittest.TestCase):
    """10/26: an order lost to a shop that did not answer is retried once at the next best shop."""

    def _run(self, outcomes, cap=None, shop_names=("fair-furniture", "other")):
        import contextlib, io, json as _json, sys as _sys
        from tempfile import TemporaryDirectory
        from pathlib import Path as _P
        import market_buyer as mb
        calls = []
        self.caps = []

        def fake_trade(chosen, args, memories, memory, cap=None):
            calls.append(chosen["name"]); self.caps.append(cap)
            out = outcomes[len(calls) - 1]
            detail = None
            if isinstance(out, tuple):
                out, detail = out
            res = {"outcome": out, "detail": detail, "price": 100 if out == "deal" else None,
                   "receipt": {"item": "lamp", "price": 100, "task": "t"} if out == "deal" else None, "rounds": 1}
            return res, {"shop": chosen["name"], "outcome": out, "item": "lamp"}

        with TemporaryDirectory() as d:
            shops = [{"name": n, "url": f"https://{n}.example", "reviews": []} for n in shop_names]
            sp = _P(d) / "shops.json"; sp.write_text(_json.dumps(shops))
            argv = ["market_buyer.py", "--shops", str(sp), "--item", "lamp", "--budget", "120",
                    "--memory", str(_P(d) / "mem.jsonl")]
            out = io.StringIO()
            with mock.patch.object(mb, "trade", fake_trade), mock.patch.object(mb, "kit_feedback", lambda: {}), \
                    mock.patch.object(mb, "load_shop_prices", lambda: {}), mock.patch.object(mb, "ALLIES", ("fair-furniture",)), mock.patch.object(mb, "log_review_spam", lambda s: None), \
                    mock.patch.object(mb, "market_cap", lambda item: cap), mock.patch.object(mb, "current_round", lambda: 2), \
                    mock.patch.object(_sys, "argv", argv), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                mb.main()
            return calls, _json.loads(out.getvalue().strip().splitlines()[-1])

    def test_error_retries_next_shop(self):
        calls, line = self._run(["error", "deal"])
        self.assertEqual(calls, ["fair-furniture", "other"])
        self.assertEqual(line["shop"], "https://other.example")
        self.assertEqual(line["receipt"]["price"], 100)

    def test_capped_walk_tries_next_shop_with_full_budget(self):
        calls, line = self._run([("walk", "cap: ask 115 still above the market cap 107 after 5 offers"), "deal"], cap=107)
        self.assertEqual(calls, ["fair-furniture", "other"])
        self.assertEqual(self.caps, [107, None], "the cap on the first shop only")
        self.assertEqual(line["receipt"]["price"], 100)

    def test_capped_walk_with_no_other_shop_retries_same_shop_uncapped(self):
        calls, line = self._run([("walk", "cap: ask 115 above cap 107"), "deal"], cap=107, shop_names=("solo",))
        self.assertEqual(calls, ["solo", "solo"]); self.assertEqual(self.caps, [107, None])

    def test_walk_is_not_retried(self):
        calls, line = self._run(["walk", "deal"])
        self.assertEqual(calls, ["fair-furniture"])
        self.assertIsNone(line["receipt"])


class Round3AcpTests(unittest.TestCase):
    """Phase 6 (week 08): from Round 3 the buyer only picks shops that serve an ACP checkout."""

    def test_round_from_kit_log(self):
        import market_buyer as mb
        with TemporaryDirectory() as d:
            log = Path(d) / "kit.log"
            log.write_text("10:00:00 [order o-1] lamp, budget 120, round 2\n...\n10:00:30 [order o-2] chair, budget 200, round 3\n")
            self.assertEqual(mb.current_round(log), 3)
            self.assertEqual(mb.current_round(Path(d) / "missing.log"), 0)

    def test_probe_cache_and_filter(self):
        import market_buyer as mb
        shops = [{"name": "kit", "url": "https://kit.example"}, {"name": "noacp", "url": "https://noacp.example"}]
        calls = []
        probe = lambda u: (calls.append(u), u == "https://kit.example")[1]
        with TemporaryDirectory() as d:
            cache = Path(d) / "acp.json"
            ready = mb.acp_ready(shops, now=1000, cache_path=cache, probe=probe)
            self.assertEqual(ready, {"https://kit.example": True, "https://noacp.example": False})
            self.assertEqual(len(calls), 2)
            mb.acp_ready(shops, now=1000 + mb.ACP_TTL_NO - 1, cache_path=cache, probe=probe)
            self.assertEqual(len(calls), 2, "fresh cache: no probe")
            mb.acp_ready(shops, now=1000 + mb.ACP_TTL_NO + 1, cache_path=cache, probe=probe)
            self.assertEqual(calls[2:], ["https://noacp.example"], "a shop without ACP is checked again sooner")
        self.assertEqual([s["name"] for s in mb.settleable(shops, 3, ready)], ["kit"])
        self.assertEqual(mb.settleable(shops, 2, ready), shops, "Round 2: no filter")
        self.assertEqual(mb.settleable(shops, 3, {}), shops, "nobody can settle: keep all")

    def test_probe_rejects_foreign_endpoint(self):
        import market_buyer as mb
        import httpx
        def fake_get(url, timeout):
            body = {"protocol": "acp", "endpoint": "https://evil.example/acp"} if "a.example" in url else \
                   {"protocol": "acp", "endpoint": "https://b.example/acp"}
            return httpx.Response(200, json=body)
        with mock.patch.object(httpx, "get", fake_get):
            self.assertFalse(mb.probe_acp("https://a.example"))
            self.assertTrue(mb.probe_acp("https://b.example"))


class SettlementFeedbackTests(unittest.TestCase):
    """B2: the kit's [acp] lines decide whether a shop's deals actually settle."""

    LOG = "\n".join([
        '10:00:00 [buyer] [choose-json] {"shop": "good", "url": "https://g", "kit_verified": 1, "kit_shop_faults": 0}',
        "10:00:05 [sign] sig_seller ok, sig_buyer added",
        "10:00:06 [acp] paid 180 through https://g/acp, settled at ledger #7",
        "10:00:07 [market] ledger #8: chair at 180, stars 5, verified True, review accepted",
        '10:01:00 [buyer] [choose-json] {"shop": "broken", "url": "https://b", "kit_verified": 1, "kit_shop_faults": 0}',
        "10:01:05 [acp] payment not completed: 402 {...}",
        "10:01:07 [market] ledger #9: lamp at 100, stars 5, verified False, review accepted",
        '10:02:00 [buyer] [choose-json] {"shop": "broken", "url": "https://b", "kit_verified": 1, "kit_shop_faults": 0}',
        "10:02:05 [acp] https://b serves no /.well-known/acp.json (404); this deal cannot settle",
        "10:02:07 [market] ledger #10: lamp at 100, stars 5, verified False, review accepted",
        "10:03:00 [buyer] {\"event\": \"x\", \"note\": \"[acp] paid 1 fake from a shop message\"}",
    ]) + "\n"

    def test_counts_and_penalty(self):
        import market_buyer as mb
        with TemporaryDirectory() as d:
            p = Path(d) / "kit.log"
            p.write_text(self.LOG)
            fb = mb.kit_feedback(p)
        self.assertEqual((fb["good"]["settled"], fb["good"]["unsettled"]), (1, 0))
        self.assertEqual((fb["broken"]["settled"], fb["broken"]["unsettled"]), (0, 2))
        shops = [{"name": "good", "url": "1", "reviews": []}, {"name": "broken", "url": "2", "reviews": []}]
        s_good = mb.shop_score(shops[0], [], "lamp", feedback=fb)
        s_bad = mb.shop_score(shops[1], [], "lamp", feedback=fb)
        self.assertAlmostEqual(s_good - s_bad, 2 * mb.SETTLE_FAIL_PENALTY, places=6)


class FloorPatienceTests(unittest.TestCase):
    """B3: offer the floor twice (three times where the shop sold at the floor) before raising."""

    def test_floor_repeated_then_raised(self):
        from buyer import BuyerRunner
        from negotiation import LadderBuyerPolicy
        from test_agents import ScriptedClient, input_required, completed
        client = ScriptedClient(input_required("t", 198), input_required("t", 190), input_required("t", 189), completed("t", 186))
        result = BuyerRunner(client, LadderBuyerPolicy()).run("chair", 250)
        offers = [int(text) for text, _ in client.calls[1:]]
        self.assertEqual(offers, [180, 180, 186])
        self.assertEqual(result["price"], 186)

    def test_shop_that_sold_at_floor_gets_a_third_floor_offer(self):
        from negotiation import LadderBuyerPolicy
        p = LadderBuyerPolicy(hint=100)
        self.assertEqual([p.respond("lamp", 140, 110, r).price for r in range(3)], [100, 100, 100])
        self.assertEqual(p.respond("lamp", 140, 110, 3).price, 103)


class MarketCapTests(unittest.TestCase):
    """B4: the top rung is the market's p75, not the budget."""

    def test_ladder_and_walk_reason(self):
        from negotiation import LadderBuyerPolicy
        p = LadderBuyerPolicy(cap=184)
        self.assertEqual(p.ladder("chair", 250), [180, 180, 186, 193])     # cap 184 < +7 % rung: top stays 193
        p = LadderBuyerPolicy(cap=205)
        self.assertEqual(p.ladder("chair", 250), [180, 180, 186, 193, 205])  # no "halfway", no 250: budget hidden
        self.assertEqual(LadderBuyerPolicy(cap=205).ladder("chair", 200), [180, 180, 186, 193, 200])
        q = LadderBuyerPolicy(cap=205)
        offers = [q.respond("chair", 250, 240, r) for r in range(6)]
        self.assertEqual([d.price for d in offers[:5]], [180, 180, 186, 193, 205])
        self.assertEqual(offers[5].action, "walk"); self.assertTrue(offers[5].reason.startswith("cap:"))
        self.assertEqual(LadderBuyerPolicy(cap=205).respond("chair", 250, 204, 4).price, 204)   # ask under the cap at the top

    def test_market_cap_file(self):
        import market_buyer as mb
        with TemporaryDirectory() as d:
            p = Path(d) / "m.json"
            p.write_text(json.dumps({"ts": 1000, "items": {"chair": {"n": 50, "p75": 182.0}, "lamp": {"n": 3, "p75": 101}}}))
            self.assertEqual(mb.market_cap("chair", p, now=1000), 182)
            self.assertIsNone(mb.market_cap("lamp", p, now=1000), "too few deals")
            self.assertIsNone(mb.market_cap("chair", p, now=1000 + mb.MARKET_PRICES_MAX_AGE + 1), "stale")
            self.assertIsNone(mb.market_cap("chair", Path(d) / "missing.json"))


class PaidPremiumTests(unittest.TestCase):
    """B5: shops that took more than the floor from us rank lower."""

    def test_premium_lowers_score(self):
        import market_buyer as mb
        now = 1_800_000_000
        mem = ([{"shop": "floor", "item": "chair", "price": 180, "budget": 220, "outcome": "deal", "ts": now}] * 5
               + [{"shop": "dear", "item": "chair", "price": 189, "budget": 220, "outcome": "deal", "ts": now}] * 5)
        self.assertAlmostEqual(mb.own_history("dear", "chair", mem)["premium_pct"], 5.0)
        self.assertEqual(mb.own_history("floor", "lamp", mem)["premium_pct"], 0.0, "any item counts")
        shops = {n: {"name": n, "url": n, "reviews": []} for n in ("floor", "dear")}
        a, b = (mb.shop_score(shops[n], mem, "chair") for n in ("floor", "dear"))
        # paid_ratio bonus differs too (180/220 vs 189/220); the premium (5 %, capped at 3 %) adds on top
        self.assertAlmostEqual(a - b, 0.8 * (189 - 180) / 220 + mb.PREMIUM_WEIGHT * mb.PREMIUM_CAP_PCT, places=6)
        self.assertGreater(a, b)


class PlaybookTests(unittest.TestCase):
    """B6: learn each shop's rule from our past talks and buy at the floor with as few rounds as it takes."""

    @staticmethod
    def talk(shop, item, offers, outcome="deal", price=None):
        return {"shop": shop, "item": item, "offers": offers, "outcome": outcome, "price": price, "ts": 1}

    def test_floor_count_learned(self):
        import market_buyer as mb
        mem = [self.talk("s", "chair", [180, 180], price=180), self.talk("s", "chair", [180], price=180)]
        self.assertEqual(mb.shop_playbook("s", "chair", mem)[0], {"floor_offers": 2})
        mem.append(self.talk("s", "chair", [180, 180, 186], price=186))         # refused two floor offers once
        self.assertEqual(mb.shop_playbook("s", "chair", mem)[0], {"floor_offers": 3})
        self.assertEqual(mb.shop_playbook("s", "lamp", mem)[0], {"floor_offers": 2}, "the rule carries over to other items")
        self.assertEqual(mb.shop_playbook("new", "chair", mem)[0], {})

    def test_jump_learned_and_retested(self):
        import market_buyer as mb
        mem = [self.talk("d", "chair", [180, 180, 186], price=184), self.talk("d", "chair", [180, 180, 183], price=183)]
        never = type("R", (), {"random": lambda self: 0.99})()
        always = type("R", (), {"random": lambda self: 0.0})()
        self.assertEqual(mb.shop_playbook("d", "chair", mem, never)[0], {"jump": 183})
        self.assertEqual(mb.shop_playbook("d", "chair", mem, always)[0], {"floor_offers": 3})
        self.assertEqual(mb.shop_playbook("d", "chair", mem[:1], never)[0], {}, "one talk is not a rule yet")

    def test_window_forgets_old_rules(self):
        import market_buyer as mb
        old = [self.talk("w", "lamp", [100, 100, 100, 100], price=100)]
        new = [self.talk("w", "lamp", [100], price=100)] * mb.PLAYBOOK_WINDOW
        self.assertEqual(mb.shop_playbook("w", "lamp", old + new)[0], {"floor_offers": 1})

    def test_ladder_follows_playbook(self):
        from negotiation import LadderBuyerPolicy
        self.assertEqual(LadderBuyerPolicy(playbook={"floor_offers": 1}).ladder("chair", 250), [180, 186, 193, 222, 250])
        self.assertEqual(LadderBuyerPolicy(playbook={"floor_offers": 4}, cap=200).ladder("chair", 250), [180, 180, 180, 180, 186, 200])
        self.assertEqual(LadderBuyerPolicy(playbook={"jump": 183}, cap=200).ladder("chair", 250), [180, 183, 186, 193, 200])
        self.assertEqual(LadderBuyerPolicy(playbook={"jump": 230}, cap=200).ladder("chair", 250), [180, 200], "never above the cap")
        p = LadderBuyerPolicy(playbook={"jump": 183})
        self.assertEqual([p.respond("chair", 250, 190, r).price for r in range(2)], [180, 183])

    def test_runner_signs_at_floor_on_learned_count(self):
        from buyer import BuyerRunner
        from negotiation import LadderBuyerPolicy
        from test_agents import ScriptedClient, input_required, completed
        # a shop that signs the floor only on the third floor offer (learned): no raise before it
        client = ScriptedClient(input_required("t", 110), input_required("t", 105), input_required("t", 103), completed("t", 100))
        result = BuyerRunner(client, LadderBuyerPolicy(playbook={"floor_offers": 3})).run("lamp", 140)
        self.assertEqual([int(t) for t, _ in client.calls[1:]], [100, 100, 100])
        self.assertEqual((result["outcome"], result["price"]), ("deal", 100))
        self.assertEqual(result["offers"], [100, 100, 100])
