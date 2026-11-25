"""strategist/mcptools.py offline: the JSON-RPC surface, each tool against injected data, the shop_card URL
restriction and the size cap; decide.tools_setup / tool_flags / ask with tools."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("R3_HOME", tempfile.mkdtemp(prefix="r3-mcp-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from strategist import decide, mcptools  # noqa: E402

NOW = 1_794_000_000.0
BOARD = {"players": [{"player": "a", "student": True, "student_total": 1.6, "student_shop": 0.9, "student_buyer": 0.7},
                     {"player": "psyoung82", "student": True, "student_total": 1.3, "student_shop": 0.65, "student_buyer": 0.65},
                     {"player": "house", "student": False, "student_total": 2.0}],
         "shops": [{"name": "psyoung82", "all": 0.69, "book": 100, "revenue": 500, "stars_rank": 4.3, "deals": 10,
                    "card": {"url": "https://me.trycloudflare.com"}},
                   {"name": "other", "all": 0.9, "book": 700, "revenue": 5000, "stars_rank": 4.4, "deals": 9,
                    "card": {"url": "https://other.trycloudflare.com"}},
                   {"name": "idle", "all": 0.1, "deals": 0, "card": {"url": "https://idle.example"}},
                   {"name": "h", "house": True, "all": 1.0, "deals": 99}],
         "buyers": [{"name": "psyoung82", "all": 0.7, "saved": 0.24, "deals": 100, "forum": 0.9, "posts": 7},
                    {"name": "b2", "all": 0.8, "saved": 0.35, "deals": 50, "forum": 0.8, "posts": 1}]}
LEDGER = [{"seq": 1, "ts": NOW - 30, "kind": "settle", "data": {"item": "G1", "amount": 900, "grade": "A", "shop": "https://other.trycloudflare.com", "buyer": "b2"}},
          {"seq": 2, "ts": NOW - 20, "kind": "broken", "data": {"item": "G1", "unit": "u1", "shop": "https://other.trycloudflare.com", "hours_after_sale": 2.5, "refunded": 0}},
          {"seq": 3, "ts": NOW - 10, "kind": "settle", "data": {"item": "G2", "amount": 100, "grade": "C", "shop": "https://me.trycloudflare.com", "buyer": "x"}}]
SUMMARY = {"seller": {"skus": {"G1": {"settled": 3, "sales_per_h": 0.5, "sold_6h": 2, "last_sale_ts": NOW}}},
           "buyer": {"shops": {"https://other.trycloudflare.com": {"attempts": 4, "paid": 3, "refused": {}, "bind_mismatch": 0,
                                                                   "broken_confirmed": 1, "broken_inferred": 0}}},
           "market": {"sku_rate": {"G1": 2.5}, "sku_settles_48h": {"G1": 40}, "supply_1h": {"G1": 7},
                      "p_clear": {"goods": 0.71}, "sku_seqs": {"G1": [1]},
                      "price_book": {"https://other.trycloudflare.com": {"sku": {"G1": {"n": 5, "med": 0.68}}, "cat": {}}},
                      "breakage": {"sku": {"G1": {"A": {"n": 20, "k": 1, "q": 0.05, "upper": 0.18}}}, "cat": {}, "grade": {}}}}
CATALOG = {"G1": ["소파", "가구>소파", 2000000, "goods", None], "G2": ["김치", "식품>김치", 10000, "food", 240]}
INVENTORY = {"balance": 1, "units": [{"unit": "u5", "sku": "G1", "grade": "A", "cost": 1300000, "supplied_at": NOW - 7200},
                                     {"unit": "u6", "sku": "G1", "grade": "A", "cost": 1100000, "supplied_at": NOW - 3600},
                                     {"unit": "u7", "sku": "G1", "grade": "expired", "cost": 1, "supplied_at": NOW}]}


def get(path, params=None):
    if path == "/api/board":
        return BOARD
    if path == "/api/ledger":
        return LEDGER
    raise RuntimeError(f"no fixture for {path}")


def market():
    m = mcptools.Market(get, summary=SUMMARY, catalog=CATALOG, inventory=INVENTORY, names={"https://other.trycloudflare.com": "Other Shop"})
    m.now = lambda: NOW
    m.get_abs = lambda url: {"name": "Other Shop", "description": "x" * 500, "skills": [{"name": "Buy one unit"}]}
    return m


def rpc(m, method, params=None, rid=1):
    return mcptools.handle({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}, m)


def text(resp):
    return resp["result"]["content"][0]["text"]


class ProtocolTest(unittest.TestCase):
    def test_initialize_list_ping_unknown_and_notifications(self):
        m = market()
        with mock.patch.dict(os.environ, {"STRATEGIST_ME": "psyoung82"}):
            init = rpc(m, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "market")
        self.assertEqual(init["result"]["capabilities"], {"tools": {}})
        self.assertIsNone(mcptools.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, m))
        self.assertEqual(rpc(m, "ping")["result"], {})
        names = [t["name"] for t in rpc(m, "tools/list")["result"]["tools"]]
        self.assertEqual(names, list(decide.TOOL_NAMES))
        self.assertEqual(rpc(m, "nope")["error"]["code"], -32601)
        self.assertEqual(rpc(m, "tools/call", {"name": "nope"})["result"]["isError"], True)
        self.assertEqual(mcptools.handle("junk", m)["error"]["code"], -32600)

    def test_serve_answers_each_line_and_skips_bad_json(self):
        m = market()
        out = io.StringIO()
        mcptools.serve(io.StringIO('{"jsonrpc":"2.0","id":7,"method":"ping"}\nnot json\n\n'), out, m)
        lines = [json.loads(x) for x in out.getvalue().splitlines()]
        self.assertEqual([(x.get("id"), "result" in x, (x.get("error") or {}).get("code")) for x in lines],
                         [(7, True, None), (None, False, -32700)])


class ToolsTest(unittest.TestCase):
    def setUp(self):
        self.m = market()
        self.env = mock.patch.dict(os.environ, {"STRATEGIST_ME": "psyoung82"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_board_ranks_students_shops_and_buyers_and_finds_us(self):
        p = self.m.board("players", 1)
        self.assertEqual((p["ranked"], p["top"][0]["player"], p["me"]["rank"]), (2, "a", 2))
        s = self.m.board("shops", 5)
        self.assertEqual(([r["name"] for r in s["top"]], s["me"]["rank"]), (["other", "psyoung82"], 2))   # idle and house out
        b = self.m.board("buyers")
        self.assertEqual(b["me"]["saved"], 0.24)
        self.assertEqual(json.loads(text(rpc(self.m, "tools/call", {"name": "board", "arguments": {"side": "buyers"}}))[len(mcptools.NOTE) + 1:])["ranked"], 2)

    def test_ledger_recent_filters_and_labels(self):
        r = self.m.ledger_recent(n=10)
        self.assertEqual([x["seq"] for x in r["rows"]], [3, 2, 1])
        self.assertEqual(r["rows"][2]["shop"], "Other Shop")
        self.assertEqual(r["rows"][0]["name"], "김치")
        self.assertEqual([x["seq"] for x in self.m.ledger_recent(kind="broken")["rows"]], [2])
        self.assertEqual([x["seq"] for x in self.m.ledger_recent(sku="G1")["rows"]], [2, 1])
        self.assertEqual([x["seq"] for x in self.m.ledger_recent(shop="Other")["rows"]], [2, 1])
        self.assertEqual([x["seq"] for x in self.m.ledger_recent(shop="me.try")["rows"]], [3])
        self.assertEqual(self.m.ledger_recent(n=1)["n"], 1)

    def test_price_book_and_sku_info(self):
        p = self.m.price_book("G1")
        self.assertEqual((p["name"], p["market_rate_6h"], p["market_settles_48h"], p["p_clear_kind"]), ("소파", 2.5, 40, 0.71))
        self.assertEqual(p["shops"], [{"shop": "Other Shop", "url": "https://other.trycloudflare.com", "med_paid_list": 0.68, "n": 5}])
        self.assertEqual(p["ours"]["sold_6h"], 2)
        i = self.m.sku_info("G1")
        self.assertEqual(i["catalog"]["list_price"], 2000000)
        self.assertEqual(i["held"], {"A": {"units": 2, "cost_mean": 1200000, "oldest_h": 2.0}})
        self.assertEqual(i["breakage"]["A"]["upper"], 0.18)
        self.assertEqual(self.m.sku_info("G9")["catalog"], None)

    def test_shop_card_only_for_listed_shops(self):
        c = self.m.shop_card("https://other.trycloudflare.com/")
        self.assertEqual((c["name"], len(c["description"]), c["skills"]), ("Other Shop", 300, ["Buy one unit"]))
        self.assertEqual(c["our_record"]["paid"], 3)
        self.assertEqual(c["price_book"], [{"sku": "G1", "name": "소파", "med_paid_list": 0.68, "n": 5}])
        self.assertIn("error", self.m.shop_card("https://evil.example"))

    def test_results_start_with_the_note_and_are_cut(self):
        t = text(rpc(self.m, "tools/call", {"name": "sku_info", "arguments": {"sku": "G1"}}))
        self.assertTrue(t.startswith(mcptools.NOTE + "\n"))
        with mock.patch.object(mcptools, "MAX_TEXT", 50):
            t = text(rpc(self.m, "tools/call", {"name": "board", "arguments": {"side": "shops"}}))
        self.assertTrue(t.endswith("(cut at 50 characters)"))
        self.assertIn("bad arguments", text(rpc(self.m, "tools/call", {"name": "board", "arguments": {"nope": 1}})))


class DecideToolsTest(unittest.TestCase):
    def test_setup_writes_the_config_and_the_flags_allow_only_market_tools(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "home"
            p = decide.tools_setup(home, Path(d) / "shop", "https://m.example", "psyoung82")
            cfg = json.loads(p.read_text(encoding="utf-8"))["mcpServers"]["market"]
            self.assertEqual(cfg["args"], ["-m", "strategist.mcptools"])
            self.assertEqual(cfg["env"]["SHOP_HOME"], str(Path(d) / "shop"))
            self.assertTrue(Path(cfg["env"]["PYTHONPATH"], "strategist", "mcptools.py").is_file())
            flags = decide.tool_flags(p)
            self.assertIn("--strict-mcp-config", flags)
            self.assertIn("--allowedTools mcp__market__board,mcp__market__ledger_recent,", flags)
            with mock.patch.dict(os.environ, {"STRATEGIST_TOOLS": "0"}):
                self.assertIsNone(decide.tools_setup(home, Path(d) / "shop", "https://m.example", "psyoung82"))

    def test_ask_appends_the_flags_to_the_harness_command(self):
        seen = {}

        def fake(prompt, timeout, cmd):
            seen["cmd"] = cmd
            return {"assessment": "x", "changes": [], "confidence": 0.5, "tool_calls": ["board(side=shops)"]}, "{}"
        with mock.patch.object(decide, "_harness", lambda: mock.Mock(ask_json=fake, CMD="claude -p --tools \"\"",
                                                                      first_json_object=lambda raw: {"assessment": "x", "changes": [], "confidence": 0.5, "tool_calls": ["board(side=shops)"]})):
            with mock.patch.dict(os.environ, {"STRATEGIST_HARNESS_CMD": "claude -p --model m --tools \"\""}):
                obj, _, err = decide.ask("p", 5, tools="/tmp/mcp.json")
        self.assertIsNone(err)
        self.assertEqual(seen["cmd"], "claude -p --model m --tools \"\"" + decide.tool_flags("/tmp/mcp.json"))
        self.assertEqual(obj["tool_calls"], ["board(side=shops)"])

    def test_prompt_has_the_tools_section_only_with_tools(self):
        p = decide.build_prompt("body", {}, {}, {}, [], tools=True)
        self.assertIn("## Tools (read-only market data", p)
        self.assertLess(p.index("## Tools"), p.index("## Rules"))
        self.assertNotIn("## Tools", decide.build_prompt("body", {}, {}, {}, []))


if __name__ == "__main__":
    unittest.main()
