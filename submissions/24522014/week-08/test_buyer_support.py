from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import buyer_support as support
import market_buyer
from market_state import read, write
from r3_policy import load_policy
from purchase_monitor import observe as observe_purchases

T = 1793760000
CFG = {**support.config(), "mode": "research", "max_attempts_per_utc_day": 8,
       "min_attempt_interval_seconds": 3600, "pair_cooldown_seconds": 21600,
       "min_catalogue_value": 30000, "alternative_tolerance_ratio": .02}
POLICY = load_policy()["buying"]
CAT = {"G1": {"kind": "goods", "list_price": 100000, "category": "goods"}}


def shop(name="other"):
    return {"name": name, "url": "https://shops/"+name, "reviews": [], "offers": [{"sku": "G1", "qty": 1}]}


def price(name="other", amount=70000, seq=1, **fields):
    return {"round": 3, "kind": "settle", "seq": seq, "ts": T-30,
            "data": {"shop": shop(name)["url"], "item": "G1", "grade": "A", "amount": amount, **fields}}


class SupportRules(unittest.TestCase):
    def setUp(self):
        settings_patch = patch.object(support, "config", return_value=CFG)
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = Path(self.tmp.name) / ".market"
        self.md.mkdir()
        self.ctx = {"targets": {"G1": "own_stock"}, "rival_names": {"leader"}, "rival_urls": {shop("leader")["url"]}}
        self.state = support.initial()

    def choose(self, shops=None, rows=None, ctx=None, memory=None, portfolio=None):
        return support.choose(shops or [shop()], memory or [], CAT, 100000, POLICY, portfolio or [],
                              rows or [], ctx or self.ctx, self.state, CFG, T)

    def test_relevant_unknown_price_uses_existing_uninsured_discount(self):
        selected, sku, cap, _, evidence = self.choose()
        self.assertEqual(selected["name"], "other")
        self.assertEqual(evidence[0]["cap_without_floor"], 80000)
        self.assertLess(cap, 80000)
        self.assertTrue(evidence[0]["support_eligible"])

    def test_unrelated_and_already_observed_public_pairs_skip(self):
        self.assertIsNone(self.choose(ctx={**self.ctx, "targets": {}})[0])
        rows = [price(seq=i) for i in (1, 2, 3)]
        self.assertIsNone(self.choose(rows=rows)[0])
        self.assertIsNotNone(self.choose(rows=[rows[0], rows[0], rows[0]])[0])

    def test_public_conditions_not_mixed_and_invalid_prices_do_not_count(self):
        rows = [price(seq=1), price(seq=2, grade="B"), price(seq=3, warranty_hours=6)]
        self.assertIsNotNone(self.choose(rows=rows)[0])
        self.assertIsNotNone(self.choose(rows=[price(seq=i, amount=-1) for i in (1, 2, 3)])[0])

    def test_known_overpriced_reference_does_not_trigger_bargaining(self):
        selected, _, _, _, evidence = self.choose(rows=[price(amount=90000)])
        self.assertIsNone(selected)
        self.assertEqual(evidence[0]["support_skip_reason"], "known_price_above_value_cap")

    def test_reference_caps_spend_and_preserves_user_budget(self):
        self.assertEqual(self.choose(rows=[price(amount=70000)])[2], 71400)
        result = support.choose([shop()], [], CAT, 60000, POLICY, [], [], self.ctx, self.state, CFG, T)
        self.assertEqual(result[2], 60000)

    def test_comparable_nonleader_preferred_but_not_overpriced_or_unknown(self):
        shops = [shop("leader"), shop("other")]
        rows = [price("leader", 70000), price("other", 71000, seq=2)]
        self.assertEqual(self.choose(shops, rows)[0]["name"], "other")
        rows[1] = price("other", 75000, seq=2)
        self.assertEqual(self.choose(shops, rows)[0]["name"], "leader")
        self.assertEqual(self.choose(shops, rows[:1])[0]["name"], "leader")

    def test_rival_avoidance_cannot_mix_observed_grade_or_warranty(self):
        shops = [shop("leader"), shop("other")]
        rows = [price("leader", 70000, warranty_hours=6), price("other", 71000, seq=2)]
        self.assertEqual(self.choose(shops, rows)[0]["name"], "leader")
        rows = [price("leader", 70000), price("other", 71000, seq=2, grade="B")]
        self.assertEqual(self.choose(shops, rows)[0]["name"], "leader")

    def test_settled_own_recent_information_skips_without_using_r2(self):
        record = {"round": 3, "shop_name": "other", "item": "G1", "outcome": "purchased", "price": 70000,
                  "verification": "market_settled", "timestamp": datetime.fromtimestamp(T-60, timezone.utc).isoformat()}
        self.assertIsNone(self.choose(memory=[record])[0])
        self.assertIsNotNone(self.choose(memory=[{**record, "round": 2}])[0])

    def test_risk_guards_and_warranty_condition_preserved(self):
        portfolio = [{"shop": shop()["url"], "sku": "G1", "fault": True}]
        self.assertIsNone(self.choose(portfolio=portfolio)[0])
        result = self.choose(portfolio=[{**portfolio[0], "warranty_hours": 6}])
        self.assertEqual(result[4][0]["warranty_required"], 6)
        self.assertEqual(result[4][0]["cap_without_floor"], 95000)
        self.assertLess(result[2], 80000)
        records = [{"round": 3, "shop_name": "other", "outcome": "receipt_mismatch"}]
        self.assertIsNone(self.choose(memory=records)[0])
        self.assertIsNone(self.choose(shops=[shop("kcwarni")])[0])
        self.assertIsNone(self.choose(shops=[{**shop(), "owner": {"login": "kcwarni"}}])[0])

    def test_reservation_enforces_duplicate_hour_pair_and_daily_limits(self):
        def reserve(order, at, sku="G1"):
            return support.reserve(self.md, order, shop(), sku, 80000, False, CFG, at)
        self.assertEqual(reserve("o1", T), "reserved")
        self.assertEqual(reserve("o1", T+3600), "duplicate_order")
        self.assertEqual(reserve("o2", T+60), "attempt_cooldown")
        self.assertEqual(reserve("o2", T+3600), "pair_attempt_cooldown")
        for i in range(1, 8):
            self.assertEqual(reserve(f"o{i+1}", T+i*3600, f"G{i+1}"), "reserved")
        self.assertEqual(reserve("o9", T+8*3600, "G9"), "daily_attempt_limit")

    def test_utc_day_reset_keeps_hourly_guard(self):
        midnight = datetime(2026, 11, 5, tzinfo=timezone.utc).timestamp()
        support.reserve(self.md, "old", shop(), "G1", 80000, False, CFG, midnight-60)
        state = read(self.md / "buyer-support-state.json")
        self.assertEqual(support.availability(state, "new", CFG, midnight+60), "attempt_cooldown")
        self.assertEqual(support.availability(state, "new", CFG, midnight+3600), "available")

    def test_stale_rival_report_not_used_and_targets_must_be_goods(self):
        path = self.md / "inventory.json"
        write(path, {"round": 3, "units": [{"sku": "G1"}]}); os.utime(path, (T, T))
        write(self.md / "leader-intel.json", {"round": 3, "basis": "shop_profit", "status": "ok", "observed_at": T-3601,
              "leaders": [{"login": "leader", "shop": {"name": "leader", "url": shop("leader")["url"]}}]})
        ctx = support.context(self.md, CAT, CFG, T)
        self.assertFalse(ctx["rival_names"])
        self.assertEqual(ctx["targets"], {"G1": "own_stock"})
        self.assertFalse(support.context(self.md, {"G1": {**CAT["G1"], "kind": "food"}}, CFG, T)["targets"])

    def test_paid_research_rows_and_report_require_matching_payment(self):
        support.reserve(self.md, "o", shop(), "G1", 80000, True, CFG, T)
        write(self.md / "buyer-deals/12.json", {"ledger": 12, "order": "o", "shop": shop()["url"], "sku": "G1",
              "purchased_at": T, "price": 70000, "grade": "A", "unit": "u1"})
        payment = {"order": "o", "shop": shop()["url"], "item": "G1", "price": 70000, "ledger": 12, "paid": True}
        path = self.md / "payments.jsonl"
        path.write_text(json.dumps(payment)+"\n", encoding="utf8")
        rows = support.research_rows(self.md, T+10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["data"]["shop"], shop()["url"])
        self.assertEqual(support.report(self.md)["buyer_paid_to_profit_leaders_at_selection"], 70000)
        path.write_text(json.dumps({**payment, "paid": False})+"\n", encoding="utf8")
        self.assertEqual(support.research_rows(self.md, T+10), [])
        self.assertEqual(support.report(self.md)["confirmed_paid_deals"], 0)

    def test_research_price_supplements_external_comparison_without_double_count(self):
        support.reserve(self.md, "o", shop(), "G1", 80000, False, CFG, T)
        write(self.md / "buyer-deals/12.json", {"ledger": 12, "order": "o", "shop": shop()["url"], "sku": "G1",
              "purchased_at": T, "price": 70000, "grade": "A", "unit": "u1"})
        payment = {"order": "o", "shop": shop()["url"], "item": "G1", "price": 70000, "ledger": 12, "paid": True}
        (self.md / "payments.jsonl").write_text(json.dumps(payment)+"\n", encoding="utf8")
        write(self.md / "catalog.json", CAT)
        write(self.md / "inventory.json", {"units": []})
        snap = {"round": 3, "observed_at": T, "rows": [price(seq=13)]}
        first = observe_purchases(self.md, now=T+10, force=True, snapshot=snap)
        self.assertEqual(first["summary"]["settled_count"], 2)
        snap["rows"].append({**price(seq=12), "ts": T-1})
        second = observe_purchases(self.md, now=T+10, force=True, snapshot=snap)
        self.assertEqual(second["summary"]["settled_count"], 2)

    def test_irrelevant_orders_skip_portfolio_scan_and_model(self):
        self.cli_inputs()
        with patch("sys.argv", self.args), patch.object(market_buyer, "ROOT", self.md.parent), \
                patch("buyer_portfolio.reconcile") as portfolio, patch.object(market_buyer, "trade") as trade, redirect_stdout(io.StringIO()):
            market_buyer.main()
        portfolio.assert_not_called()
        trade.assert_not_called()

    def test_cli_skip_never_calls_trade_and_outputs_valid_no_deal(self):
        self.cli_inputs()
        with patch("sys.argv", self.args), patch.object(market_buyer, "ROOT", self.md.parent), \
                patch.object(market_buyer, "trade") as trade, redirect_stdout(io.StringIO()) as output:
            market_buyer.main()
        trade.assert_not_called()
        self.assertIsNone(json.loads(output.getvalue().splitlines()[-1])["receipt"])

    def cli_inputs(self):
        for name, data in {"shops.json": [shop()], "catalog.json": CAT, "order.json": {
             "order": "o1", "round": 3, "item": "goods", "kind": "goods", "budget": 100000, "acceptable": ["G1"]}}.items():
            write(self.md / name, data)
        self.args = ["buyer", "--shops", str(self.md/"shops.json"), "--order", str(self.md/"order.json"),
                     "--item", "goods", "--budget", "100000"]

    def test_cli_research_limits_turns_and_retains_payment_accept_conditions(self):
        self.cli_inputs()
        write(self.md / "inventory.json", {"round": 3, "units": [{"sku": "G1"}]})
        result = {"shop": shop()["url"], "receipt": None, "stars": 3, "review": "fixture"}
        record = {"outcome": "no_deal", "timestamp": datetime.now(timezone.utc).isoformat(), "shop_name": "other", "item": "G1"}
        with patch("sys.argv", self.args), patch.object(market_buyer, "ROOT", self.md.parent), \
                patch.object(market_buyer, "trade", return_value=(result, record)) as trade, redirect_stdout(io.StringIO()) as output:
            market_buyer.main()
        self.assertEqual(trade.call_args.kwargs["max_turns"], 3)
        accept = json.loads(output.getvalue().splitlines()[-1])["accept"]
        self.assertEqual(accept["grades"], ["A", "B"])
        self.assertTrue(accept["cert"])


if __name__ == "__main__":
    unittest.main()
