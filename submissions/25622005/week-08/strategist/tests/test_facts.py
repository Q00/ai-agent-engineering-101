"""strategist/facts.py offline: the Round section, parsed facts with plausibility, fetch with cache fallback,
24 h rule diffs, the /rules patch notes, supply prices and the market-rule sync rows. fixture_agent.md is the
Round 3 section of the market's /agent.md on 2026-11-05; fixture_rules.html is the market's /rules page on
2026-11-06 (three patch articles: 3.3 latest, 3.2, 3.0)."""
import copy
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-facts-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402

import policy  # noqa: E402
from strategist import facts  # noqa: E402

FIXTURE = (Path(__file__).resolve().parent / "fixture_agent.md").read_text(encoding="utf-8")
PAGE = (Path(__file__).resolve().parent / "fixture_rules.html").read_text(encoding="utf-8")
DOC = "# Agent Market: setup instructions for a coding agent\n\nRun the commands yourself.\n\n## 8. Tell the person\n\nx\n\n" + FIXTURE
NOW = 1_794_000_000.0
H = 3600
PRICES = {"window_s": 3600, "free": 50, "per_unit": 0.002, "max": 1.5,
          "surge": [{"sku": f"G{i:02d}", "name": "이름", "units": 100 - i, "factor": 1.1} for i in range(12)]}
TODAY = {"supply_every_min": 20, "supply_open_min": 3, "break_window_h": 6, "storage_pct_day": 1.0,
         "vote_min_deals": 5, "book_counts_stock_at_cost": True, "stock_pays_storage": True, "post_gap_min": 5, "shop_weights": {"book": 0.5, "revenue": 0.3, "stars": 0.2},
         "buyer_weights": {"surplus": 0.5, "forum": 0.3, "deals": 0.1, "posts": 0.1}}


def patched(text=FIXTURE):
    return (text.replace("once every 20 minutes, for 3 minutes", "once every 30 minutes, for 5 minutes")
            .replace("one post per 5 minutes", "one post per 10 minutes"))


class Parse(unittest.TestCase):
    def test_parse_today(self):
        sec, found = facts.section(FIXTURE, 3)
        self.assertTrue(found)
        self.assertEqual(facts.parse(sec), (TODAY, []))

    def test_a_reworded_stock_valuation_is_unknown_with_an_alert_never_assumed(self):
        sec = facts.section(FIXTURE, 3)[0].replace("Book = cash + what the unsold units cost",
                                                   "Book = cash + half of what the unsold units cost")
        got, alerts = facts.parse(sec)
        self.assertIsNone(got["book_counts_stock_at_cost"])
        self.assertTrue(got["stock_pays_storage"])
        self.assertEqual(alerts, ["rules: book_counts_stock_at_cost not found (the market text changed?)"])

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


class Market:
    """A fake market serving /agent.md, /rules and /api/supply-prices from the test's fields."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="facts-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.rules = self.tmp / "rules"
        self.doc, self.resp, self.prices, self.page, self.page_resp = DOC, None, PRICES, PAGE, None
        self.state = {}

    def handler(self, request):
        if request.url.path == "/agent.md":
            return self.resp(request) if self.resp else text_response(self.doc)
        if request.url.path == "/rules":
            return self.page_resp(request) if self.page_resp else text_response(self.page, "text/html; charset=utf-8")
        if request.url.path == "/api/supply-prices":
            return httpx.Response(200, json=self.prices)
        return httpx.Response(404)

    def collect(self, now=NOW):
        with httpx.Client(base_url="http://market.test", transport=httpx.MockTransport(self.handler)) as c:
            return facts.collect(c, self.state, now, self.rules, 3)


class Collect(Market, unittest.TestCase):
    def test_first_run_parses_stores_a_copy_and_has_no_diff(self):
        m, errors = self.collect()
        self.assertEqual(errors, [])
        self.assertEqual((m["sha"], m["from_cache"], m["section_found"], m["facts"], m["recent"], m["alerts"]),
                         (facts.sha8(DOC), False, True, TODAY, [], []))
        self.assertTrue(m["section"].startswith("## Round 3"))
        self.assertEqual(sorted(p.name for p in self.rules.iterdir()), [f"agent-{m['sha']}.md", f"rules-{facts.sha8(PAGE)}.html"])
        self.assertEqual({k: self.state["rules"][k] for k in ("sha", "facts", "recent")},
                         {"sha": m["sha"], "facts": TODAY, "recent": []})
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
        self.assertNotIn("sha", self.state["rules"])                      # only the patch notes' hashes

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


class PatchNotes(Market, unittest.TestCase):
    """The /rules patch articles: parsed, seeded silently on the first sight, a new or changed one is one `recent`
    entry (which wakes the model in run.py), a page without articles is hashed as text, a failed fetch uses the
    cached page and records nothing."""

    def test_parse_the_three_patches(self):
        patches, text = facts.parse_page(PAGE)
        self.assertEqual([(p["version"], p["date"], p["title"]) for p in patches],
                         [("3.3", "Nov 5, 2026", "Stock counts"), ("3.2", "Nov 4, 2026", "The crowded-product patch"),
                          ("3.0", "Nov 3, 2026", "Round 3 opens")])
        p32 = patches[1]["text"]
        self.assertIn("[New]", p32)
        self.assertIn("Up to 50 units of a product bought by the whole market in the last hour cost the plain price.", p32)
        self.assertIn("Do: Check surge in inventory.json or the list below before you restock.", p32)
        self.assertNotIn("Stock counts", p32)                          # another article's header
        self.assertTrue(all(len(p["text"]) <= facts.PATCH_TEXT_MAX for p in patches))
        self.assertIn("Round 3 rulebook", text)
        self.assertNotIn("function", text)                             # the page's script is left out

    def test_first_sight_is_silent_then_a_new_patch_is_one_entry(self):
        m, errors = self.collect()
        self.assertEqual((errors, m["recent"], [p["version"] for p in m["patches"]]), ([], [], ["3.3", "3.2", "3.0"]))
        self.assertEqual(set(self.state["rules"]["patches"]), {"3.3", "3.2", "3.0"})
        m, _ = self.collect(NOW + 1800)                                 # same page: nothing
        self.assertEqual(m["recent"], [])
        new = ('<article class="patch latest">\n      <header><span class="v">Patch 3.4</span><span class="d">Nov 7, '
               '2026</span><span class="t">Smaller lines</span></header>\n      <ul class="changes">\n        <li><span '
               'class="tag">Changed</span><div><b>Supply calls.</b> Up to 10 units per call.\n          <span class="do">'
               'Plan fewer, larger lines.</span></div></li></ul>\n    </article>\n\n    ')
        self.page = PAGE.replace('<article class="patch latest">', new + '<article class="patch">', 1)
        m, errors = self.collect(NOW + 3600)
        self.assertEqual(errors, [])
        self.assertEqual(len(m["recent"]), 1)
        r = m["recent"][0]
        self.assertEqual((r["ts"], r["source"], r["version"], r["title"], r["parsed_changed"]),
                         (NOW + 3600, "rules", "3.4", "Smaller lines", {}))
        self.assertTrue(r["diff"].startswith("Patch 3.4 (Nov 7, 2026): Smaller lines\n"))
        self.assertIn("Up to 10 units per call.\nDo: Plan fewer, larger lines.", r["diff"])
        self.assertEqual(m["patches"][0]["version"], "3.4")

    def test_a_changed_patch_shows_a_diff(self):
        self.collect()
        self.page = PAGE.replace("Each further unit adds 0.2%, up to +50%.", "Each further unit adds 0.5%, up to +80%.")
        m, _ = self.collect(NOW + 1800)
        self.assertEqual([(r["source"], r["version"]) for r in m["recent"]], [("rules", "3.2")])
        self.assertIn("+", m["recent"][0]["diff"])
        self.assertIn("adds 0.5%, up to +80%", m["recent"][0]["diff"])
        self.assertIn("adds 0.2%, up to +50%", m["recent"][0]["diff"])

    @mock.patch.object(facts, "PATCH_TEXT_MAX", 200)
    def test_a_change_past_the_text_cut_still_counts(self):
        self.collect()
        self.page = PAGE.replace("Missing players.", "CHANGED players.", 1)   # 3.2, past its first 200 characters
        self.assertEqual(facts.parse_page(self.page)[0][1]["text"], facts.parse_page(PAGE)[0][1]["text"])
        m, _ = self.collect(NOW + 1800)
        self.assertEqual([(r["source"], r["version"]) for r in m["recent"]], [("rules", "3.2")])
        self.assertIn("CHANGED", m["recent"][0]["diff"])

    def test_a_moving_page_without_articles_wakes_at_most_once_per_gap(self):
        self.collect()
        bare = PAGE.replace('class="patch latest"', 'class="note"').replace('class="patch"', 'class="note"')
        for i in range(4):                                              # the text changes every run
            self.page = bare.replace("Round 3 rulebook", f"Round 3 rulebook {i}")
            m, _ = self.collect(NOW + 1800 * (i + 1))
        self.assertEqual([r["source"] for r in m["recent"]], ["rules-page"])
        m, _ = self.collect(NOW + 1800 + facts.PAGE_WAKE_GAP_H * 3600)  # after the gap: the next change shows
        self.assertEqual([r["source"] for r in m["recent"]], ["rules-page", "rules-page"])

    def test_no_article_hashes_the_page_once(self):
        self.collect()
        self.page = PAGE.replace('class="patch latest"', 'class="note"').replace('class="patch"', 'class="note"')
        m, _ = self.collect(NOW + 1800)
        self.assertEqual([(r["source"], r["version"]) for r in m["recent"]], [("rules-page", None)])
        self.assertEqual(m["patches"], [])
        m, _ = self.collect(NOW + 3600)                                 # the same page again: no new entry
        self.assertEqual(len(m["recent"]), 1)

    def test_a_failed_fetch_uses_the_cache_and_records_nothing(self):
        self.collect()
        self.page_resp = lambda r: httpx.Response(503)
        m, errors = self.collect(NOW + 1800)
        self.assertTrue(errors[0].startswith("patch_notes: HTTPStatusError") and errors[0].endswith("(using the cached copy)"))
        self.assertEqual((m["patches_from_cache"], len(m["patches"]), m["recent"]), (True, 3, []))
        self.page_resp = lambda r: text_response("x" * (facts.MAX_DOC + 1), "text/html")
        _, errors = self.collect(NOW + 3600)
        self.assertTrue(errors[0].startswith("patch_notes: ValueError"), errors)

    def test_agent_md_change_keeps_the_patch_hashes(self):
        self.collect()
        self.doc = patched(DOC)
        self.collect(NOW + 1800)
        self.assertEqual(set(self.state["rules"]["patches"]), {"3.3", "3.2", "3.0"})
        self.assertEqual(self.state["rules"]["sha"], facts.sha8(self.doc))

    def test_surge_units_keep_every_listed_sku(self):
        m, _ = self.collect()
        self.assertEqual(len(m["surge_units"]), 12)
        self.assertEqual(m["surge_units"]["G00"], 100)


SP = {"window_s": 3600, "free": 50, "per_unit": 0.002, "max": 1.5, "surge": []}


class Sync(unittest.TestCase):
    def pol(self, gap):
        p = copy.deepcopy(policy.DEFAULTS)
        p["forum"]["min_gap_min"] = gap
        return p

    def test_sync_row_when_the_market_differs(self):
        rows, alerts = facts.sync_proposals(self.pol(10), TODAY, SP)
        self.assertEqual((rows, alerts), ([{"key": "forum.min_gap_min", "value": 5, "source": "market-rule",
                                            "reason": "market rule: one post per 5 minutes"}], []))
        self.assertEqual(facts.sync_proposals(self.pol(5), TODAY, SP), ([], []))

    def test_market_value_outside_policy_ranges_is_an_alert_not_a_row(self):
        self.assertEqual(facts.sync_proposals(self.pol(5), dict(TODAY, post_gap_min=3), SP),
                         ([], ["forum.min_gap_min kept at 5: the market's post_gap_min 3 is outside policy.RANGES"]))
        rows, _ = facts.sync_proposals(self.pol(10), dict(TODAY, post_gap_min=3), SP)     # the guard clamps it to 5
        self.assertEqual([r["value"] for r in rows], [3])

    def test_unknown_fact_keeps_the_value_with_an_alert(self):
        for f in ({}, None, dict(TODAY, post_gap_min=None)):
            rows, alerts = facts.sync_proposals(self.pol(10), f, SP)
            self.assertEqual(rows, [])
            self.assertEqual(alerts, ["forum.min_gap_min not synced: the market's post_gap_min is unknown, kept at 10"])

    def test_surge_constants_follow_supply_prices(self):
        p = self.pol(5)
        rows, alerts = facts.sync_proposals(p, TODAY, dict(SP, free=40, per_unit=0.003, max=2.0))
        self.assertEqual(alerts, [])
        self.assertEqual({r["key"]: r["value"] for r in rows},
                         {"market.surge_free": 40, "market.surge_per_unit": 0.003, "market.surge_max": 2.0})
        self.assertEqual({r["source"] for r in rows}, {"market-rule"})
        self.assertIn("/api/supply-prices free", rows[0]["reason"])

    def test_surge_keys_missing_from_the_file_compare_with_defaults(self):
        p = self.pol(5)
        del p["market"]
        self.assertEqual(facts.sync_proposals(p, TODAY, SP), ([], []))
        rows, _ = facts.sync_proposals(p, TODAY, dict(SP, free=60))
        self.assertEqual([(r["key"], r["value"]) for r in rows], [("market.surge_free", 60)])

    def test_supply_prices_missing_keeps_the_last_value(self):
        p = self.pol(5)
        p["market"]["surge_free"] = 40                  # synced earlier
        rows, alerts = facts.sync_proposals(p, TODAY, None)
        self.assertEqual(rows, [])
        self.assertEqual(alerts[0], "market.surge_free not synced: the market's surge_free is unknown, kept at 40")
        self.assertEqual(len(alerts), 3)


if __name__ == "__main__":
    unittest.main()
