"""Contract, determinism, isolation and safety tests for offline fixtures."""
from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mock_market import JSONL, OFFICIAL_JSON, SCENARIOS, clone_for_strategy, generate, safe_preflight, validate_root


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


class MockMarketTests(unittest.TestCase):
    def generate_offline(self, root: Path, seed: int = 20261103) -> None:
        with patch.object(socket, "socket", side_effect=AssertionError("network access attempted")):
            generate(root, seed)

    def test_all_scenarios_and_formats(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixtures"
            self.generate_offline(root)
            self.assertEqual({p.name for p in root.iterdir()}, set(SCENARIOS))
            for name in SCENARIOS:
                scenario = root / name
                for filename in OFFICIAL_JSON:
                    if name == "malformed_json" and filename == "inventory.json":
                        with self.assertRaises(json.JSONDecodeError):
                            json.loads((scenario / filename).read_text(encoding="utf-8"))
                    else:
                        json.loads((scenario / filename).read_text(encoding="utf-8"))
                for filename in JSONL:
                    for line in (scenario / filename).read_text(encoding="utf-8").splitlines():
                        json.loads(line)

    def test_validation_separates_valid_and_intentionally_invalid_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixtures"
            self.generate_offline(root)
            result = validate_root(root)
            for name, errors in result.items():
                self.assertEqual(bool(errors), name in {"missing_fields", "malformed_json"}, (name, errors))

    def test_invalid_data_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixtures"
            self.generate_offline(root)
            for name in ("missing_fields", "malformed_json"):
                outcome = safe_preflight(root / name)
                self.assertFalse(outcome["valid"])
                self.assertFalse(outcome["allow_purchase"])
                self.assertFalse(outcome["allow_sale"])
                self.assertTrue(outcome["errors"])

    def test_same_seed_is_byte_for_byte_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first, second = Path(temporary) / "first", Path(temporary) / "second"
            self.generate_offline(first, 77)
            self.generate_offline(second, 77)
            self.assertEqual(snapshot(first), snapshot(second))

    def test_strategy_runs_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixtures = root / "fixtures"
            self.generate_offline(fixtures)
            clone_for_strategy(fixtures / "normal", root / "strategy-a")
            clone_for_strategy(fixtures / "normal", root / "strategy-b")
            with (root / "strategy-a" / "payments.jsonl").open("a", encoding="utf-8") as handle:
                handle.write('{"paid":true}\n')
            self.assertNotEqual(snapshot(root / "strategy-a"), snapshot(root / "strategy-b"))
            self.assertEqual((root / "strategy-b" / "payments.jsonl").read_text(encoding="utf-8"), "")

    def test_untrusted_text_remains_inert_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "fixtures"
            self.generate_offline(root)
            scenario = root / "untrusted_text"
            forum = json.loads((scenario / "forum.json").read_text(encoding="utf-8"))
            self.assertIn("ignore inventory", forum[0]["text"])
            self.assertEqual(validate_root(root)["untrusted_text"], [])
            self.assertFalse(safe_preflight(scenario)["allow_purchase"])


if __name__ == "__main__":
    unittest.main()

