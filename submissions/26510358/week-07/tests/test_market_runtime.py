"""Polling recovery uses simulated failures and never contacts the live market."""
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import call, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import market_runtime as runtime


class MarketPollingRecoveryTests(unittest.TestCase):
    def test_connection_and_read_timeout_recover_with_same_request(self):
        response = object()
        failures = [httpx.ConnectTimeout("test only"), httpx.ReadTimeout("test only"), response]
        market = runtime.ReliableMarket("https://market.invalid", "test-only-claim")
        try:
            with patch.object(runtime.OFFICIAL_MARKET, "call", side_effect=failures) as request, \
                    patch.object(runtime.time, "sleep") as sleep, \
                    patch.object(runtime.market_kit, "log"):
                self.assertIs(market.call("POST", "/api/orders"), response)
            self.assertEqual(request.call_args_list,
                             [call("POST", "/api/orders", role="buyer")] * 3)
            self.assertEqual(sleep.call_args_list, [call(1), call(2)])
        finally:
            market.c.close()

    def test_results_and_other_requests_are_never_resent(self):
        market = runtime.ReliableMarket("https://market.invalid")
        try:
            for method, path, role in [("POST", "/api/results", "buyer"),
                                       ("GET", "/api/shops", "buyer"),
                                       ("POST", "/api/orders", "shop")]:
                with self.subTest(path=path, role=role), \
                        patch.object(runtime.OFFICIAL_MARKET, "call",
                                     side_effect=httpx.ReadTimeout("test only")) as request, \
                        patch.object(runtime.time, "sleep") as sleep:
                    with self.assertRaises(httpx.ReadTimeout):
                        market.call(method, path, role=role)
                    self.assertEqual(request.call_count, 1)
                    sleep.assert_not_called()
        finally:
            market.c.close()

    def test_retry_delay_is_bounded_and_shutdown_interrupts_backoff(self):
        market = runtime.ReliableMarket("https://market.invalid")
        try:
            with patch.object(runtime.OFFICIAL_MARKET, "call",
                              side_effect=httpx.ConnectError("test only")), \
                    patch.object(runtime.time, "sleep",
                                 side_effect=[None] * 7 + [SystemExit(0)]) as sleep, \
                    patch.object(runtime.market_kit, "log"):
                with self.assertRaises(SystemExit):
                    market.call("POST", "/api/orders")
            self.assertEqual([args.args[0] for args in sleep.call_args_list],
                             [1, 2, 4, 8, 16, 30, 30, 30])
        finally:
            market.c.close()

    def test_http_errors_remain_for_official_kit_to_handle(self):
        response = httpx.Response(429)
        market = runtime.ReliableMarket("https://market.invalid")
        try:
            with patch.object(runtime.OFFICIAL_MARKET, "call", return_value=response) as request, \
                    patch.object(runtime.time, "sleep") as sleep:
                self.assertIs(market.call("POST", "/api/orders"), response)
            self.assertEqual(request.call_count, 1)
            sleep.assert_not_called()
        finally:
            market.c.close()

    def test_permanent_protocol_configuration_error_is_not_retried(self):
        market = runtime.ReliableMarket("https://market.invalid")
        try:
            with patch.object(runtime.OFFICIAL_MARKET, "call",
                              side_effect=httpx.UnsupportedProtocol("test only")) as request, \
                    patch.object(runtime.time, "sleep") as sleep:
                with self.assertRaises(httpx.UnsupportedProtocol):
                    market.call("POST", "/api/orders")
            self.assertEqual(request.call_count, 1)
            sleep.assert_not_called()
        finally:
            market.c.close()


class MarketOwnerLoadingTests(unittest.TestCase):
    def test_empty_file_waits_until_operator_supplies_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.env"
            path.write_text("MARKET_OWNER=\n")
            def provide_claim(seconds):
                path.write_text("export MARKET_OWNER='test-only-claim' # operator input\n")
            with patch.object(runtime.time, "sleep", side_effect=provide_claim) as sleep, \
                    patch.object(runtime.market_kit, "log") as log:
                self.assertEqual(runtime.wait_for_owner(path), "test-only-claim")
            sleep.assert_called_once_with(15)
            self.assertNotIn("test-only-claim", str(log.call_args_list))

    def test_ambiguous_claim_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.env"
            for text in ["MARKET_OWNER=first\nMARKET_OWNER=second\n", "MARKET_OWNER=two values\n"]:
                path.write_text(text)
                with self.subTest(text=text), self.assertRaises(ValueError):
                    runtime.read_owner(path)

    def test_claim_is_used_without_inheriting_it_in_buyer_environment(self):
        observed = []
        def official_main():
            market = runtime.market_kit.Market("https://market.invalid", None)
            try:
                observed.append((market.owner, "MARKET_OWNER" in os.environ,
                                 isinstance(market, runtime.ReliableMarket)))
            finally:
                market.c.close()
        for arguments in [[], ["--owner=test-only-cli-claim"]]:
            with self.subTest(arguments=arguments), \
                    patch.dict(os.environ, {"MARKET_OWNER": "test-only-claim"}), \
                    patch.object(sys, "argv", ["market_runtime.py", "join", *arguments]), \
                    patch.object(runtime.signal, "signal"), \
                    patch.object(runtime.market_kit, "Market"), \
                    patch.object(runtime.market_kit, "main", side_effect=official_main), \
                    patch.object(runtime.market_kit, "log"):
                runtime.main()
        self.assertEqual(observed, [("test-only-claim", False, True),
                                    ("test-only-cli-claim", False, True)])


if __name__ == "__main__":
    unittest.main()
