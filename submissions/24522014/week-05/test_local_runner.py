"""One real localhost HTTP check; scripted model, no API calls or graded rows."""

import csv
import secrets
import socket
import tempfile
import unittest
from pathlib import Path

from run_experiment import BASE, local_market, run_batch
from test_negotiation_host import ScriptedModel


class LocalRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_market_and_sdk_clients_over_tcp(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server_url = f"http://127.0.0.1:{port}/mcp"
        admin = secrets.token_urlsafe(32)
        model = ScriptedModel([("propose", {"price": 130}), ("accept_proposal", {})])
        scenario = {"id": 1, "item": "bicycle", "reserve": 120, "budget": 150}
        with tempfile.TemporaryDirectory(dir=BASE) as directory:
            root = Path(directory)
            async with local_market(server_url, admin):
                failed = await run_batch([scenario], "prompt", 1, server_url, admin,
                                         root / "results.csv", root / "logs", lambda _: model)
            self.assertEqual(failed, 0)
            with (root / "results.csv").open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual((row["outcome"], row["price"], row["tool_calls"]), ("deal", "130", "4"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
