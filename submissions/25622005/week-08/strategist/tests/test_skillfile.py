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


class Fmt(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual([sf.fmt(v) for v in (0.8, 0.82, 0.123456, 1.0, 3, 500000, 0.1 + 0.2)],
                         ["0.8", "0.82", "0.1235", "1.0", "3", "500000", "0.3"])
        for v in (True, float("inf"), None, "1"):
            with self.assertRaises(ValueError):
                sf.fmt(v)


if __name__ == "__main__":
    unittest.main()
