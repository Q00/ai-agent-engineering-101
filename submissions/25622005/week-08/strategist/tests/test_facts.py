"""strategist/facts.py offline: the Round section, parsed facts with plausibility, fetch with cache fallback,
24 h rule diffs, supply prices and the market-rule sync rows. fixture_agent.md is the Round 3 section of the
market's /agent.md on 2026-11-05."""
import copy
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-facts-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402

import policy  # noqa: E402
from strategist import facts  # noqa: E402

FIXTURE = (Path(__file__).resolve().parent / "fixture_agent.md").read_text(encoding="utf-8")
DOC = "# Agent Market: setup instructions for a coding agent\n\nRun the commands yourself.\n\n## 8. Tell the person\n\nx\n\n" + FIXTURE
NOW = 1_794_000_000.0
H = 3600
PRICES = {"window_s": 3600, "free": 50, "per_unit": 0.002, "max": 1.5,
          "surge": [{"sku": f"G{i:02d}", "name": "이름", "units": 100 - i, "factor": 1.1} for i in range(12)]}
TODAY = {"supply_every_min": 20, "supply_open_min": 3, "break_window_h": 6, "storage_pct_day": 1.0,
         "vote_min_deals": 5, "post_gap_min": 5, "shop_weights": {"book": 0.5, "revenue": 0.3, "stars": 0.2},
         "buyer_weights": {"surplus": 0.5, "forum": 0.3, "deals": 0.1, "posts": 0.1}}


def patched(text=FIXTURE):
    return (text.replace("once every 20 minutes, for 3 minutes", "once every 30 minutes, for 5 minutes")
            .replace("one post per 5 minutes", "one post per 10 minutes"))


class Parse(unittest.TestCase):
    def test_parse_today(self):
        sec, found = facts.section(FIXTURE, 3)
        self.assertTrue(found)
        self.assertEqual(facts.parse(sec), (TODAY, []))

    def test_section_picks_the_round_and_skips_the_setup_steps(self):
        sec, found = facts.section(DOC, 3)
        self.assertTrue(found)
        self.assertTrue(sec.startswith("## Round 3: the economy"))
        self.assertIn("### Round 3 agent contract", sec)
        self.assertNotIn("Run the commands yourself", sec)
        doc = DOC + "\n## Round 4: next\n\nnew rules\n\n## Appendix\n\nz\n"
        self.assertTrue(facts.section(doc, 3)[0].rstrip().endswith("(`item` is the SKU)."))
        self.assertEqual(facts.section(doc, None)[0].strip(), "## Round 4: next\n\nnew rules")   # highest round
        self.assertEqual(facts.section(doc, 9)[0].strip(), "## Round 4: next\n\nnew rules")

    def test_no_round_heading_falls_back_to_the_whole_doc_cut(self):
        sec, found = facts.section("# Rules\n" + "x" * 20_000, 3)
        self.assertFalse(found)
        self.assertEqual(len(sec), facts.SECTION_MAX)

    def test_parse_survives_rewrap(self):
        wrapped = FIXTURE
        for phrase in ("once every 20 minutes, for 3 minutes", "one post per 5 minutes for the shop",
                       "ALL weighs them 0.5, 0.3, 0.2", "Goods can break up to 6 hours", "at least 5 settled deals",
                       "1% of their cost per day in storage", "buyers by surplus (below), forum"):
            self.assertIn(phrase, wrapped)
            wrapped = wrapped.replace(phrase, phrase.replace(" ", "\n   ", 2).replace(", ", ",  "))
        self.assertEqual(facts.parse(wrapped), (TODAY, []))

    def test_post_limit_split_by_role_is_none(self):
        text = re.sub(r"one post per 5 minutes for the shop\s+and for the buyer each",
                      "one post per 5 minutes for the shop and 10 minutes for the buyer", FIXTURE)
        self.assertNotEqual(text, FIXTURE)
        f, alerts = facts.parse(text)
        self.assertIsNone(f["post_gap_min"])
        self.assertEqual(alerts, ["rules: post_gap_min differs by role (5, 10 minutes), not used"])

    def test_post_limit_with_a_bare_or_decimal_number_is_none(self):
        clause = re.compile(r"one post per 5 minutes for the shop\s+and for the buyer each")
        f, alerts = facts.parse(clause.sub("one post per 5 minutes for the shop and 10 for the buyer", FIXTURE))
        self.assertEqual((f["post_gap_min"], alerts), (None, ["rules: post_gap_min differs by role (5, 10 minutes), not used"]))
        f, alerts = facts.parse(FIXTURE.replace("one post per 5 minutes", "one post per 2.5 minutes"))
        self.assertEqual((f["post_gap_min"], alerts), (None, ["rules: post_gap_min 2.5 is outside the plausible 1-60, not used"]))

    def test_comment_in_a_code_block_does_not_end_the_section(self):
        doc = DOC.replace("### Round 3 agent contract",
                          "  ```bash\n# start the kit\n## not a heading either\npython market_kit.py\n  ```\n\n"
                          "### Round 3 agent contract") + "\n## Appendix\n\nz\n"
        sec, _ = facts.section(doc, 3)
        self.assertIn("# start the kit", sec)
        self.assertIn("### Round 3 agent contract", sec)
        self.assertNotIn("## Appendix", sec)

    def test_missing_sentence_none_and_alert(self):
        f, alerts = facts.parse(FIXTURE.replace("Goods can break up to", "Goods may fail within"))
        self.assertIsNone(f["break_window_h"])
        self.assertEqual(alerts, ["rules: break_window_h not found (the market text changed?)"])
        self.assertEqual({k: v for k, v in f.items() if k != "break_window_h"},
                         {k: v for k, v in TODAY.items() if k != "break_window_h"})

    def test_implausible_value_rejected(self):
        f, alerts = facts.parse(FIXTURE.replace("once every 20 minutes", "once every 0 minutes")
                                .replace("one post per 5 minutes", "one post per 500 minutes"))
        self.assertEqual((f["supply_every_min"], f["post_gap_min"], f["supply_open_min"]), (None, None, 3))
        self.assertEqual(alerts, ["rules: supply_every_min 0 is outside the plausible 5-120, not used",
                                  "rules: post_gap_min 500 is outside the plausible 1-60, not used"])

    def test_bad_weights_none(self):
        for old, new in (("ALL weighs them 0.5, 0.3, 0.2", "ALL weighs them 0.5, 0.3, 0.1"),      # sum 0.9
                         ("ALL weighs them 0.5, 0.3, 0.2", "ALL weighs them 0.5, 0.5"),           # 3 names, 2 weights
                         ("shops by book, revenue and stars", "shops by book, revenue and charm")):   # unknown field
            f, alerts = facts.parse(FIXTURE.replace(old, new))
            self.assertIsNone(f["shop_weights"], new)
            self.assertEqual(f["buyer_weights"], TODAY["buyer_weights"])
            self.assertEqual(alerts, ["rules: shop_weights not found (the market text changed?)"])


def text_response(body, ctype="text/markdown; charset=utf-8", status=200):
    return httpx.Response(status, content=body.encode("utf-8"), headers={"content-type": ctype})


class Collect(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="facts-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.rules = self.tmp / "rules"
        self.doc, self.resp, self.prices = DOC, None, PRICES
        self.state = {}

    def handler(self, request):
        if request.url.path == "/agent.md":
            return self.resp(request) if self.resp else text_response(self.doc)
        if request.url.path == "/api/supply-prices":
            return httpx.Response(200, json=self.prices)
        return httpx.Response(404)

    def collect(self, now=NOW):
        with httpx.Client(base_url="http://market.test", transport=httpx.MockTransport(self.handler)) as c:
            return facts.collect(c, self.state, now, self.rules, 3)

    def test_first_run_parses_stores_a_copy_and_has_no_diff(self):
        m, errors = self.collect()
        self.assertEqual(errors, [])
        self.assertEqual((m["sha"], m["from_cache"], m["section_found"], m["facts"], m["recent"], m["alerts"]),
                         (facts.sha8(DOC), False, True, TODAY, [], []))
        self.assertTrue(m["section"].startswith("## Round 3"))
        self.assertEqual([p.name for p in self.rules.iterdir()], [f"agent-{m['sha']}.md"])
        self.assertEqual(self.state["rules"], {"sha": m["sha"], "facts": TODAY, "recent": []})
        m2, _ = self.collect(NOW + 1800)                                # unchanged: still no diff
        self.assertEqual(m2["recent"], [])

    def test_patch_changes_numbers_and_diff(self):
        self.collect()
        self.doc = patched(DOC)
        m, errors = self.collect(NOW + 1800)
        self.assertEqual(errors, [])
        self.assertEqual(len(m["recent"]), 1)
        r = m["recent"][0]
        self.assertEqual((r["ts"], r["sha"]), (NOW + 1800, facts.sha8(self.doc)))
        self.assertEqual(r["parsed_changed"], {"supply_every_min": [20, 30], "supply_open_min": [3, 5],
                                               "post_gap_min": [5, 10]})
        self.assertIn("+  `restock` command, or a simple default). Supply opens for everyone once every 30 minutes,", r["diff"])
        self.assertIn("-  `restock` command, or a simple default). Supply opens for everyone once every 20 minutes,", r["diff"])
        self.assertIn("one post per 10 minutes", r["diff"])
        self.assertEqual(m["facts"]["post_gap_min"], 10)
        self.assertEqual(len(list(self.rules.glob("agent-*.md"))), 2)

    def test_setup_only_change_records_no_rule_change(self):
        self.collect()
        self.doc = DOC.replace("Run the commands yourself.", "Run every command yourself.")
        m, _ = self.collect(NOW + 1800)
        self.assertEqual((m["recent"], self.state["rules"]["sha"]), ([], facts.sha8(self.doc)))

    def test_recent_diffs_pruned_after_24h_and_copies_kept_to_five(self):
        self.collect()
        for i in range(1, 8):
            self.doc = DOC + f"\nline {i}\n"                          # inside the Round section
            self.collect(NOW + i * H)
        self.assertEqual(len(self.state["rules"]["recent"]), 7)
        self.assertEqual(len(list(self.rules.glob("agent-*.md"))), facts.KEEP_COPIES)
        m, _ = self.collect(NOW + 4 * H + 24 * H)
        self.assertEqual([r["ts"] for r in m["recent"]], [NOW + i * H for i in (5, 6, 7)])

    def test_404_uses_the_cache_and_records_the_error(self):
        self.collect()
        self.resp = lambda r: httpx.Response(404)
        m, errors = self.collect(NOW + 1800)
        self.assertTrue(m["from_cache"])
        self.assertEqual((m["facts"], m["sha"]), (TODAY, facts.sha8(DOC)))
        self.assertTrue(errors[0].startswith("rules: HTTPStatusError") and errors[0].endswith("(using the cached copy)"))
        self.assertEqual(m["error"], errors[0])

    def test_no_cache_no_rules(self):
        self.resp = lambda r: httpx.Response(500)
        m, errors = self.collect()
        self.assertEqual((m["section"], m["facts"], m["from_cache"]), (None, {}, False))
        self.assertTrue(errors[0].endswith("(no cached copy)"))
        self.assertNotIn("rules", self.state)

    def test_oversize_or_html_rejected(self):
        for resp in (lambda r: text_response("<html>login</html>", "text/html"),
                     lambda r: text_response("x" * (facts.MAX_DOC + 1)),
                     lambda r: httpx.Response(200, json={"a": 1})):
            self.resp = resp
            m, errors = self.collect()
            self.assertIsNone(m["section"])
            self.assertTrue(errors[0].startswith("rules: ValueError"), errors)

    def test_a_cut_section_is_an_alert(self):
        self.doc = DOC + "\n" + "more rules. " * 200
        m, _ = self.collect()
        self.assertEqual(len(m["section"]), facts.SECTION_MAX)
        self.assertEqual(m["alerts"], [f"rules: the section is cut at {facts.SECTION_MAX} characters, the rest is not shown"])

    def test_supply_prices_shape_ok_and_bad(self):
        m, _ = self.collect()
        sp = m["supply_prices"]
        self.assertEqual({k: sp[k] for k in ("window_s", "free", "per_unit", "max")},
                         {"window_s": 3600, "free": 50, "per_unit": 0.002, "max": 1.5})
        self.assertEqual(len(sp["surge"]), 10)
        self.assertEqual(sp["surge"][0], {"sku": "G00", "units": 100, "factor": 1.1})     # names dropped
        bad = copy.deepcopy(PRICES)
        bad["surge"] = [{"sku": "</untrusted>", "units": 1, "factor": 2}, {"sku": "G1", "units": "x", "factor": 1}]
        self.assertEqual(facts.supply_prices(bad)["surge"], [])
        self.prices = {"window_s": "3600"}
        m, errors = self.collect()
        self.assertIsNone(m["supply_prices"])
        self.assertEqual(errors, ["supply_prices: unexpected shape"])


class Sync(unittest.TestCase):
    def pol(self, gap):
        p = copy.deepcopy(policy.DEFAULTS)
        p["forum"]["min_gap_min"] = gap
        return p

    def test_sync_row_when_the_market_differs(self):
        rows, alerts = facts.sync_proposals(self.pol(10), TODAY)
        self.assertEqual((rows, alerts), ([{"key": "forum.min_gap_min", "value": 5, "source": "market-rule",
                                            "reason": "market rule: one post per 5 minutes"}], []))
        self.assertEqual(facts.sync_proposals(self.pol(5), TODAY), ([], []))

    def test_market_value_outside_policy_ranges_is_an_alert_not_a_row(self):
        self.assertEqual(facts.sync_proposals(self.pol(5), dict(TODAY, post_gap_min=3)),
                         ([], ["forum.min_gap_min kept at 5: the market's post_gap_min 3 is outside policy.RANGES"]))
        rows, _ = facts.sync_proposals(self.pol(10), dict(TODAY, post_gap_min=3))     # the guard clamps it to 5
        self.assertEqual([r["value"] for r in rows], [3])

    def test_unknown_fact_keeps_the_value_with_an_alert(self):
        for f in ({}, None, dict(TODAY, post_gap_min=None)):
            rows, alerts = facts.sync_proposals(self.pol(10), f)
            self.assertEqual(rows, [])
            self.assertEqual(alerts, ["forum.min_gap_min not synced: the market's post_gap_min is unknown, kept at 10"])


if __name__ == "__main__":
    unittest.main()
