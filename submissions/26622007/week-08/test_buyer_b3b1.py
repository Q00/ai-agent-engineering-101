import collections, json, os, tempfile, time, unittest

import buyer_b3b1
import market_buyer


def offer(ask, price):
    return {"shop_price": ask, "action": "offer", "price": price}


class SwitchTest(unittest.TestCase):
    def test_off_unless_the_switch_file_says_on(self):
        with tempfile.TemporaryDirectory() as md:
            self.assertFalse(buyer_b3b1.enabled(md, env={}))
            path = os.path.join(md, buyer_b3b1.SWITCH)
            for body, want in (('{"enabled": true}', True), ('{"enabled": false}', False), ('{"enabled": 1}', False),
                               ("not json", False), ("[]", False)):
                with open(path, "w") as f:
                    f.write(body)
                self.assertIs(buyer_b3b1.enabled(md, env={}), want, body)
            with open(path, "w") as f:
                f.write('{"enabled": true}')
            self.assertFalse(buyer_b3b1.enabled(md, env={"BUYER_B3B1": "0"}))   # the environment off wins


class B3Test(unittest.TestCase):
    def test_one_more_counter_at_92_of_the_first_ask_instead_of_taking_the_counter(self):
        history = [("shop asked", 1000), ("you offered", 900)]
        out, note = buyer_b3b1.b3_turn(offer(960, 960), history, limit=2000)
        self.assertEqual(out, offer(960, 920))
        self.assertEqual(note, {"applied": True, "first": 1000, "baseline": 960, "price": 920})

    def test_limit_bounds_the_extra_counter(self):
        out, _ = buyer_b3b1.b3_turn(offer(1100, 905), [("shop asked", 1000), ("you offered", 900)], limit=905)
        self.assertEqual(out["price"], 905)                       # baseline already at the limit: nothing to lower
        out, note = buyer_b3b1.b3_turn(offer(1100, 950), [("shop asked", 1000), ("you offered", 900)], limit=950)
        self.assertEqual((out["price"], note["applied"]), (920, True))

    def test_counter_at_or_under_92_is_taken_as_before(self):
        base = offer(915, 915)
        out, note = buyer_b3b1.b3_turn(base, [("shop asked", 1000), ("you offered", 900)], limit=2000)
        self.assertEqual((out, note), (base, None))

    def test_only_after_exactly_one_offer_and_only_offers(self):
        h2 = [("shop asked", 1000), ("you offered", 900), ("shop asked", 960), ("you offered", 920)]
        for base, history in ((offer(950, 950), h2), (offer(1000, 900), [("shop asked", 1000)]),
                              ({"shop_price": 960, "action": "walk_away", "price": 0}, h2[:2]),
                              (offer(960, 960), [("you offered", 900)]), (None, h2[:2])):
            self.assertEqual(buyer_b3b1.b3_turn(base, history, limit=2000), (base, None))

    def test_probe_openings_keep_their_own_ladder(self):
        base = offer(960, 960)
        self.assertEqual(buyer_b3b1.b3_turn(base, [("shop asked", 1000), ("you offered", 1)], 2000, probe=True), (base, None))

    def test_malformed_rows_do_not_break_rates(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as md:
            B1Test.write(None, md, [{"ts": now, "order": ["x"], "shop": "a", "passed_to_kit": True},
                                    {"ts": True, "order": "y", "shop": "a", "passed_to_kit": True}, [1]],
                         [{"order": "y", "paid": True}, {"order": ["x"], "paid": True}])
            self.assertEqual(buyer_b3b1.paid_rates(md, now=now), ({}, None))

    def test_never_below_or_at_my_last_offer(self):
        base = offer(930, 930)
        out, note = buyer_b3b1.b3_turn(base, [("shop asked", 1000), ("you offered", 925)], limit=2000)
        self.assertEqual((out, note), (base, None))

    def test_next_shop_counter_is_taken_by_oneshot_after_the_extra_offer(self):
        import oneshot
        history = [("shop asked", 1000), ("you offered", 900), ("shop asked", 960), ("you offered", 920)]
        self.assertEqual(oneshot.code_turn(940, history, 2000, (.9,)), offer(940, 940))


class B1Test(unittest.TestCase):
    def write(self, md, rows, pays):
        os.makedirs(os.path.join(md, "measure"))
        with open(os.path.join(md, "measure", "orders.jsonl"), "w") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)
        with open(os.path.join(md, "payments.jsonl"), "w") as f:
            f.writelines(json.dumps(p) + "\n" for p in pays)

    def test_paid_share_per_shop_with_shrinkage_to_the_overall_share(self):
        now = time.time()
        rows, pays = [], []
        for i in range(10):
            rows.append({"ts": now - 60, "order": f"a{i}", "shop": "good", "passed_to_kit": True})
            pays.append({"order": f"a{i}", "paid": True})
            rows.append({"ts": now - 60, "order": f"b{i}", "shop": "bad", "passed_to_kit": True})
            pays.append({"order": f"b{i}", "paid": i < 5})
        rows.append({"ts": now - 60, "order": "c0", "shop": "few", "passed_to_kit": True})
        pays.append({"order": "c0", "paid": False})
        rows.append({"ts": now - 60, "order": "d0", "shop": "good", "passed_to_kit": False})
        rows.append({"ts": now - 60, "order": "p0", "shop": "bad", "passed_to_kit": True})    # no payment line yet: skipped
        rows.append({"ts": now - 9 * 3600, "order": "old", "shop": "bad", "passed_to_kit": True})
        pays.append({"order": "old", "paid": True})
        with tempfile.TemporaryDirectory() as md:
            self.write(md, rows, pays)
            rates, prior = buyer_b3b1.paid_rates(md, now=now)
        self.assertAlmostEqual(prior, 15 / 21)
        self.assertAlmostEqual(rates["good"], (10 + 2 * prior) / 12)
        self.assertAlmostEqual(rates["bad"], (5 + 2 * prior) / 12)
        self.assertNotIn("few", rates)                       # under three settled tries: the prior
        self.assertEqual(buyer_b3b1.paid_rates(md + "/none", now=now), ({}, None))

    def test_quote_order_ranks_fitters_by_yield_times_paid_rate_and_keeps_the_fresh_slot(self):
        shop = lambda n, r: {"name": n, "reviews": [0] * r}
        others = [shop("new", 9), shop("fit1", 1), shop("old", 5), shop("fit2", 3)]
        fits = collections.Counter({"fit1": 52, "fit2": 1})
        base = [s["name"] for s in market_buyer.quote_order(others, fits, 3)]
        self.assertEqual(base, ["fit2", "new", "fit1", "old"])
        ranked = [s["name"] for s in market_buyer.quote_order(others, fits, 3, rates=({"fit1": .99, "fit2": .5}, .8))]
        self.assertEqual(ranked, ["fit1", "new", "fit2", "old"])
        ranked = [s["name"] for s in market_buyer.quote_order(others, fits, 3, yields={"fit1": .05, "fit2": .2},
                                                              rates=({"fit1": .99, "fit2": .5}, .8))]
        self.assertEqual(ranked, ["fit2", "new", "fit1", "old"])  # .2*.5 = .10 > .05*.99
        unknown = [s["name"] for s in market_buyer.quote_order(others, fits, 3, rates=({"fit2": .9}, .5))]
        self.assertEqual(unknown, ["fit2", "new", "fit1", "old"])  # a shop with no rate takes the prior


class LadderTest(unittest.TestCase):
    def ladder(self, order):
        import ast, inspect
        from types import SimpleNamespace
        node = next(n for n in ast.walk(ast.parse(inspect.getsource(market_buyer.main)))
                    if isinstance(n, ast.FunctionDef) and n.name == "price_ladder")
        ns = {**vars(market_buyer), "discount_arm": None, "probe_arm": None, "goods_first_arm": None, "limit": 2000,
              "steps": (.9,), "q": {"ask": 1000}, "resume": True, "order": order, "args": SimpleNamespace(budget=3000),
              "shop": {"name": "fixture"}, "key": "G12345678", "r3": False}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "<ladder>", "exec"), ns)
        return ns["price_ladder"]

    def test_the_real_ladder_makes_the_extra_offer_only_when_on(self):
        history = [("shop asked", 1000), ("you offered", 900)]
        on = {"kind": "goods", "_b3": {"applied": False}}
        self.assertEqual(self.ladder(on)(960, "", history), offer(960, 920))
        self.assertEqual(on["_b3"]["price"], 920)
        probe = {"kind": "food", "_b3": {"applied": False}, "_one_won_default": True}
        self.assertEqual(self.ladder(probe)(960, "", history), offer(960, 960))
        off = {"kind": "goods"}
        self.assertEqual(self.ladder(off)(960, "", history), offer(960, 960))
        self.assertEqual(self.ladder(on)(900 + 40, "", history + [("shop asked", 960), ("you offered", 920)]),
                         offer(940, 940))


if __name__ == "__main__":
    unittest.main()
