"""TEST ONLY local readiness checks: no inference, purchases or market calls."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_seller_cancel_http import actual_sdk_server


ROOT = Path(__file__).resolve().parents[1]


class SellerReadinessTests(unittest.TestCase):
    def test_local_http_checks_real_key_and_executable_without_model_or_secret_response(self):
        with tempfile.TemporaryDirectory() as directory, actual_sdk_server() as state:
            key = Path(directory) / "test-only-key.pem"
            key.write_bytes(Ed25519PrivateKey.generate().private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()))
            state.executor.config["shop_key"] = str(key)
            with patch("seller.command_for", return_value=[sys.executable]):
                ready = httpx.get(state.url + "/readyz")
                self.assertEqual((ready.status_code, ready.json()), (200, {"ready": True}))
                key.unlink()
                missing_key = httpx.get(state.url + "/readyz")
                self.assertEqual((missing_key.status_code, missing_key.json()), (503, {"ready": False}))
                key.write_text("TEST ONLY invalid key; never expose this text")
                malformed = httpx.get(state.url + "/readyz")
                self.assertEqual((malformed.status_code, malformed.json()), (503, {"ready": False}))
            self.assertEqual(state.model_contexts, [])

    def test_missing_executable_is_not_reported_as_ready(self):
        with actual_sdk_server() as state, patch("seller.load_shop_key"), patch(
                "seller.command_for", return_value=["/nonexistent/test-only-harness"]):
            response = httpx.get(state.url + "/readyz")
            self.assertEqual((response.status_code, response.json()), (503, {"ready": False}))
            self.assertEqual(state.model_contexts, [])

    def test_cli_with_missing_key_exits_before_becoming_a_gateway_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text(json.dumps({"shop_key": str(Path(directory) / "missing-key.pem"),
                                          "log_dir": str(Path(directory) / "logs")}))
            process = subprocess.run([sys.executable, str(ROOT / "seller.py"), "--port", "0",
                                      "--config", str(config)],
                                     capture_output=True, text=True, timeout=10)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("Seller startup preflight failed", process.stderr)


if __name__ == "__main__":
    unittest.main()
