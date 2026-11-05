import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import log_archive


class LogArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / log_archive.SOURCE
        self.source.parent.mkdir(parents=True)

    def kept(self):
        folder = self.root / log_archive.ARCHIVE
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []

    def test_copy_is_byte_identical_with_a_checkable_hash(self):
        data = b"12:00:00 line\r\n13:00:00 \xea\xb0\x80\r\n"
        self.source.write_bytes(data)
        made = log_archive.observe(self.root)
        self.assertEqual(made.read_bytes(), data)
        side = made.with_name(made.name + ".sha256").read_bytes().decode()
        self.assertEqual(side, f"{hashlib.sha256(data).hexdigest()}  {made.name}\n")
        self.assertEqual(self.source.read_bytes(), data)

    def test_same_generation_is_copied_once_and_a_new_one_gets_its_own_name(self):
        self.source.write_bytes(b"first")
        os.utime(self.source, ns=(10**18, 10**18))
        first = log_archive.observe(self.root)
        self.assertIsNotNone(first)
        self.assertIsNone(log_archive.observe(self.root))
        self.source.write_bytes(b"second generation")
        os.utime(self.source, ns=(2 * 10**18, 2 * 10**18))
        second = log_archive.observe(self.root)
        self.assertNotEqual(first.name, second.name)
        self.assertEqual(first.read_bytes(), b"first")
        self.assertEqual(len(self.kept()), 4)

    def test_rotation_during_the_copy_leaves_no_normal_file(self):
        self.source.write_bytes(b"old generation")
        real = log_archive.copy_with_hash

        def rotating(source, target):
            digest = real(source, target)
            Path(source).write_bytes(b"new generation, longer than the old")
            return digest

        with mock.patch.object(log_archive, "copy_with_hash", rotating):
            self.assertIsNone(log_archive.observe(self.root))
        self.assertEqual(self.kept(), [])
        failures = (self.root / log_archive.FAILURES).read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(failures[0])["error"], "SourceChanged")
        made = log_archive.observe(self.root)
        self.assertEqual(made.read_bytes(), b"new generation, longer than the old")

    def test_a_truncated_copy_is_not_kept(self):
        self.source.write_bytes(b"whole file")

        def short(source, target):
            Path(target).write_bytes(b"who")
            return hashlib.sha256(b"whole file").hexdigest()

        with mock.patch.object(log_archive, "copy_with_hash", short):
            self.assertIsNone(log_archive.observe(self.root))
        self.assertEqual(self.kept(), [])

    def test_failure_is_recorded_and_never_raised(self):
        self.source.write_bytes(b"data")
        with mock.patch.object(log_archive, "copy_with_hash", side_effect=PermissionError("in use")):
            self.assertIsNone(log_archive.observe(self.root))
        self.assertEqual(self.kept(), [])
        row = json.loads((self.root / log_archive.FAILURES).read_text(encoding="utf-8"))
        self.assertEqual(row["error"], "PermissionError")

    def test_even_a_failed_failure_record_does_not_raise(self):
        self.source.write_bytes(b"data")
        with mock.patch.object(log_archive, "copy_with_hash", side_effect=OSError("x")), mock.patch("builtins.open", side_effect=OSError("disk")):
            self.assertIsNone(log_archive.observe(self.root))

    def test_no_source_is_not_a_failure(self):
        self.assertIsNone(log_archive.observe(self.root))
        self.assertFalse((self.root / log_archive.FAILURES).exists())

    def test_the_hook_calls_it_inside_its_own_guard(self):
        text = (Path(log_archive.__file__).parent / "restock_policy.py").read_text(encoding="utf-8")
        spot = text.index("from log_archive import observe")
        self.assertIn("try:", text[spot - 40:spot])
        self.assertIn("except Exception:", text[spot:spot + 120])


if __name__ == "__main__":
    unittest.main()
