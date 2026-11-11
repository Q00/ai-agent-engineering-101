"""docker/host_tunnel.py's watchdog verdict: restart cloudflared only when our inbound path alone is dead."""
import importlib.util
import sys
import unittest
from pathlib import Path

W = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("host_tunnel", W / "docker" / "host_tunnel.py")
ht = importlib.util.module_from_spec(spec)
sys.modules["host_tunnel"] = ht
spec.loader.exec_module(ht)

NOW = 1_794_400_000.0


class VerdictTest(unittest.TestCase):
    def test_seller_silent_while_the_buyer_hook_runs_restarts(self):
        why = ht.verdict(NOW, seller_mtime=NOW - 400, buyer_mtime=NOW - 30, probe_fails=0, last_restart=0)
        self.assertEqual(why, "seller silent 400 s while the buyer hook ran 30 s ago")

    def test_a_quiet_market_is_not_a_reason(self):
        self.assertIsNone(ht.verdict(NOW, NOW - 400, NOW - 400, 0, 0))     # both logs still: nothing to serve

    def test_short_silence_is_not_a_reason(self):
        self.assertIsNone(ht.verdict(NOW, NOW - 299, NOW - 30, 0, 0))

    def test_probe_failures_restart(self):
        self.assertIsNone(ht.verdict(NOW, NOW - 10, NOW - 10, ht.PROBE_FAILS - 1, 0))
        self.assertEqual(ht.verdict(NOW, NOW - 10, NOW - 10, ht.PROBE_FAILS, 0), "public card failed 3 checks")

    def test_cooldown_after_a_restart(self):
        self.assertIsNone(ht.verdict(NOW, NOW - 400, NOW - 30, ht.PROBE_FAILS, NOW - ht.COOLDOWN_S + 1))
        self.assertTrue(ht.verdict(NOW, NOW - 400, NOW - 30, 0, NOW - ht.COOLDOWN_S))


if __name__ == "__main__":
    unittest.main()
