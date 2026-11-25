"""strategist/skillfile.py: in-place value edits on a copy of a frozen strategy/SKILL.md (fixture_SKILL.md).

Only the targeted value bytes and the version line may change; a changed file (Conflict) or
an invalid result leaves the file untouched. The real SKILL.md is only ever read.
"""
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-skillfile-"))
W = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(W))

import policy
from strategist import skillfile as sf

# A frozen copy of strategy/SKILL.md: the image has no SKILL.md, and the values asserted here
# must not move when the user or the strategist tunes the real file.
REAL = Path(__file__).resolve().parent / "fixture_SKILL.md"


def value_token(line):
    """The value text of a 'key: value   # comment' line."""
    return policy._strip_comment(line).partition(":")[2].strip()


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="skillfile-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = self.dir / "SKILL.md"
        shutil.copyfile(REAL, self.path)
        self.orig = self.path.read_text(encoding="utf-8")

    def line_of(self, text, key):
        """Index of the line holding the leaf key (last part of the dotted key) inside its section."""
        sec, leaf = key.split(".")
        lines = text.splitlines()
        start = lines.index(f"  {sec}:")
        return next(i for i in range(start + 1, len(lines)) if lines[i].startswith(f"    {leaf}:"))

    def assert_untouched(self):
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.orig)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["SKILL.md"])


class Read(Base):
    def test_read_real_copy(self):
        text, front, body, errors = sf.read(self.path)
        self.assertEqual(errors, [])
        self.assertEqual(text, self.orig)
        self.assertIn("seller", front["policy"])
        self.assertTrue(body.lstrip().startswith("# "))

    def test_read_missing_and_broken(self):
        self.assertTrue(sf.read(self.dir / "nope.md")[3])
        self.path.write_text("no front matter\n", encoding="utf-8")
        text, front, body, errors = sf.read(self.path)
        self.assertEqual((text, front), ("no front matter\n", {}))
        self.assertTrue(errors)
        self.path.write_text(self.orig.replace("floor_list: 0.60", "floor_list: 0.90"), encoding="utf-8")
        self.assertIn("seller.floor_list: above seller.first_ask_list", sf.read(self.path)[3])

    def test_key_missing_from_the_file_is_filled_for_validate_only(self):
        old = self.orig.replace("    focus_max: 8\n", "")
        self.path.write_text(old, encoding="utf-8")
        text, front, _, errors = sf.read(self.path)
        self.assertEqual(errors, [])
        self.assertNotIn("focus_max", front["policy"]["stock"])
        new = sf.apply(self.path, old, {"stock.max_qty": 15})       # the write validates the same way
        self.assertNotIn("focus_max:", new)


class Apply(Base):
    CHANGES = {"seller.first_ask_list": 0.9, "stock.max_qty": 15, "buyer.cap_food": 0.98}

    def test_only_value_bytes_and_version_change(self):
        new = sf.apply(self.path, self.orig, self.CHANGES)
        self.assertEqual(self.path.read_text(encoding="utf-8"), new)
        old_lines, new_lines = self.orig.splitlines(keepends=True), new.splitlines(keepends=True)
        self.assertEqual(len(old_lines), len(new_lines))
        changed = {i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b}
        vi = old_lines.index(next(x for x in old_lines if x.startswith("version:")))
        targets = {self.line_of(self.orig, k): k for k in self.CHANGES}
        self.assertEqual(changed, {vi} | set(targets))
        n = int(value_token(old_lines[vi]))
        self.assertEqual(new_lines[vi], old_lines[vi].replace(str(n), str(n + 1), 1))
        for i, key in targets.items():
            old, tok = old_lines[i], value_token(old_lines[i])
            colon = old.index(":")
            want = old[:colon] + old[colon:].replace(tok, sf.fmt(self.CHANGES[key]), 1)
            self.assertEqual(new_lines[i], want)

    def test_comments_and_body_kept(self):
        new = sf.apply(self.path, self.orig, self.CHANGES)
        line = new.splitlines()[self.line_of(new, "seller.first_ask_list")]
        self.assertEqual(line, "    first_ask_list: 0.9          # D3 first ask = max(list x this, floor)")
        self.assertEqual(new.count("#"), self.orig.count("#"))
        self.assertEqual(policy.split_skill(new)[1], policy.split_skill(self.orig)[1])

    def test_result_validates_and_reads_back(self):
        sf.apply(self.path, self.orig, self.CHANGES)
        text, front, _, errors = sf.read(self.path)
        self.assertEqual(errors, [])
        for k, v in self.CHANGES.items():
            self.assertEqual(policy.get(front["policy"], k), v)
        old_front = policy.split_skill(self.orig)[0]
        self.assertEqual(front["version"], old_front["version"] + 1)

    def test_versions_keep_counting(self):
        t1 = sf.apply(self.path, self.orig, {"seller.floor_list": 0.55})
        t2 = sf.apply(self.path, t1, {"seller.floor_list": 0.5})
        v0 = policy.split_skill(self.orig)[0]["version"]
        self.assertEqual(policy.split_skill(t2)[0]["version"], v0 + 2)
        self.assertEqual(policy.split_skill(t2)[0]["policy"]["seller"]["floor_list"], 0.5)

    def test_conflict_when_file_changed(self):
        edited = self.orig.replace("floor_list: 0.60", "floor_list: 0.65")
        self.path.write_text(edited, encoding="utf-8")
        with self.assertRaises(sf.Conflict):
            sf.apply(self.path, self.orig, {"seller.floor_list": 0.55})
        self.assertEqual(self.path.read_text(encoding="utf-8"), edited)

    def test_conflict_when_saved_during_the_write(self):
        real_fsync = os.fsync

        def save_then_fsync(fd):                        # the user saves while the temp file is fsynced
            with open(self.path, "a", encoding="utf-8") as f:
                f.write("USER EDIT\n")
            real_fsync(fd)

        with mock.patch.object(sf.os, "fsync", save_then_fsync), self.assertRaises(sf.Conflict):
            sf.apply(self.path, self.orig, {"seller.floor_list": 0.55})
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.orig + "USER EDIT\n")
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["SKILL.md"])

    def test_invalid_change_raises_and_leaves_file(self):
        bad = [{"seller.floor_list": 0.9},              # above first_ask_list 0.85
               {"stock.cash_floor": 100000},            # below the bankruptcy line
               {"seller.first_ask_list": 2.0},          # outside policy.RANGES
               {"seller.no_such_key": 1},
               {"seller": 1},                           # a section, not a value
               {"seller.fact_block": True},
               {"seller.floor_list": float("nan")},
               {"seller.floor_list": "0.55"}]
        for change in bad:
            with self.subTest(change=change), self.assertRaises(ValueError):
                sf.apply(self.path, self.orig, change)
            self.assert_untouched()

    def test_file_mode_kept(self):
        os.chmod(self.path, 0o640)
        sf.apply(self.path, self.orig, self.CHANGES)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o640)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["SKILL.md"])

    def test_ver_matches_policy_load(self):
        new = sf.apply(self.path, self.orig, self.CHANGES)
        with mock.patch.object(policy, "LASTGOOD", self.dir / "memory" / "policy.lastgood.json"):
            pol, ver, errors = policy.load(self.path)
        self.assertEqual(errors, [])
        self.assertEqual(sf.ver(new), ver)
        self.assertNotEqual(sf.ver(self.orig), ver)


class ListValue(Base):
    def test_focus_list_round_trip_through_policy_load(self):
        skus = ["Ga1b2c3d4", "G00ff00ff"]
        new = sf.apply(self.path, self.orig, {"stock.focus_skus": skus, "stock.focus_cover_mult": 2.5})
        i = self.line_of(new, "stock.focus_skus")
        self.assertEqual(new.splitlines()[i], "    focus_skus: [Ga1b2c3d4, G00ff00ff]"
                         + self.orig.splitlines()[i][len("    focus_skus: []"):])     # spacing and comment kept
        with mock.patch.object(policy, "LASTGOOD", self.dir / "memory" / "policy.lastgood.json"):
            pol, ver, errors = policy.load(self.path)
        self.assertEqual(errors, [])
        self.assertEqual(pol["stock"]["focus_skus"], skus)
        self.assertEqual(ver, sf.ver(new))
        back = sf.apply(self.path, new, {"stock.focus_skus": []})
        self.assertEqual(back.splitlines()[i], self.orig.splitlines()[i])
        self.assertEqual(policy.split_skill(back)[0]["version"], policy.split_skill(self.orig)[0]["version"] + 2)

    def test_bad_lists_leave_the_file(self):
        for skus in (["a b"], ["G1", "G1"], [f"G{i}" for i in range(9)], ["123"], ["true"], [1], "G1"):
            with self.subTest(skus=skus), self.assertRaises(ValueError):
                sf.apply(self.path, self.orig, {"stock.focus_skus": skus})
            self.assert_untouched()

    def test_fmt_list(self):
        self.assertEqual((sf.fmt([]), sf.fmt(["A", "b_2"])), ("[]", "[A, b_2]"))


class MissingKeys(Base):
    """A policy.DEFAULTS key the file lacks is inserted when written: under its section, or a new section."""

    def test_inserted_at_the_end_of_its_section_with_the_template_comment(self):
        # a frozen template, not the repository's SKILL.md: on the N36L that file is the live one (compose mount)
        template = self.dir / "template.md"
        template.write_text("---\nversion: 1\npolicy:\n  stock:\n"
                            "    max_hold: 40                  # units one SKU may hold (the cover target's cap)\n---\n",
                            encoding="utf-8")
        with mock.patch.object(sf, "TEMPLATES", (template, self.path)):
            new = sf.apply(self.path, self.orig, {"stock.max_hold": 50, "seller.floor_list": 0.55})
        lines = new.splitlines()
        i = next(n for n, x in enumerate(lines) if x.startswith("    max_hold:"))
        self.assertEqual(lines[i + 1], "  buyer:")                         # after the last stock line
        self.assertEqual(value_token(lines[i]), "50")
        self.assertIn("# units one SKU may hold", lines[i])                # the repository SKILL.md's comment
        front = policy.split_skill(new)[0]
        self.assertEqual((front["version"], front["policy"]["stock"]["max_hold"]), (2, 50))
        self.assertEqual(policy.load(self.path)[2], [])
        self.assertEqual(len(new.splitlines()), len(self.orig.splitlines()) + 1)

    def test_new_section_after_the_policy_block(self):
        new = sf.apply(self.path, self.orig, {"market.surge_free": 40, "market.surge_max": 2.0})
        front = policy.split_skill(new)[0]
        self.assertEqual(front["policy"]["market"], {"surge_free": 40, "surge_max": 2.0})
        lines = new.splitlines()
        self.assertEqual(lines[lines.index("  market:") + 3], "---")
        self.assertEqual(policy.fill_defaults(front["policy"])[0]["market"]["surge_per_unit"], 0.002)

    def test_without_a_template_line_a_plain_comment(self):
        with mock.patch.object(sf, "TEMPLATES", (self.dir / "none.md", self.path)):
            new = sf.apply(self.path, self.orig, {"stock.trend_max": 8})
        line = next(x for x in new.splitlines() if x.startswith("    trend_max:"))
        self.assertEqual(line.split("#")[1].strip(), "added by the strategist (a policy.DEFAULTS key)")

    def test_unknown_key_still_refused(self):
        for key in ("stock.no_such_key", "nosection.x", "seller"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                sf.apply(self.path, self.orig, {key: 1})
            self.assert_untouched()


class Notes(Base):
    def test_clean(self):
        dirty = ("# Heading\n---\n```python\nversion: 9\n  floor_list: 0.9\n```\npolicy:\n"
                 "<script>x</script>keep <b>this</b>\x00\x1b[31m a < b\n\n\n\nlast")
        self.assertEqual(sf.clean_notes(dirty), "Heading\nxkeep this[31m a  b\n\nlast")
        self.assertEqual(len(sf.clean_notes("y" * 5000)), sf.NOTES_MAX)
        for v in (None, 3, "", "---\n```", "   "):
            self.assertIsNone(sf.clean_notes(v))

    def test_section_created_then_replaced(self):
        self.assertIsNone(sf.notes_of(self.orig))
        one = sf.apply(self.path, self.orig, {}, notes="first")
        self.assertEqual(sf.notes_of(one), "first")
        self.assertTrue(one.replace("version: 2", "version: 1", 1).startswith(self.orig.rstrip("\n")))
        self.assertEqual(policy.split_skill(one)[0]["version"], 2)
        two = sf.apply(self.path, one, {}, notes="second\nline")
        self.assertEqual((sf.notes_of(two), two.count(sf.NOTES_HEAD)), ("second\nline", 1))

    def test_section_in_the_middle_keeps_what_follows(self):
        text = self.orig.rstrip("\n") + "\n\n## Strategist notes\n\nold\n\n## Later\n\nkept\n"
        self.path.write_text(text, encoding="utf-8")
        new = sf.apply(self.path, text, {}, notes="new")
        self.assertTrue(new.endswith("## Strategist notes\n\nnew\n\n## Later\n\nkept\n"), new[-80:])
        self.assertEqual(sf.notes_of(new), "new")


class Release(Base):
    """The body, except `## Strategist notes`, follows the SKILL.md shipped with the code."""
    BODY = "\n# Release\n\n- Unsold stock counts at cost.\n"

    def test_body_replaced_front_matter_and_notes_kept(self):
        live = sf.set_notes(self.orig, "my reading")
        new = sf.follow_release(live, self.BODY)
        front = live[:live.index("\n---\n", 4) + 5]
        self.assertEqual(new, front + self.BODY + "\n## Strategist notes\n\nmy reading\n")
        self.assertIsNone(sf.follow_release(new, self.BODY))
        self.assertIsNone(sf.follow_release(new, "# Release\n- Unsold   stock counts\nat cost.\n\n"))   # whitespace only
        self.assertEqual(sf.notes_of(new), "my reading")
        self.assertEqual(sf.follow_release(self.orig, self.BODY), front + self.BODY)        # no notes section

    def test_notes_in_the_middle_move_to_the_end_and_the_template_has_none(self):
        live = self.orig.replace("\n## Stock\n", "\n## Strategist notes\n\nmid\n\n## Stock\n", 1)
        new = sf.follow_release(live, self.BODY)
        self.assertTrue(new.endswith(self.BODY + "\n## Strategist notes\n\nmid\n\n"), new[-200:])
        self.assertNotIn("## Stock", new)

    def test_release_body_from_the_first_template_that_is_not_the_live_file(self):
        other = self.dir / "release.md"
        front = self.orig[:self.orig.index("\n---\n", 4) + 5]
        other.write_text(sf.set_notes(front + self.BODY, "template notes are dropped"), encoding="utf-8")
        with mock.patch.object(sf, "TEMPLATES", (self.path, self.dir / "none.md", other)):
            self.assertEqual(sf.release_body(self.path), self.BODY + "\n")
        with mock.patch.object(sf, "TEMPLATES", (self.path, self.dir / "none.md")):
            self.assertIsNone(sf.release_body(self.path))

    def test_apply_writes_the_body_with_the_changes_and_notes(self):
        new = sf.apply(self.path, self.orig, {"seller.floor_list": 0.55}, notes="n", body=self.BODY)
        self.assertEqual(self.path.read_text(encoding="utf-8"), new)
        self.assertEqual(policy.split_skill(new)[1], self.BODY + "\n## Strategist notes\n\nn\n")
        self.assertEqual(policy.split_skill(new)[0]["policy"]["seller"]["floor_list"], 0.55)
        again = sf.apply(self.path, new, {}, body=self.BODY)            # the same body: only the version moves
        self.assertEqual(policy.split_skill(again)[1], policy.split_skill(new)[1])


class History(Base):
    def test_copies_are_named_by_epoch_and_never_overwritten(self):
        a = sf.save_copy(self.path, "one", 1794000000.7)
        b = sf.save_copy(self.path, "two", 1794000000.2)
        c = sf.save_copy(self.path, "three", 1794000001)
        self.assertEqual([p.name for p in (a, b, c)], ["SKILL.md.1794000000", "SKILL.md.1794000000_1",
                                                         "SKILL.md.1794000001"])
        self.assertEqual([p.read_text(encoding="utf-8") for p in sf.copies(self.path)], ["one", "two", "three"])
        self.assertEqual(sorted(p.name for p in self.dir.iterdir() if p.name.startswith(".")), [])

    def test_without_hard_links_a_rename_to_a_free_name(self):
        sf.save_copy(self.path, "one", 1794000000)
        with mock.patch.object(sf.os, "link", side_effect=PermissionError("no links")):
            b = sf.save_copy(self.path, "two", 1794000000)
        self.assertEqual(b.name, "SKILL.md.1794000000_1")
        self.assertEqual([p.read_text(encoding="utf-8") for p in sf.copies(self.path)], ["one", "two"])
        self.assertEqual(sorted(p.name for p in self.dir.iterdir() if p.name.startswith(".")), [])

    def test_keep_hand_edit_only_when_different(self):
        self.assertEqual(sf.keep_hand_edit(self.path, self.orig, 10).name, "SKILL.md.10")     # the first copy
        self.assertIsNone(sf.keep_hand_edit(self.path, self.orig, 20))
        self.assertEqual(sf.keep_hand_edit(self.path, self.orig + "x", 30).name, "SKILL.md.30")
        self.assertEqual(policy.load(self.path)[2], [])                  # the shop reads only SKILL.md

    def test_other_files_are_not_copies(self):
        for name in ("SKILL.md.bak", "SKILL.md.12x", "OTHER.md.12", ".SKILL.md.12"):
            (self.dir / name).write_text("x", encoding="utf-8")
        self.assertEqual(sf.copies(self.path), [])


class Fmt(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual([sf.fmt(v) for v in (0.8, 0.82, 0.123456, 1.0, 3, 500000, 0.1 + 0.2)],
                         ["0.8", "0.82", "0.1235", "1.0", "3", "500000", "0.3"])
        for v in (True, float("inf"), None, "1"):
            with self.assertRaises(ValueError):
                sf.fmt(v)


if __name__ == "__main__":
    unittest.main()
