"""Recover when the provider revokes a tunnel without exiting cloudflared."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_runtime import TunnelWatchdog


class TunnelWatchdogTests(unittest.TestCase):
    def test_revoked_tunnel_restarts_without_waiting_for_process_exit(self):
        probe = Mock()
        watchdog = TunnelWatchdog(0, probe)
        reason = watchdog.restart_reason('ERR Unauthorized: Tunnel not found', 1, 'https://shop.example')
        self.assertEqual(reason, 'provider revoked the tunnel')
        probe.assert_not_called()

    def test_three_failures_after_startup_grace_restart(self):
        probe = Mock(return_value=False)
        watchdog = TunnelWatchdog(0, probe)
        url = 'https://shop.example'
        self.assertIsNone(watchdog.restart_reason('', 10, url))
        self.assertIsNone(watchdog.restart_reason('', 54, url))
        probe.assert_not_called()
        for now in (55, 85):
            self.assertIsNone(watchdog.restart_reason('', now, url))
        self.assertEqual(watchdog.restart_reason('', 115, url),
                         'public card failed three consecutive checks')

    def test_success_resets_failure_count(self):
        probe = Mock(side_effect=[False, False, True, False, False, False])
        watchdog = TunnelWatchdog(0, probe)
        url = 'https://shop.example'
        watchdog.restart_reason('', 0, url)
        for now in (45, 75, 105, 135, 165):
            self.assertIsNone(watchdog.restart_reason('', now, url))
        self.assertIsNotNone(watchdog.restart_reason('', 195, url))

    def test_stalled_provisioning_has_deadline(self):
        watchdog = TunnelWatchdog(10, Mock())
        self.assertIsNone(watchdog.restart_reason('', 129, None))
        self.assertEqual(watchdog.restart_reason('', 130, None),
                         'no public URL within 120 seconds')


if __name__ == '__main__':
    unittest.main()
