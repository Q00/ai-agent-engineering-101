"""mcp_server: read-only tools only, names without dots, calls go through the registry.

    py -m unittest discover -s tests      # from submissions/26512070/week-08
"""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import mcp_server  # noqa: E402
from tests.test_calc_check import Base  # noqa: E402


class McpTest(Base):
    def test_list_and_call(self):
        tools = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, self.ctx)["result"]["tools"]
        names = {t["name"] for t in tools}
        self.assertIn("me_inventory", names)
        self.assertFalse([n for n in names if n.startswith(("act_", "rec_")) or "." in n])
        r = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": "calc_margin", "arguments": {"unit": "u-goods", "price": 3300}}}, self.ctx)
        self.assertFalse(r["result"]["isError"])
        self.assertEqual(json.loads(r["result"]["content"][0]["text"])["profit"], 300)
        bad = mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                 "params": {"name": "act_buy", "arguments": {"plan": []}}}, self.ctx)
        self.assertIn("error", bad)
        self.assertIsNone(mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, self.ctx))


if __name__ == "__main__":
    unittest.main()
