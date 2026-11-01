"""Runtime supervisor, fixed address, claim code and tunnel DNS, offline and in temp dirs only.

Ported from week-07 tests (fixed address, tunnel DNS, runtime paths, market owner,
tunnel watchdog) and extended for week-08: the local kit copy is preferred, stop()
tolerates EPERM, and the kit gets MARKET_OWNER and OWN_SHOP_URL on every launch path.
"""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import configure_ngrok
from configure_market_owner import kit_environment, save_owner
import fixed_address_runtime
from fixed_address_runtime import FixedAddressRuntime, kit_env, load_fixed_config
import market_runtime
from market_runtime import TunnelWatchdog, public_url_from_line
import tunnel_dns


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

    def test_fixed_kit_gets_claim_code_and_own_url_but_tunnel_does_not(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ), \
                patch("fixed_address_runtime.subprocess.Popen") as popen:
            os.environ.pop("MARKET_OWNER", None)
            save_owner(folder, "synthetic-claim-code")
            tunnel, kit = Mock(pid=1), Mock(pid=2)
            tunnel.poll.return_value = kit.poll.return_value = None
            popen.side_effect = [tunnel, kit]
            runtime = FixedAddressRuntime("https://shop.example", ["tunnel"], ["kit"],
                                          Path(folder), None, None, Mock(), Mock(), lambda _: True)
            runtime.tick(0)
            tunnel_call, kit_call = popen.call_args_list
            self.assertNotIn("MARKET_OWNER", tunnel_call.kwargs.get("env") or {})
            self.assertEqual(kit_call.kwargs["env"]["MARKET_OWNER"], "synthetic-claim-code")
            self.assertEqual(kit_call.kwargs["env"]["OWN_SHOP_URL"], "https://shop.example")

    def test_kit_env_without_claim_code_keeps_inherited_environment(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"A": "B"}):
            os.environ.pop("MARKET_OWNER", None)
            environment = kit_env(folder, "https://shop.example")
            self.assertEqual(environment["A"], "B")
            self.assertNotIn("MARKET_OWNER", environment)
            self.assertNotIn("OWN_SHOP_URL", os.environ)

    def test_ngrok_config_points_at_this_folder_and_validates(self):
        with tempfile.TemporaryDirectory() as folder:
            config = configure_ngrok.prepare_config(Path(folder), "https://shop.ngrok-free.app/")
            self.assertEqual(config["public_url"], "https://shop.ngrok-free.app")
            self.assertTrue(config["tunnel_command"][0].startswith(str(Path(folder) / ".market")))
            self.assertNotIn("authtoken", " ".join(config["tunnel_command"]))
            path = Path(folder) / "fixed.json"
            path.write_text(json.dumps(config))
            self.assertEqual(load_fixed_config(path)[0], "https://shop.ngrok-free.app")


class StopTests(unittest.TestCase):
    def test_permission_error_on_terminate_is_tolerated(self):
        proc = Mock(pid=4242)
        proc.poll.return_value = None
        with patch("market_runtime.os.killpg", side_effect=PermissionError) as killpg:
            market_runtime.stop(proc)
        killpg.assert_called_once_with(4242, signal.SIGTERM)

    def test_permission_error_on_kill_falls_back_to_the_process(self):
        proc = Mock(pid=4242)
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired("kit", 10), 0]
        with patch("market_runtime.os.killpg", side_effect=[None, PermissionError]):
            market_runtime.stop(proc)
        proc.kill.assert_called_once()
        self.assertEqual(proc.wait.call_count, 2)

    def test_finished_or_missing_process_is_left_alone(self):
        with patch("market_runtime.os.killpg") as killpg:
            market_runtime.stop(None)
            done = Mock()
            done.poll.return_value = 0
            market_runtime.stop(done)
        killpg.assert_not_called()


class _Stop(Exception):
    pass


class MainLaunchTests(unittest.TestCase):
    """Run main() with fake processes until its first sleep, then capture the kit launch."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.kit_path = self.folder / "market_kit.py"
        self.kit_path.write_text("# fixture\n")
        save_owner(self.folder / ".market", "synthetic-claim-code")
        handlers = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
        self.addCleanup(lambda: [signal.signal(s, h) for s, h in handlers.items()])
        original = socket.getaddrinfo
        self.addCleanup(setattr, socket, "getaddrinfo", original)

    def run_main(self, extra, popen_target, popen):
        argv = ["market_runtime.py", "--name", "psyoung82", "--dir", str(self.folder),
                "--kit", str(self.kit_path), "--port", "8900"] + extra
        with patch.dict(os.environ), patch.object(sys, "argv", argv), \
                patch(popen_target, side_effect=popen), patch("market_runtime.stop"), \
                patch("time.sleep", side_effect=_Stop), redirect_stdout(io.StringIO()):
            os.environ.pop("MARKET_OWNER", None)
            with self.assertRaises(_Stop):
                market_runtime.main()

    def test_quick_tunnel_path_passes_claim_code_and_url_to_kit(self):
        launches = []

        def popen(command, **kwargs):
            launches.append((command, kwargs))
            proc = Mock(pid=100 + len(launches))
            proc.poll.return_value = None
            proc.stdout = iter(["INF | https://grab-marking.trycloudflare.com |\n"]) if len(launches) == 1 else None
            return proc

        self.run_main([], "market_runtime.subprocess.Popen", popen)
        kit_command, kit_kwargs = launches[1]
        self.assertEqual(kit_command[2], str(self.kit_path.resolve()))
        self.assertEqual(kit_command[-1], "https://grab-marking.trycloudflare.com")
        self.assertEqual(kit_kwargs["env"]["MARKET_OWNER"], "synthetic-claim-code")
        self.assertEqual(kit_kwargs["env"]["OWN_SHOP_URL"], "https://grab-marking.trycloudflare.com")
        self.assertTrue((self.folder / "logs").is_dir())

    def test_fixed_config_path_passes_claim_code_and_url_to_kit(self):
        config = self.folder / ".market" / "fixed-address.json"
        config.write_text(json.dumps({"public_url": "https://shop.example", "tunnel_command": ["tunnel"]}))
        launches = []

        def popen(command, **kwargs):
            launches.append((command, kwargs))
            proc = Mock(pid=200 + len(launches))
            proc.poll.return_value = None
            return proc

        self.run_main(["--fixed-config", str(config)], "fixed_address_runtime.subprocess.Popen", popen)
        self.assertEqual(launches[0][0], ["tunnel"])
        kit_command, kit_kwargs = launches[1]
        self.assertIn("join", kit_command)
        self.assertEqual(kit_command[-1], "https://shop.example")
        self.assertEqual(kit_kwargs["env"]["MARKET_OWNER"], "synthetic-claim-code")
        self.assertEqual(kit_kwargs["env"]["OWN_SHOP_URL"], "https://shop.example")


class RuntimePathTests(unittest.TestCase):
    def test_local_kit_copy_is_preferred_over_course_kit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            course = root / "scripts/market_kit.py"
            course.parent.mkdir()
            course.write_text("# course kit\n")
            week = root / "submissions/student/week-08"
            week.mkdir(parents=True)
            local = week / "market_kit.py"
            local.write_text("# patched copy\n")
            self.assertEqual(market_runtime.default_kit_path(week / "market_runtime.py"), local)
            local.unlink()
            self.assertEqual(market_runtime.default_kit_path(week / "market_runtime.py"), course)

    def test_this_folder_uses_its_own_kit_copy(self):
        if (BASE / "market_kit.py").is_file():
            self.assertEqual(market_runtime.default_kit_path(market_runtime.__file__),
                             (BASE / "market_kit.py").resolve())

    def test_default_folder_is_this_week(self):
        with patch.object(market_runtime, "default_kit_path", return_value=Path(__file__)):
            args = market_runtime.parse_arguments(["--name", "psyoung82"])
        self.assertEqual(args.dir, BASE.resolve())

    def test_shallow_container_path_uses_container_kit(self):
        container = Path('/opt/market/market_kit.py')
        with patch.object(Path, 'is_file', autospec=True, side_effect=lambda p: p == container):
            self.assertEqual(market_runtime.default_kit_path('/data/market_runtime.py'), container)
            self.assertEqual(market_runtime.default_kit_path('/market_runtime.py'), container)

    def test_explicit_kit_does_not_evaluate_default_discovery(self):
        with patch.object(market_runtime, '__file__', '/data/market_runtime.py'), \
                patch.object(market_runtime, 'default_kit_path', side_effect=AssertionError('must not run')), \
                patch.object(Path, 'is_file', return_value=True):
            args = market_runtime.parse_arguments(['--name', 'fixture', '--kit', '/custom/kit.py'])
        self.assertEqual(args.kit, Path('/custom/kit.py'))

    def test_missing_kit_has_actionable_argument_error(self):
        with patch.object(market_runtime, 'default_kit_path', return_value=None), \
                redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit) as raised:
            market_runtime.parse_arguments(['--name', 'fixture'])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn('specify --kit', errors.getvalue())

    def test_help_needs_no_kit_or_network(self):
        with patch.object(market_runtime, 'default_kit_path', side_effect=AssertionError('must not run')), \
                redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as raised:
            market_runtime.parse_arguments(['--help'])
        self.assertEqual(raised.exception.code, 0)


class MarketOwnerConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.private = Path(self.temp.name) / '.market'

    def test_absent_local_code_preserves_inherited_environment(self):
        self.assertEqual(kit_environment(self.private, {'A': 'B'}), {'A': 'B'})

    def test_saved_code_is_private_and_used_without_mutating_caller_environment(self):
        save_owner(self.private, ' synthetic-claim-code ')
        self.assertEqual((self.private / 'market-owner').stat().st_mode & 0o777, 0o600)
        inherited = {'A': 'B'}
        environment = kit_environment(self.private, inherited)
        self.assertEqual(environment['MARKET_OWNER'], 'synthetic-claim-code')
        self.assertNotIn('MARKET_OWNER', inherited)

    def test_invalid_input_retains_existing_code(self):
        save_owner(self.private, 'synthetic-claim-code')
        with self.assertRaises(ValueError):
            save_owner(self.private, 'bad code')
        self.assertEqual(kit_environment(self.private, {})['MARKET_OWNER'], 'synthetic-claim-code')

    def test_invalid_local_file_error_does_not_disclose_contents(self):
        self.private.mkdir()
        (self.private / 'market-owner').write_text('sensitive bad value')
        with self.assertRaises(ValueError) as caught:
            kit_environment(self.private, {})
        self.assertNotIn('sensitive', str(caught.exception))


class TunnelWatchdogTests(unittest.TestCase):
    def test_provider_api_failure_is_not_a_public_tunnel(self):
        self.assertIsNone(public_url_from_line(
            'failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": timeout'))
        self.assertEqual(public_url_from_line(
            'INF | https://grab-marking-relating-alpha.trycloudflare.com |'),
            'https://grab-marking-relating-alpha.trycloudflare.com')
        self.assertEqual(public_url_from_line('https://324bf083803a15.lhr.life'),
                         'https://324bf083803a15.lhr.life')
        self.assertIsNone(public_url_from_line('https://shop.trycloudflare.com.example.org'))

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

    def test_stalled_provisioning_has_deadline(self):
        watchdog = TunnelWatchdog(10, Mock())
        self.assertIsNone(watchdog.restart_reason('', 129, None))
        self.assertEqual(watchdog.restart_reason('', 130, None),
                         'no public URL within 120 seconds')

    def test_card_check_requires_our_name(self):
        response = Mock()
        response.json.return_value = {"name": "psyoung82 shop", "supportedInterfaces": [{}]}
        client = Mock()
        client.get.return_value = response
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        with patch("fixed_address_runtime.httpx.Client", return_value=client):
            self.assertTrue(fixed_address_runtime.public_card_healthy("https://shop.example", "psyoung82"))
            self.assertFalse(fixed_address_runtime.public_card_healthy("https://shop.example", "someone"))


class TunnelDnsTests(unittest.TestCase):
    def setUp(self):
        tunnel_dns._cache.clear()
        original = socket.getaddrinfo
        self.addCleanup(setattr, socket, "getaddrinfo", original)

    def test_native_success_does_not_query_https_dns(self):
        with patch('socket.getaddrinfo', return_value=['native']), \
                patch('tunnel_dns.public_addresses') as fallback:
            tunnel_dns.install()
            self.assertEqual(socket.getaddrinfo('shop.trycloudflare.com', 443), ['native'])
            fallback.assert_not_called()

    def test_failed_tunnel_lookup_resolves_only_socket_address(self):
        def native(host, *args):
            if host == 'shop.trycloudflare.com':
                raise socket.gaierror('cached negative lookup')
            return [('resolved', host)]
        with patch('socket.getaddrinfo', side_effect=native), \
                patch('tunnel_dns.public_addresses', return_value=['104.16.230.132']) as fallback:
            tunnel_dns.install()
            self.assertEqual(socket.getaddrinfo('shop.trycloudflare.com', 443),
                             [('resolved', '104.16.230.132')])
            fallback.assert_called_once()

    def test_other_hosts_do_not_use_fallback(self):
        for host in ('market.ourolabs.ai', 'localhost', 'shop.trycloudflare.com.example.org'):
            with self.subTest(host=host), patch('socket.getaddrinfo', side_effect=socket.gaierror()), \
                    patch('tunnel_dns.public_addresses') as fallback:
                tunnel_dns.install()
                with self.assertRaises(socket.gaierror):
                    socket.getaddrinfo(host, 443)
                fallback.assert_not_called()

    def test_https_query_keeps_only_public_ips_and_caches_them(self):
        data = {'Status': 0, 'Answer': [{'type': 1, 'data': '104.16.230.132', 'TTL': 300},
                                      {'type': 1, 'data': '127.0.0.1', 'TTL': 300}]}
        response = Mock()
        response.__enter__ = Mock(return_value=io.BytesIO(json.dumps(data).encode()))
        response.__exit__ = Mock(return_value=False)
        with patch('tunnel_dns.urlopen', return_value=response) as request, redirect_stderr(io.StringIO()):
            self.assertEqual(tunnel_dns.public_addresses('shop.trycloudflare.com', 0), ['104.16.230.132'])
            tunnel_dns.public_addresses('shop.trycloudflare.com', 0)
            request.assert_called_once()

    def test_failed_https_query_preserves_native_error(self):
        original = socket.gaierror('native failure')
        with patch('socket.getaddrinfo', side_effect=original), \
                patch('tunnel_dns.public_addresses', side_effect=OSError('offline')):
            tunnel_dns.install()
            with self.assertRaises(socket.gaierror) as caught:
                socket.getaddrinfo('shop.trycloudflare.com', 443)
            self.assertIs(caught.exception, original)

    def test_install_is_idempotent(self):
        with patch('socket.getaddrinfo', return_value=[]):
            tunnel_dns.install()
            first = socket.getaddrinfo
            tunnel_dns.install()
            self.assertIs(socket.getaddrinfo, first)


if __name__ == "__main__":
    unittest.main()
