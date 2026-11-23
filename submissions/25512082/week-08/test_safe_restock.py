"""Local contract tests for the approval-gated Round 3 restock hook."""
from __future__ import annotations

import io
import json
import socket
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import safe_restock


ROOT = Path(__file__).resolve().parent


class SafeRestockTests(unittest.TestCase):
    def run_hook(self, state: Path, approval: Path | None = None) -> dict:
        output = io.StringIO()
        argv = ["--state", str(state)]
        if approval is not None:
            argv += ["--approval", str(approval)]
        with patch.object(socket, "socket", side_effect=AssertionError("network access attempted")):
            with redirect_stdout(output):
                self.assertEqual(safe_restock.main(argv), 0)
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertNotIn("bankrupt", lines[0].lower())
        return json.loads(lines[0])

    def make_state(self, root: Path, factor: float = 1.0) -> Path:
        state = root / ".market"
        state.mkdir()
        inventory = {
            "round": 3, "balance": 5_000_000, "units": [], "surge": [],
            "restock": {"open": True, "opens_at": 1_800_000_000, "closes_at": 2_100_000_000},
        }
        if factor != 1.0:
            inventory["surge"] = [{"sku": "G-safe", "factor": factor}]
        (state / "inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
        (state / "catalog.json").write_text(json.dumps({
            "G-safe": {"list_price": 100_000, "kind": "goods", "name": "Safe goods"}
        }), encoding="utf-8")
        return state

    def approval(self, path: Path, **changes) -> Path:
        value = {
            "enabled": True,
            "round": 3,
            "expires_at": 2_000_000_000,
            "purchase_cap": 80_000,
            "lines": [{"sku": "G-safe", "qty": 1, "max_unit_cost": 75_000}],
        }
        value.update(changes)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_without_explicit_approval_never_buys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = self.make_state(Path(temporary))
            self.assertEqual(self.run_hook(state), {"buy": []})

    def test_valid_short_lived_approval_emits_only_approved_line(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.make_state(root)
            approval = self.approval(root / "approval.json")
            with patch("safe_restock.time.time", return_value=1_900_000_000):
                self.assertEqual(self.run_hook(state, approval), {"buy": [{"sku": "G-safe", "qty": 1}]})
            self.assertFalse(approval.exists())
            self.assertTrue((root / "restock-approval.claimed.json").exists())
            self.assertEqual(self.run_hook(state, approval), {"buy": []})

    def test_closed_window_does_not_consume_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.make_state(root)
            inventory = json.loads((state / "inventory.json").read_text())
            inventory["restock"]["open"] = False
            (state / "inventory.json").write_text(json.dumps(inventory))
            approval = self.approval(root / "approval.json")
            self.assertEqual(self.run_hook(state, approval), {"buy": []})
            self.assertTrue(approval.exists())

    def test_expired_or_malformed_approval_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.make_state(root)
            approval = self.approval(root / "approval.json", expires_at=1)
            self.assertEqual(self.run_hook(state, approval), {"buy": []})
            approval.write_text("{bad-json", encoding="utf-8")
            self.assertEqual(self.run_hook(state, approval), {"buy": []})

    def test_surge_that_breaks_margin_or_price_ceiling_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.make_state(root, factor=1.20)
            approval = self.approval(
                root / "approval.json",
                lines=[{"sku": "G-safe", "qty": 1, "max_unit_cost": 90_000}],
                purchase_cap=100_000,
            )
            self.assertEqual(self.run_hook(state, approval), {"buy": []})

    def test_held_sku_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.make_state(root)
            inventory = json.loads((state / "inventory.json").read_text())
            inventory["units"] = [{"sku": "G-safe", "cost": 50_000}]
            (state / "inventory.json").write_text(json.dumps(inventory))
            approval = self.approval(root / "approval.json")
            self.assertEqual(self.run_hook(state, approval), {"buy": []})

    def test_agent_keeps_existing_commands_and_registers_safe_hook(self) -> None:
        agent = json.loads((ROOT / "agent.json").read_text(encoding="utf-8"))
        self.assertEqual(agent["seller"]["cmd"], "sh run_agent.sh seller.py --port {port} --market-dir .market --key shop_key.pem --ledger .market/seller-ledger.json")
        self.assertEqual(agent["buyer"]["cmd"], "sh run_agent.sh buyer.py --url {url} --item {item} --catalog .market/catalog.json --budget {budget}")
        self.assertEqual(agent["market_buyer"]["cmd"], "sh run_agent.sh market_buyer.py --shops {shops} --order {order} --item {item} --budget {budget}")
        self.assertEqual(agent["restock"]["cmd"], "python safe_restock.py --state {state}")


if __name__ == "__main__":
    unittest.main()
