import json
import os
import tempfile
import time
import unittest

import patch_notes
import scorecard

PAGE = """
<article class="patch latest">
  <header><span class="v">Patch 3.3</span><span class="d">Nov 5, 2026</span><span class="t">Stock counts</span></header>
  <ul class="changes">
    <li><span class="tag">Changed</span><div><b>Shop ALL now uses Book.</b> Unsold stock counts at cost.
      <span class="do">Shop ALL = 0.5 book + 0.3 revenue + 0.2 stars.</span></div></li>
  </ul>
</article>
<article class="patch">
  <header><span class="v">Patch 3.2</span><span class="d">Nov 4, 2026</span><span class="t">Surge</span></header>
  <ul class="changes"><li><span class="tag">New</span><div><b>Surge cost.</b> +0.2% a unit.</div></li></ul>
</article>
<div class="bar"><span>Shop ALL</span><div class="parts"><span class="p1" style="width:50%">book 0.5</span><span class="p2" style="width:30%">revenue 0.3</span><span class="p3" style="width:20%">stars 0.2</span></div></div>
<div class="bar"><span>Buyer ALL</span><div class="parts"><span class="p1" style="width:50%">saved 0.5</span><span class="p2" style="width:30%">forum 0.3</span><span class="p3" style="width:10%">deals</span><span class="p4" style="width:10%">posts</span></div></div>
"""


class PatchNotesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_parses_patches_and_weights(self):
        got = patch_notes.parse(PAGE)
        self.assertEqual([p["id"] for p in got["patches"]], ["Patch 3.3", "Patch 3.2"])
        self.assertEqual(got["patches"][0]["changes"][0]["tag"], "Changed")
        self.assertIn("0.5 book", got["patches"][0]["changes"][0]["text"])
        self.assertEqual(got["scoring"]["shops"], [["book", 0.5], ["revenue", 0.3], ["stars", 0.2]])
        self.assertEqual(got["scoring"]["buyers"][2:], [["deals", 0.1], ["posts", 0.1]])

    def test_the_shipped_objective_matches_the_published_one(self):
        out = patch_notes.check(self.tmp.name, PAGE, now=1000)
        self.assertTrue(out["objective_matches"], out["mismatch"])
        self.assertTrue(out["first_check"])
        self.assertEqual(out["new"], [])
        self.assertEqual(len(out["recent"]), 2)

    def test_old_surplus_objective_is_flagged(self):
        old = {**scorecard.OBJECTIVE, "buyers": [( ("surplus", "saved"), 0.5), *((names, w) for names, w in scorecard.OBJECTIVE["buyers"][1:])]}
        # An alias can mask the bug, so the actual outdated single-field formula must be rejected.
        old["buyers"][0] = (("surplus",), 0.5)
        self.assertFalse(patch_notes.check(self.tmp.name, PAGE, now=1000, objective=old)["objective_matches"])

    def test_a_cash_objective_is_flagged(self):
        old = {**scorecard.OBJECTIVE, "shops": [(("profit",), 0.5), (("revenue",), 0.3), (("stars_rank", "stars"), 0.2)]}
        out = patch_notes.check(self.tmp.name, PAGE, now=1000, objective=old)
        self.assertFalse(out["objective_matches"])
        self.assertIn("published book 0.5", out["mismatch"][0])

    def test_a_new_patch_shows_once_as_new_and_a_day_as_recent(self):
        patch_notes.check(self.tmp.name, PAGE, now=1000)
        newer = PAGE.replace('<article class="patch latest">', '<article class="patch latest"><header><span class="v">'
                             'Patch 3.4</span><span class="d">Nov 6</span><span class="t">x</span></header></article>'
                             '<article class="patch">', 1)
        out = patch_notes.check(self.tmp.name, newer, now=2000)
        self.assertEqual([p["id"] for p in out["new"]], ["Patch 3.4"])
        self.assertEqual(out["latest"], "Patch 3.4")
        self.assertEqual(patch_notes.check(self.tmp.name, newer, now=3000)["new"], [])
        later = patch_notes.check(self.tmp.name, newer, now=2000 + 25 * 3600)
        self.assertEqual(later["recent"], [])
        with open(os.path.join(self.tmp.name, patch_notes.STATE)) as f:
            self.assertEqual(json.load(f)["first_seen"]["Patch 3.4"], 2000)

    def test_the_first_check_dates_old_patches_by_their_own_date(self):
        now = time.mktime(time.strptime("Nov 5, 2026 11:00", "%b %d, %Y %H:%M"))
        out = patch_notes.check(self.tmp.name, PAGE, now=now)
        self.assertEqual([p["id"] for p in out["recent"]], ["Patch 3.3"])     # 3.2 (Nov 4) is 35 h old

    def test_a_look_by_hand_records_nothing(self):
        patch_notes.check(self.tmp.name, PAGE, now=1000, record=False)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, patch_notes.STATE)))

    def test_weights_match_by_name_within_the_drawn_width(self):
        drawn = {"shops": [["stars", 0.2], ["book", 0.497], ["revenue", 0.3]],
                 "buyers": [["saved", 0.5], ["forum", 0.3], ["deals", 0.1], ["posts", 0.1]]}
        self.assertEqual(patch_notes.compare(drawn, scorecard.OBJECTIVE), [])
        drawn["shops"][0] = ["stars", 0.1]
        self.assertEqual(patch_notes.compare(drawn, scorecard.OBJECTIVE), ["shops: published stars 0.1, scorecard 0.2"])

    def test_the_profit_fallback_never_matches_a_published_part(self):
        drawn = {"shops": [["profit", 0.5], ["revenue", 0.3], ["stars", 0.2]],
                 "buyers": [["saved", 0.5], ["forum", 0.3], ["deals", 0.1], ["posts", 0.1]]}
        got = patch_notes.compare(drawn, scorecard.OBJECTIVE)
        self.assertIn("shops: published profit 0.5, not in the scorecard", got)
        self.assertIn("shops: scorecard book 0.5, not published", got)

    def test_an_unreadable_page_is_an_error_not_a_crash(self):
        self.assertIn("error", patch_notes.check(self.tmp.name, "<html>moved</html>"))


if __name__ == "__main__":
    unittest.main()
