"""A dry run of the rule patch the course is likely to ship next: storage far above 1% a day and Book no longer
counting unsold stock at cost. No model call: the test follows the patch through facts (parsed values, alerts,
the diff), the brief (economics.capital and holding) and the prompt (the rule changes, the patch limits, the
reverse-order instruction), and then the guard's wider step on the stock-posture keys."""
import copy
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-dryrun-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import policy  # noqa: E402
from strategist import analyze, decide, facts, guard  # noqa: E402
from strategist.tests import test_analyze as ta  # noqa: E402

FIXTURE = (Path(__file__).resolve().parent / "fixture_agent.md").read_text(encoding="utf-8")
NOW = ta.NOW
PATCHED = re.sub(r"[Bb]ook = cash \+ what the unsold units cost", "Book = cash + half of what the unsold units cost",
                 FIXTURE.replace("1% of their cost per day in storage", "12% of their cost per day in storage"))


def market_of(text, old=None, now=NOW):
    """facts.collect's market dict as the run sees it, from a text (and the previous text for the diff)."""
    sec = facts.section(text, 3)[0]
    f, alerts = facts.parse(sec)
    m = {"sha": facts.sha8(text), "facts": f, "alerts": alerts, "recent": [], "supply_prices": {"free": 50}, "surge_units": {}}
    if old is not None:
        old_sec = facts.section(old, 3)[0]
        of = facts.parse(old_sec)[0]
        m["recent"] = [{"ts": now - 600, "sha": m["sha"], "source": "rules", "version": "3.6", "title": "Storage",
                        "parsed_changed": {k: [of.get(k), v] for k, v in f.items() if of.get(k) != v},
                        "diff": facts._diff(old_sec, sec)}]
    return m


class PatchDryRun(unittest.TestCase):
    def setUp(self):
        self.before = market_of(FIXTURE)
        self.after = market_of(PATCHED, FIXTURE)

    def brief(self, market, days_left=30):
        t = ta.EconomicsMixTest()
        t.MARKET = market
        t.POL = dict(t.POL, stock=dict(t.POL["stock"], end_ts=NOW + days_left * 86400))
        return t.brief()

    def test_the_patch_is_read_as_facts_an_alert_and_a_diff(self):
        f = self.after["facts"]
        self.assertEqual((f["storage_pct_day"], f["book_counts_stock_at_cost"], f["stock_pays_storage"]), (12.0, None, True))
        self.assertEqual(self.after["alerts"], ["rules: book_counts_stock_at_cost not found (the market text changed?)"])
        r = self.after["recent"][0]
        self.assertEqual(r["parsed_changed"], {"storage_pct_day": [1.0, 12.0], "book_counts_stock_at_cost": [True, None]})
        self.assertIn("+", r["diff"])
        self.assertIn("12% of their cost per day in storage", r["diff"])
        self.assertIn("half of what the unsold units cost", r["diff"])

    def test_the_brief_turns_the_patch_into_losing_lines(self):
        e0, e1 = self.brief(self.before)["economics"], self.brief(self.after)["economics"]
        self.assertEqual((e0["capital"]["storage_pct_day"], e0["holding"]["losing_lines"], e0["capital"]["stock_at_cost"]), (1.0, 0, True))
        c, h = e1["capital"], e1["holding"]
        self.assertEqual((c["storage_pct_day"], c["storage_cost_day"], c["stock_at_cost"]), (12.0, 120000, None))
        self.assertLess(c["hold_vs_margin"], 1)                       # the shelf-wide test still reads cheap...
        self.assertEqual((h["losing_lines"], h["losing_cost"], h["losing_share"]), (1, 400000, 0.4))   # ...the line test does not
        self.assertEqual(h["lines"][0]["sku"], "GSM")
        self.assertTrue(h["lines"][0]["losing"] and h["lines"][0]["hold_share"] > 1)

    def test_the_prompt_carries_the_patch_its_limits_and_the_reverse_order(self):
        pol = copy.deepcopy(policy.DEFAULTS)
        p = decide.build_prompt("# strategy\n", pol, guard.tunable_view(pol), self.brief(self.after), [],
                                market=self.after, max_changes=4, alerts=self.after["alerts"],
                                patch={"version": "3.6", "title": "Storage", "ts": NOW - 600})
        self.assertIn("## Rule changes in the last 24 h", p)
        self.assertIn('"storage_pct_day": [1.0, 12.0]', p)
        self.assertIn("book_counts_stock_at_cost not found", p)
        self.assertIn('"losing_lines": 1', p)
        self.assertIn("- Change at most 4 keys.", p)
        self.assertIn("may step 3x their max_step", p)
        self.assertIn("reverse\n  the order: stock.inventory_cap_share and stock.big_ticket_share down", p)
        self.assertIn("also lower seller.first_ask_list, first_ask_big_list and\n  seller.floor_list toward p_clear_24h", p)

    def test_the_guard_takes_the_answer_in_three_steps_while_the_patch_is_recent(self):
        pol = copy.deepcopy(policy.DEFAULTS)
        pol["stock"].update(inventory_cap_share=0.90, big_ticket_share=0.90, max_hold=20)
        pol["seller"].update(floor_list=0.63, stale_floor_cost_mult=1.0, goods_stale_days=5)
        rows = [{"key": "stock.inventory_cap_share", "value": 0.5, "reason": "storage 12%"},
                {"key": "stock.big_ticket_share", "value": 0.5, "reason": "storage 12%"},
                {"key": "seller.stale_floor_cost_mult", "value": 0.7, "reason": "clearance"},
                {"key": "seller.goods_stale_days", "value": 2, "reason": "clearance"}]
        acc, rej = guard.guard(rows, pol, {}, NOW, max_changes=4, cooldown_h=1, patch=True)
        self.assertEqual([(a["key"], a["new"]) for a in acc],
                         [("stock.inventory_cap_share", 0.75), ("stock.big_ticket_share", 0.6),
                          ("seller.stale_floor_cost_mult", 0.7), ("seller.goods_stale_days", 2)])
        self.assertEqual(rej, [])
        acc, _ = guard.guard(rows, pol, {}, NOW, max_changes=4, cooldown_h=3)      # the usual limits: one step each
        self.assertEqual([(a["key"], a["new"]) for a in acc],
                         [("stock.inventory_cap_share", 0.85), ("stock.big_ticket_share", 0.8),
                          ("seller.stale_floor_cost_mult", 0.9), ("seller.goods_stale_days", 4)])


if __name__ == "__main__":
    unittest.main()
