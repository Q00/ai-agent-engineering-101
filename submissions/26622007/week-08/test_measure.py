"""measure.py: the Round 3 records GPT Pro §5 asked for (review/W-20261104-1507), and that they never stop a run."""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import harness
import market_buyer
import measure


def lines(md, name):
    p = os.path.join(md, "measure", name + ".jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f]


def unit(uid, sku, cost=100, supplied=1000, expires=None, kind="goods", grade="A"):
    return {"unit": uid, "sku": sku, "name": sku, "kind": kind, "grade": grade, "cost": cost, "list_price": 200,
            "supplied_at": supplied, "expires_at": expires}


def inv(units, opens=2000, closes=2180, open_=False):
    return {"round": 3, "units": units, "restock": {"open": open_, "opens_at": opens, "closes_at": closes}}


class MeasureTest(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.md)

    def tick(self, i, now, sent=(), recs=(), rows=(), pricing=None):
        measure.tick(self.md, i, sent, pricing or {}, now=now, sold_records=list(recs), rows=list(rows))

    def test_a_failing_part_does_not_repeat_the_others_next_minute(self):
        with mock.patch.object(measure, "_forum", side_effect=RuntimeError("boom")):
            self.tick(inv([unit("u1", "S1")]), 1500, sent=[{"sku": None, "qty": 1}, {"sku": "S1", "qty": 1}])
            self.tick(inv([unit("u1", "S1")]), 1560)
        self.assertEqual([x["unit"] for x in lines(self.md, "units_in")], ["u1"])
        self.assertEqual(len(lines(self.md, "stock")), 1)

    def test_units_in_out_and_stock_out(self):
        self.tick(inv([unit("u1", "S1"), unit("u2", "S1")]), 1500)
        sold = [{"unit": "u1", "result": "deal", "price": 180, "warranty": 6, "cost": 100}]
        self.tick(inv([unit("u2", "S1")]), 1560, recs=sold)
        self.tick(inv([]), 1620)
        self.assertEqual([u["unit"] for u in lines(self.md, "units_in")], ["u1", "u2"])
        out = lines(self.md, "units_out")
        self.assertEqual([(u["unit"], u["how"], u["price"]) for u in out], [("u1", "sold", 180), ("u2", "gone", None)])
        self.assertEqual([(s["qty"], s["was"]) for s in lines(self.md, "stock")], [(2, None), (1, 2), (0, 1)])

    def test_expired_food_is_told_from_a_sale(self):
        self.tick(inv([unit("f1", "F", kind="food", expires=1550)]), 1500)
        self.tick(inv([]), 1560)
        self.assertEqual(lines(self.md, "units_out")[0]["how"], "expired")

    def test_supply_window_target_sent_supplied(self):
        p = {"restock": {"sku_target_units": {"S1": 3}}}
        self.tick(inv([], 2000, 2180), 1990, pricing=p)                    # first pass: stock there is not a supply
        self.tick(inv([], 2000, 2180, True), 2010, sent=[{"sku": "S1", "qty": 3}, {"sku": "S2", "qty": 1}], pricing=p)
        self.tick(inv([unit("a", "S1", supplied=2020), unit("b", "S1", supplied=2021)], 2000, 2180), 2030, pricing=p)
        self.tick(inv([unit("a", "S1", supplied=2020), unit("b", "S1", supplied=2021)], 3200, 3380), 3000, pricing=p)
        w = lines(self.md, "supply_windows")
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0]["lines"], [{"sku": "S1", "target": 3, "sent": 3, "supplied": 2},
                                         {"sku": "S2", "target": None, "sent": 1, "supplied": 0}])
        self.assertEqual(lines(self.md, "supply_sent")[0]["window_open"], True)

    def test_sales_after_six_hours_with_refunded_break(self):
        with open(os.path.join(self.md, "loss_state.json"), "w") as f:
            json.dump({"mine": {"10": {"unit": "u1", "amount": 300}, "11": {"unit": "u2", "amount": 200},
                                "12": {"unit": "u3", "amount": 250}}}, f)
        rows = [{"k": "settle", "seq": 10, "ts": 100, "order": "o1", "sku": "S", "amount": 300, "grade": "C", "warranty": 6.0, "buyer": "b"},
                {"k": "broken", "seq": 13, "ts": 3700, "order": "o1", "sku": "S", "buyer": "b"},
                {"k": "settle", "seq": 11, "ts": 100, "order": "o2", "sku": "S", "amount": 200, "grade": "A", "warranty": 6.0, "buyer": "c"},
                {"k": "settle", "seq": 12, "ts": 9 * 3600, "order": "o3", "sku": "S", "amount": 250, "grade": "A", "buyer": "d"}]
        recs = [{"unit": "u1", "result": "deal", "cost": 120}, {"unit": "u2", "result": "deal", "cost": 150}]
        self.tick(inv([]), 10 * 3600, recs=recs, rows=rows)
        self.tick(inv([]), 20 * 3600, recs=recs, rows=rows)                 # each sale once; seq 12 ages in here
        s = {x["seq"]: x for x in lines(self.md, "sales_6h")}
        self.assertEqual(sorted(s), [10, 11, 12])
        self.assertEqual((s[10]["broke"], s[10]["refunded"], s[10]["net"]), (True, True, -120))
        self.assertEqual((s[11]["broke"], s[11]["net"]), (False, 50))
        self.assertIsNone(s[12]["net"])                                        # cost unknown: no guess

    def test_a_pass_that_skips_the_sales_leaves_them_and_their_clock_to_the_next(self):
        # restock_window_fast's run inside a supply window: no read of the ledger rows file, the next pass does them
        with open(os.path.join(self.md, "loss_state.json"), "w") as f:
            json.dump({"mine": {"10": {"unit": "u1", "amount": 300}}}, f)
        rows = [{"k": "settle", "seq": 10, "ts": 100, "order": "o1", "sku": "S", "amount": 300, "grade": "A", "warranty": 6.0, "buyer": "b"}]
        recs = [{"unit": "u1", "result": "deal", "cost": 120}]
        with mock.patch("market_model.load_rows", side_effect=AssertionError("the rows file")):
            measure.tick(self.md, inv([]), [{"sku": "S", "qty": 2}], {}, now=10 * 3600, sold_records=recs, skip_sales=True)
        self.assertEqual(lines(self.md, "sales_6h"), [])
        self.assertEqual(lines(self.md, "supply_sent")[-1]["lines"], [{"sku": "S", "qty": 2}])     # the run's lines are kept
        self.tick(inv([]), 10 * 3600 + 60, recs=recs, rows=rows)                # a minute later, not SALES_EVERY later
        self.assertEqual([x["seq"] for x in lines(self.md, "sales_6h")], [10])

    def test_the_window_arm_is_kept_on_the_sent_line_only_when_the_study_is_on(self):
        measure.tick(self.md, inv([]), [{"sku": "S", "qty": 1}], {}, now=1000, sold_records=[], rows=[])
        measure.tick(self.md, inv([]), [{"sku": "S", "qty": 1}], {}, now=1060, sold_records=[], rows=[], window_arm="B-fast")
        measure.tick(self.md, inv([]), [{"sku": "S", "qty": 1}], {}, now=1120, sold_records=[], rows=[], window_arm="")
        got = lines(self.md, "supply_sent")
        self.assertNotIn("window_fast", got[0])                                # off: the line as it was
        self.assertEqual(got[1]["window_fast"], "B-fast")
        self.assertNotIn("window_fast", got[2])
        self.assertEqual({k for k in got[0]}, {"ts", "window_open", "opens_at", "lines"})

    def test_forum_follow_ups(self):
        with open(os.path.join(self.md, "forum.json"), "w") as f:
            json.dump([{"id": 7, "ts": 1000, "author": "nocha12", "role": "buyer", "likes": 3, "dislikes": 0, "score": 0.4},
                       {"id": 8, "ts": 1000, "author": "other", "likes": 9}], f)
        with mock.patch.dict(os.environ, {"SHOP_MARKET_NAME": "nocha12"}):
            self.tick(inv([]), 1700)
            with open(os.path.join(self.md, "forum.json"), "w") as f:
                json.dump([], f)                                               # gone from the market's latest 100
            self.tick(inv([]), 1000 + 3700)
            self.tick(inv([]), 1000 + 6 * 3600 + 5)
        got = [(x["post"], x["at"], x["listed"], x.get("likes")) for x in lines(self.md, "forum_followup")]
        self.assertEqual(got, [(7, "10m", True, 3), (7, "1h", False, None), (7, "6h", False, None)])
        self.assertEqual(json.load(open(os.path.join(self.md, "measure", "state.json")))["posts"], {})

    def test_never_raises(self):
        measure.tick(self.md, {"round": 3, "units": ["x", None, {"unit": "u", "sku": "S"}], "restock": "bad"}, ["x"], {},
                     sold_records=[None, "x"], rows=[])
        measure.tick(self.md, {"round": 2}, sold_records=[])
        with open(os.path.join(self.md, "measure", "state.json"), "w") as f:
            f.write("[not a dict")
        measure.tick(self.md, inv([]), sold_records=[], rows=[])
        measure.write("/nonexistent/x/y", "z", {})


class OrderRecordTest(unittest.TestCase):
    def test_quotes_say_why_each_lost(self):
        shops = [{"name": "A", "usable": [{"sku": "S", "qty": 1, "list_price": 1000}], "_offer": {"sku": "S", "list_price": 1000}},
                 {"name": "B", "usable": [{"sku": "S", "qty": 2, "list_price": 1000}], "_offer": {"sku": "S", "list_price": 1000}},
                 {"name": "C", "usable": [{"sku": "S", "qty": 1, "list_price": 1000}], "_offer": {"sku": "S", "list_price": 1000}}]
        quotes = {"A": {"ask": 700}, "B": {"ask": None}, "C": {"ask": 900, "refused": "the shop offers only a 0 h warranty"}}
        args = mock.Mock(budget=800)
        out = {"state": "TASK_STATE_COMPLETED", "history": [("shop asked", 700), ("I offered", 600), ("shop asked", 650)],
               "agreed": 650, "holds": 0}
        rec = {"price": 650, "unit": "u9", "claim": "B", "warranty_hours": 6}
        with mock.patch.dict(harness.CALLS, {"attempts": 3, "ok": 3, "cap": 0}):
            r = market_buyer.order_record({"order": "o1", "kind": "goods", "acceptable": ["S"]}, args, shops, quotes, "B",
                                          "explore agent: x", shops[0], "best quote of 3 (700); first choice was B",
                                          shops[0]["_offer"], 800, 0.05, out, rec, {"receipt": rec}, [], [], None, None,
                                          {"choose": market_buyer.STARTED + 5}, {"S": {"shelf_hours": None}})
        self.assertEqual([(q["shop"], q["lost"]) for q in r["quotes"]],
                         [("A", None), ("B", "no ask (no free unit or no answer)"), ("C", "refused: the shop offers only a 0 h warranty")])
        self.assertEqual((r["how"], r["first_choice"], r["shop"], r["counters"], r["price"], r["luna"]["attempts"]),
                         ("agent", "B", "A", 2, 650, 3))
        self.assertEqual(r["seconds"]["choose"], 5)
        json.dumps(r)

    def test_choose_path(self):
        self.assertEqual([market_buyer.choose_path(x) for x in ("harness failed (x); most reviewed", "gem agent: cheap",
                                                                "explore: the only shop in the pool", "all: picked")],
                         ["code_fallback", "agent", "code_single", "single_ask"])


class CapTest(unittest.TestCase):
    def test_cap_refusal_is_recorded(self):
        md = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, md)
        import time
        with mock.patch.dict(os.environ, {"MARKET_DIR": md, "OPENROUTER_API_KEY": "x"}), \
                mock.patch.object(harness, "OR_MAX_CALLS_PER_HOUR", 1), mock.patch.object(harness, "_or_calls", [time.time()]):
            with self.assertRaises(harness.HarnessError):
                harness._ask_openrouter("p", {}, "s", "buyer-agent")
        self.assertEqual([x["tag"] for x in lines(md, "llm_cap")], ["buyer-agent"])

    def test_no_cap_by_default(self):
        """No OR_MAX_CALLS_PER_HOUR: the cap is 0, and 0 refuses nothing however many calls the last hour made."""
        import subprocess
        import sys
        import time
        env = {k: v for k, v in os.environ.items() if k != "OR_MAX_CALLS_PER_HOUR"}
        out = subprocess.run([sys.executable, "-c", "import harness; print(harness.OR_MAX_CALLS_PER_HOUR)"],
                             cwd=os.path.dirname(os.path.abspath(__file__)), env=env, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "0", out.stderr)
        md = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, md)
        ok = mock.Mock(status_code=200)
        ok.json.return_value = {"choices": [{"message": {"content": "{}"}}]}
        client = mock.MagicMock()
        client.__enter__.return_value.post.return_value = ok
        with mock.patch.dict(os.environ, {"MARKET_DIR": md, "OPENROUTER_API_KEY": "x"}), \
                mock.patch.object(harness, "OR_MAX_CALLS_PER_HOUR", 0), mock.patch.object(harness, "_or_calls", [time.time()] * 1000), \
                mock.patch.object(harness.httpx, "Client", return_value=client):
            self.assertEqual(harness._ask_openrouter("p", {}, "s", "seller-harness"), {})
        self.assertEqual(lines(md, "llm_cap"), [])


class TryTimeoutTest(unittest.TestCase):
    """The buyer's offers get TRY_SECONDS per OpenRouter try and TRY_RETRIES tries: a slow answer (30-60 s for 30 of 258
    offers, 11/06 17:50-19:50) is asked again instead of holding the order; other tags keep one try with the whole budget."""

    def run_tag(self, tag, answers):
        md = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, md)
        client = mock.MagicMock()
        post = client.__enter__.return_value.post
        post.side_effect = answers
        with mock.patch.dict(os.environ, {"MARKET_DIR": md, "OPENROUTER_API_KEY": "x"}), \
                mock.patch.object(harness, "OR_MAX_CALLS_PER_HOUR", 0), mock.patch.object(harness.httpx, "Client", return_value=client):
            try:
                out = harness._ask_openrouter("p", {}, "s", tag)
            except harness.HarnessError:
                out = None
        return out, [c.kwargs["timeout"] for c in post.call_args_list]

    def test_buyer_offers_retry_a_slow_try(self):
        ok = mock.Mock(status_code=200)
        ok.json.return_value = {"choices": [{"message": {"content": "{}"}}]}
        slow = harness.httpx.ReadTimeout("slow")
        out, timeouts = self.run_tag("buyer-harness", [slow, slow, ok])
        self.assertEqual(out, {})
        self.assertEqual(len(timeouts), 3)
        self.assertTrue(all(t <= harness.TRY_SECONDS["buyer-harness"] for t in timeouts), timeouts)

    def test_other_tags_keep_one_long_try(self):
        slow = harness.httpx.ReadTimeout("slow")
        out, timeouts = self.run_tag("seller-harness", [slow, slow, slow])
        self.assertIsNone(out)
        self.assertGreater(timeouts[0], harness.TRY_SECONDS["buyer-harness"])
        self.assertLessEqual(len(timeouts), harness.RETRIES)


class EffortTest(unittest.TestCase):
    """The buyer's offers go out at reasoning effort none, every other tag at OR_REASONING (low): 80 logged offer turns
    replayed 11/07 03:40 took a median 1.22 s at none against 4.19 s at low, with the same action in 80 of 80."""

    def effort(self, tag):
        client = mock.MagicMock()
        ok = mock.Mock(status_code=200)
        ok.json.return_value = {"choices": [{"message": {"content": "{}"}}]}
        post = client.__enter__.return_value.post
        post.return_value = ok
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "x"}), mock.patch.object(harness, "OR_MAX_CALLS_PER_HOUR", 0), \
                mock.patch.object(harness.httpx, "Client", return_value=client):
            harness._ask_openrouter("p", {}, "s", tag)
        return post.call_args.kwargs["json"]["reasoning"]["effort"]

    def test_buyer_offers_use_none(self):
        self.assertEqual(self.effort("buyer-harness"), "none")

    def test_the_seller_and_the_agents_keep_or_reasoning(self):
        for tag in ("seller-harness", "seller-jev", "buyer-agent", "buyer-choose", "forum-agent"):
            self.assertEqual(self.effort(tag), harness.OR_REASONING, tag)


if __name__ == "__main__":
    unittest.main()
