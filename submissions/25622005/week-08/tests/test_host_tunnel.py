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
        self.assertIsNone(ht.verdict(NOW, NOW - ht.SILENT_S + 1, NOW - 30, 0, 0))

    def test_two_minutes_is_the_threshold(self):
        self.assertEqual(ht.SILENT_S, 120)      # 11-12: 30-50 rows/min fall to 0 inside two minutes when the tunnel dies
        self.assertTrue(ht.verdict(NOW, NOW - 120, NOW - 30, 0, 0))

    def test_probe_failures_restart(self):
        self.assertIsNone(ht.verdict(NOW, NOW - 10, NOW - 10, ht.PROBE_FAILS - 1, 0))
        self.assertEqual(ht.verdict(NOW, NOW - 10, NOW - 10, ht.PROBE_FAILS, 0), "public card failed 3 checks")

    def test_cooldown_after_a_restart(self):
        self.assertIsNone(ht.verdict(NOW, NOW - 400, NOW - 30, ht.PROBE_FAILS, NOW - ht.COOLDOWN_S + 1))
        self.assertTrue(ht.verdict(NOW, NOW - 400, NOW - 30, 0, NOW - ht.COOLDOWN_S))


class DataDirTest(unittest.TestCase):
    """The data folder is never a path of one machine: environment, then .env next to compose.yaml, then ./data."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.here = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_is_data_next_to_compose(self):
        self.assertEqual(ht.data_dir(self.here, {}), self.here / "data")

    def test_dotenv_then_environment(self):
        (self.here / ".env").write_text('OTHER=1\nMARKET_DATA_DIR="/srv/market data"\n', encoding="utf-8")
        self.assertEqual(ht.data_dir(self.here, {}), Path("/srv/market data"))
        self.assertEqual(ht.data_dir(self.here, {"MARKET_DATA_DIR": "/from/env"}), Path("/from/env"))

    def test_relative_value_is_taken_from_the_compose_folder(self):
        (self.here / ".env").write_text("MARKET_DATA_DIR=state/live\n", encoding="utf-8")
        self.assertEqual(ht.data_dir(self.here, {}), self.here / "state" / "live")


if __name__ == "__main__":
    unittest.main()
