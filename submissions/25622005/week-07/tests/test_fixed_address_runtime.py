"""A dropped tunnel must not change the URL or terminate active shop tasks."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixed_address_runtime import FixedAddressRuntime, load_fixed_config


class FixedRuntimeTests(unittest.TestCase):
    def test_validate_fixed_origin_and_command(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps({"public_url": "https://shop.example/", "tunnel_command": ["tunnel", "run"]}))
            self.assertEqual(load_fixed_config(path), ("https://shop.example", ["tunnel", "run"]))
            for bad in ["http://shop.example", "https://token@shop.example", "https://shop.example/path"]:
                path.write_text(json.dumps({"public_url": bad, "tunnel_command": ["tunnel"]}))
                with self.assertRaises(ValueError):
                    load_fixed_config(path)

    def test_reconnect_keeps_kit_and_fixed_url(self):
        with tempfile.TemporaryDirectory() as folder, patch("fixed_address_runtime.subprocess.Popen") as popen:
            tunnel, kit, replacement = [Mock(pid=n) for n in [1, 2, 3]]
            for proc in [tunnel, kit, replacement]:
                proc.poll.return_value = None
            popen.side_effect = [tunnel, kit, replacement]
            stop = Mock()
            runtime = FixedAddressRuntime("https://shop.example", ["tunnel"],
                                          ["kit", "--public-url", "https://shop.example"],
                                          Path(folder), None, None, Mock(), stop, lambda _: True)
            runtime.tick(0)
            tunnel.poll.return_value = 1
            runtime.tick(11)
            self.assertIs(runtime.kit, kit)
            self.assertEqual(popen.call_count, 3)
            stop.assert_not_called()
            self.assertEqual(runtime.kit_command[-1], "https://shop.example")

    def test_failed_health_checks_only_restart_tunnel(self):
        with tempfile.TemporaryDirectory() as folder, patch("fixed_address_runtime.subprocess.Popen") as popen:
            tunnel, kit = Mock(pid=1), Mock(pid=2)
            tunnel.poll.return_value = kit.poll.return_value = None
            popen.side_effect = [tunnel, kit]
            stop = Mock()
            runtime = FixedAddressRuntime("https://shop.example", ["tunnel"], ["kit"],
                                          Path(folder), None, None, Mock(), stop, lambda _: False)
            for now in [0, 20, 35, 50]:
                runtime.tick(now)
            stop.assert_called_once_with(tunnel)
            self.assertIs(runtime.kit, kit)


if __name__ == "__main__":
    unittest.main()
