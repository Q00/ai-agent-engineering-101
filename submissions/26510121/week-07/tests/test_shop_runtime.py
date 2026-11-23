"""Exercise tunnel renewal without contacting a market or sending an order."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch


spec = importlib.util.spec_from_file_location("shop_runtime", Path(__file__).parents[1] / "agent-market" / "shop_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class ShopRuntimeTests(unittest.TestCase):
    def fixture(self, directory):
        Path(directory, "agent.json").write_text(json.dumps({"seller": {"cmd": "python seller.py --port {port}"}}))
        args = SimpleNamespace(dir=directory, market="https://market.example", owner="test-owner", name="student", port=0)
        kit = MagicMock()
        kit.KIT_VERSION = "test"
        kit.start.return_value.poll.return_value = None
        kit.wait_card.return_value = True
        kit.free_port.side_effect = [8901, 8902]
        kit.Market.return_value.join.return_value = {"name": "student", "round": 2}
        return args, kit

    def test_renewed_url_uses_same_key_and_seller_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as folder:
            args, kit = self.fixture(folder)
            first, second = MagicMock(), MagicMock()
            first.poll.return_value = 0
            second.poll.return_value = None
            kit.open_tunnel.side_effect = [(first, "https://first.example"), (second, "https://second.example")]
            with patch.object(runtime.time, "sleep", side_effect=[None, KeyboardInterrupt]), patch.object(runtime.time, "monotonic", side_effect=[0, 3600, 3601]):
                runtime.run_shop(args, kit)
            joins = kit.Market.return_value.join.call_args_list
            self.assertEqual([call.args[3] for call in joins], ["https://first.example", "https://second.example"])
            self.assertIs(joins[0].args[2], joins[1].args[2])
            kit.start.assert_called_once()
            kit.load_key.assert_called_once()
            self.assertTrue(kit.Proxy.return_value.server.should_exit)
            kit.Market.return_value.c.close.assert_called_once()
            kit.stop.assert_any_call(kit.start.return_value)

    def test_repeated_provisioning_failure_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            args, kit = self.fixture(folder)
            kit.open_tunnel.side_effect = RuntimeError("unavailable")
            with patch.object(runtime.time, "sleep") as sleep:
                with self.assertRaisesRegex(RuntimeError, "repeated failure"):
                    runtime.run_shop(args, kit)
            self.assertEqual(kit.open_tunnel.call_count, 3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [30, 60])
            kit.stop.assert_any_call(kit.start.return_value)


if __name__ == "__main__":
    unittest.main()
