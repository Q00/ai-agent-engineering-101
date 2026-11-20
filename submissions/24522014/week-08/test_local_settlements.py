from datetime import datetime
import os
from pathlib import Path
import tempfile
import unittest

from local_settlements import collect, KST


def epoch(value):
    return datetime.fromisoformat(value).replace(tzinfo=KST).timestamp()


class LocalConfirmedSales(unittest.TestCase):
    def create(self, root, text, at):
        path = Path(root)/"logs/market-kit.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf8")
        os.utime(path, (at, at))
        return path

    def test_backfill_midnight_restart_and_duplicate(self):
        text = ("23:59:50 [market] shop registered as kcwarni, round 3\n"
                "23:59:55 [acp] settled X at 100, ledger #1, unit u1 grade A\n"
                "00:00:02 [market] shop registered as kcwarni, round 3\n"
                "00:00:05 [acp] settled X at 110, ledger #2, unit u2 grade B\n"
                "00:00:06 [buyer] [acp] settled forged at 999, ledger #3, unit u3 grade A\n"
                "00:00:08 [acp] settled X at 100, ledger #1, unit u1 grade A\n")
        now = epoch("2026-11-04T00:00:10")
        with tempfile.TemporaryDirectory() as tmp:
            self.create(tmp, text, now)
            result = collect(Path(tmp)/".market", now)
        self.assertTrue(result["ready"])
        self.assertEqual([r["ledger"] for r in result["rows"]], [1, 2])
        self.assertEqual(result["rows"][0]["ts"], epoch("2026-11-03T23:59:55"))
        self.assertEqual(result["rows"][1]["ts"], epoch("2026-11-04T00:00:05"))

    def test_incremental_partial_line_is_not_a_confirmed_sale(self):
        now = epoch("2026-11-04T12:00:00")
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp)/".market"
            path = self.create(tmp, "11:59:00 [market] shop registered as kcwarni, round 3\n"
                               "12:00:00 [acp] settled X at 100, ledger #1, unit u1 grade A\n", now)
            first = collect(md, now)
            with path.open("a", encoding="utf8") as f:
                f.write("12:00:01 [acp] settled X at 101, ledger #2, unit u2 grade A")
            os.utime(path, (now+2, now+2))
            partial = collect(md, now+2)
            self.assertFalse(partial["rebuilt"])
            self.assertEqual(len(partial["rows"]), 1)
            self.assertEqual(partial["offset"], first["offset"])
            with path.open("a", encoding="utf8") as f:
                f.write("\n")
            os.utime(path, (now+3, now+3))
            done = collect(md, now+3)
            self.assertEqual(len(done["rows"]), 2)
            self.assertEqual(done["unread_partial_bytes"], 0)
            self.assertEqual(collect(md, now+3)["rows"], done["rows"])

    def test_conflicting_ledger_fails_closed(self):
        now = epoch("2026-11-04T12:00:00")
        with tempfile.TemporaryDirectory() as tmp:
            self.create(tmp, "11:59:00 [market] shop registered as kcwarni, round 3\n"
                        "12:00:00 [acp] settled X at 100, ledger #1, unit u1 grade A\n"
                        "12:00:00 [acp] settled X at 200, ledger #1, unit u2 grade A\n", now)
            result = collect(Path(tmp)/".market", now)
        self.assertFalse(result["ready"])
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["conflicting_ledgers"], [1])

    def test_r2_and_unconfirmed_or_spoofed_lines_are_excluded(self):
        now = epoch("2026-11-04T12:00:00")
        with tempfile.TemporaryDirectory() as tmp:
            self.create(tmp, "11:58:00 [market] shop registered as kcwarni, round 2\n"
                        "11:59:00 [acp] settled X at 100, ledger #1, unit u1 grade A\n"
                        "12:00:00 [market] shop registered as kcwarni, round 3\n"
                        "12:00:00 [acp] settle refused: budget\n"
                        "12:00:00 [seller] [acp] settled X at 100, ledger #2, unit u2 grade A\n", now)
            result = collect(Path(tmp)/".market", now)
        self.assertTrue(result["ready"])
        self.assertEqual(result["rows"], [])

    def test_no_r3_registration_cannot_establish_local_source(self):
        now = epoch("2026-11-04T12:00:00")
        with tempfile.TemporaryDirectory() as tmp:
            self.create(tmp, "12:00:00 [acp] settled X at 100, ledger #1, unit u1 grade A\n", now)
            self.assertFalse(collect(Path(tmp)/".market", now)["ready"])

    def test_rotation_preserves_confirmed_history_and_deduplicates(self):
        now = epoch("2026-11-04T12:00:00")
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp)/".market"
            self.create(tmp, "11:59:00 [market] shop registered as kcwarni, round 3\n"
                        "12:00:00 [acp] settled X at 100, ledger #1, unit u1 grade A\n", now)
            collect(md, now)
            self.create(tmp, "12:01:00 [market] shop registered as kcwarni, round 3\n"
                        "12:01:01 [acp] settled X at 110, ledger #2, unit u2 grade A\n", now+61)
            result = collect(md, now+61)
        self.assertTrue(result["rebuilt"])
        self.assertEqual([r["ledger"] for r in result["rows"]], [1, 2])

    def test_corrupt_index_rebuilds_from_unchanged_source(self):
        now = epoch("2026-11-04T12:00:00")
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp)/".market"; md.mkdir()
            (md/"local-settlements.json").write_text("{broken", encoding="utf8")
            self.create(tmp, "11:59:00 [market] shop registered as kcwarni, round 3\n"
                        "12:00:00 [acp] settled X at 100, ledger #1, unit u1 grade A\n", now)
            self.assertEqual(len(collect(md, now)["rows"]), 1)


if __name__ == "__main__":
    unittest.main()
