"""Exercise tunnel renewal without contacting a market or sending an order."""
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
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


class V3CheckoutTests(unittest.TestCase):
    def test_shop_only_runtime_exposes_signed_checkout_before_publication(self):
        """Exercise the real v3 proxy through our runtime, without a live market."""
        import httpx
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        path = Path(__file__).parents[4] / 'scripts' / 'market_kit.py'
        kit_spec = importlib.util.spec_from_file_location('v3_kit_test', path)
        kit = importlib.util.module_from_spec(kit_spec)
        kit_spec.loader.exec_module(kit)
        key = Ed25519PrivateKey.generate()
        observed = {}

        def inspect_proxy(port):
            base = f'http://127.0.0.1:{port}'
            with httpx.Client(timeout=5, trust_env=False) as client:
                descriptor = client.get(base + '/.well-known/acp.json')
                self.assertEqual(descriptor.status_code, 200)
                self.assertIn('ap2-mandate', descriptor.json()['capabilities']['payment_methods'])
                receipt = {'shop': 'Seochanit Shop', 'item': 'lamp', 'price': 100, 'task': 'setup-v3-test'}
                import base64
                receipt['sig_seller'] = base64.b64encode(key.sign(kit.canon(receipt))).decode()
                body = {'line_items': [{'id': 'lamp', 'quantity': 1}], 'receipt': receipt}
                created = client.post(base + '/acp/checkout_sessions', json=body)
                self.assertEqual(created.status_code, 201)
                signed = kit.jwt_payload(created.json()['checkout_jwt'], kit.pub_b64(key))
                self.assertEqual((signed['item'], signed['price']), ('lamp', 100))
                self.assertEqual(client.post(base + '/acp/checkout_sessions', json=body).status_code, 409)
                receipt['price'] = 1
                self.assertEqual(client.post(base + '/acp/checkout_sessions', json=body).status_code, 400)
                observed['descriptor'] = descriptor.json()
            raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as folder:
            args, _ = ShopRuntimeTests().fixture(folder)
            fake_seller = MagicMock()
            fake_seller.poll.return_value = None
            with patch.object(kit, 'load_key', return_value=key), \
                 patch.object(kit, 'Market') as market, \
                 patch.object(kit, 'start', return_value=fake_seller), \
                 patch.object(kit, 'pump'), patch.object(kit, 'stop'), \
                 patch.object(kit, 'wait_card', return_value=True), \
                 patch.object(kit, 'open_tunnel', side_effect=inspect_proxy):
                runtime.run_shop(args, kit)
                market.return_value.call.assert_not_called()
        self.assertEqual(observed['descriptor']['protocol'], 'acp')


if __name__ == "__main__":
    unittest.main()
