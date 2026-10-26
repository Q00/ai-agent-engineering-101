"""Offline persistence checks; simulated state never enters market history."""
import json
from pathlib import Path
import tempfile
import unittest

from pricing_state import load_item_state, save_item_state


class PricingStateTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "simulated-state.json"

    def test_initial_prices(self):
        for item, reserve, ask in [("lamp", 100, 115), ("chair", 180, 207),
                                   ("bookshelf", 260, 299)]:
            state = load_item_state(item, reserve, self.path)
            self.assertEqual(state["ask"], ask)
            self.assertIsNone(state["last_reviewed_task"])
        self.assertFalse(self.path.exists())

    def test_price_and_checkpoint_survive_reload(self):
        state = load_item_state("lamp", 100, self.path)
        state.update(ask=118, last_reviewed_task="simulated-sixth-result")
        save_item_state("lamp", 100, state, self.path)
        self.assertEqual(load_item_state("lamp", 100, self.path), state)

    def test_saving_one_item_preserves_other_items(self):
        lamp = load_item_state("lamp", 100, self.path)
        save_item_state("lamp", 100, lamp, self.path)
        chair = load_item_state("chair", 180, self.path)
        save_item_state("chair", 180, chair, self.path)
        self.assertEqual(load_item_state("lamp", 100, self.path), lamp)
        self.assertEqual(load_item_state("chair", 180, self.path), chair)
        self.assertEqual(list(self.path.parent.glob("pricing-*.tmp")), [])

    def test_invalid_state_cannot_overwrite_valid_state(self):
        state = load_item_state("lamp", 100, self.path)
        save_item_state("lamp", 100, state, self.path)
        original = self.path.read_bytes()
        for invalid in (dict(state, ask=99), dict(state, ask=115.5),
                        dict(state, last_reviewed_task="")):
            with self.assertRaises(ValueError):
                save_item_state("lamp", 100, invalid, self.path)
        self.assertEqual(self.path.read_bytes(), original)

    def test_corrupt_file_is_preserved_instead_of_silently_reset(self):
        self.path.write_text("{unfinished", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            load_item_state("lamp", 100, self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{unfinished")


if __name__ == "__main__":
    unittest.main(verbosity=2)
