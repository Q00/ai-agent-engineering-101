"""PC test of the data room: the four tools, and the MCP server that serves them.

    python test_data_room.py

replay runs on hand-made negotiations whose outcomes are worked out below by the shop's rule;
shop_credit on a hand-made ledger, notebook and shop list; leave_memo on its limits. Then the
server itself, over stdio: through our host (mcp_host.py), through the official MCP client (so any
MCP host can use it), and an analysis whose data room does not start (it goes on without tools).
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(tempfile.mkdtemp(prefix="round3-room-test-"))
NOW = time.time()
os.environ.update(MARKET_DIR=str(ROOT / ".market"), ROUND_END_TS=str(NOW + 10 * 86400),
                  STRATEGY_FILE=str(ROOT / "strategy.json"))

import analysts       # noqa: E402
import buyer_memory   # noqa: E402
import common         # noqa: E402
import ledger         # noqa: E402
import mcp_host       # noqa: E402
import tools          # noqa: E402

HERE = Path(__file__).resolve().parent
MD = common.MARKET_DIR
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail != "" else ""), flush=True)


def neg(offers, outcome, price=None, lp=20000, cost=12000, floor=12600, ask=15000, warranty=False, grade="A"):
    return dict(sku="Gaaaa0001", unit="u", grade=grade, cost=cost, list_price=lp, floor=floor, at_cost=True,
                warranty=warranty, how="sku", asks=[ask], offers=offers, outcome=outcome, price=price)


def setup():
    (MD / "analysis").mkdir(parents=True)
    (ROOT / "strategy.json").write_text('{"phase": "explore"}', encoding="utf-8")
    common.write_json(MD / "catalog.json", {"Gaaaa0001": {"sku": "Gaaaa0001", "name": "김치", "category": "식품>김치",
                                                          "list_price": 20000, "kind": "food", "shelf_hours": 168}})
    # the shop's rule (pricing.respond) on each: ask 15000, floor 12600, list 20000, cost 12000
    for n in (neg([12000, 14000], "deal", 13800),      # counter 13800, then 14000 >= 13800: sold at 13800
              neg([15000], "deal", 15000),             # at the ask
              neg([10000, 11000], "walked"),           # counters 13800, 13200; the buyer left
              neg([], "deal", 15000),                  # a plain yes to the ask
              neg([10000] * 4, "rejected"),            # under the floor four times
              neg([27930, 29000], "deal", 29000, lp=39900, floor=20580, ask=31920, warranty=True, grade="C")):
        common.record("negotiations", **n)
    for o in (dict(deal=True, price=17500), dict(deal=True, price=14000), dict(deal=False, price=None, asks=[19000, 18800, 18800])):
        common.record("orders", order="o", list_price=20000, budget=22000, candidates=1, **o)


def test_replay():
    print("\n[replay] our own negotiations under another rule")
    r = tools.replay("seller.price_adjust", 0.05)
    print("    " + json.dumps(r, ensure_ascii=False)[:400])
    check("the replay with the rules in force gives back every recorded outcome", r["check"].endswith("gives back 6 of 6 recorded outcomes"))
    check("recorded: 4 deals, 72,800 taken, 24,800 over cost", r["recorded"] == {"deals": 4, "revenue": 72800, "margin": 24800}, r["recorded"])
    check("asks 5% of list higher: the 12,000-then-14,000 buyer pays 14,000; the three who took the ask leave",
          r["replayed"] == {"deals": 1, "revenue": 14000, "margin": 2000}, r["replayed"])
    check("4 deals is weak evidence, and the answer says so", "weak evidence" in r.get("weak", ""))
    r = tools.replay("seller.price_adjust", -0.05)
    check("asks 5% lower: four deals still, each closing lower", r["replayed"]["deals"] == 4 and r["replayed"]["revenue"] < 72800,
          r["replayed"])
    r = tools.replay("seller.warranty_extra", {"C": 0.25})
    check("the warranty for grade C at 25%: the refurbished deal is lost", r["replayed"]["deals"] == 3, r["replayed"])
    r = tools.replay("buyer.max_ratio", 0.85)
    check("our buyer's ceiling at 85%: the 17,500 deal is lost (surplus 8,500 -> 6,000)",
          (r["recorded"]["surplus"], r["replayed"]["deals"], r["replayed"]["surplus"]) == (8500, 1, 6000), r)
    r = tools.replay("buyer.max_ratio", 0.95)
    check("at 95%: the order we left at an ask of 18,800 closes too (surplus 9,700)",
          (r["replayed"]["deals"], r["replayed"]["surplus"]) == (3, 9700), r["replayed"])
    check("open_ratio cannot be replayed, and the answer says why", "how shops answer a lower first offer" in tools.replay("buyer.open_ratio", 0.5)["error"])
    check("out of range or not a field: refused", "outside" in tools.replay("seller.price_adjust", 0.3)["error"]
          and "replay covers" in tools.replay("seller.floor", 0)["error"])


def test_credit_and_memos():
    print("\n[shop_credit, leave_memo]")
    ledger.folder().mkdir(parents=True, exist_ok=True)
    with open(ledger.folder() / "settles.jsonl", "w", encoding="utf-8") as f:
        for i in range(3):
            f.write(json.dumps({"ts": NOW, "shop": "Alpha", "item": "Gaaaa0001", "amount": 14000, "claim": "A" if i < 2 else None,
                                "claim_honest": i == 0}) + "\n")
    (ledger.folder() / "broken.jsonl").write_text(json.dumps({"ts": NOW, "shop": "Alpha", "item": "Gaaaa0001"}) + "\n", encoding="utf-8")
    common.write_json(MD / "market_summary.json", {"by_shop": {"Alpha": {"settled_24h": 3, "claims": 2, "honest": 1}}})
    buyer_memory.save({"Alpha": {"recent": ["settled", "unpaid"], "settled": 1, "unpaid": 1, "errors": 0, "false_claims": 1,
                                 "broken": 1, "broken_no_warranty": 1, "ratios": [0.7], "url": "http://alpha"}})
    common.write_json(MD / "shops.json", [{"name": "Alpha", "url": "http://alpha", "reviews": [{"stars": 4}, {"stars": 2}]}])
    c = tools.shop_credit("http://alpha")
    print("    " + json.dumps(c, ensure_ascii=False))
    check("by address: the ledger (3 deals, 1 of 2 claims true, 1 breakdown), our notebook and its reviews",
          c["ledger"] == {"settled": 3, "grade_claims": 2, "claims_true": 1, "broke_after_sale": 1}
          and c["our_buyer"]["false_claims"] == 1 and c["reviews"] == {"count": 2, "avg_stars": 3.0} and not c["our_own_shop"])
    check("the trust our buyer gives it", c["trust"] == buyer_memory.trust(buyer_memory.load(), "Alpha", common.market_file("market_summary.json")))
    check("our own shop is marked as ours", tools.shop_credit("Kiro729 Shop")["our_own_shop"] is True)
    state = {}
    run = lambda **a: json.loads(tools.run("leave_memo", a, role="seller", state=state))
    check("a memo to itself, a made-up act, 301 characters: refused",
          all("error" in x for x in (run(to="seller", act="inform", text="x"), run(to="buyer", act="order", text="x"),
                                     run(to="buyer", act="inform", text="y" * 301))))
    first = run(to="buyer", act="inform", text="other shops open at 82% of list", evidence="9 asks", **{"from": "integrated"})
    check("a memo is signed by the caller the host set, not by the model", first["saved"]["from"] == "seller", first)
    run(to="all", act="request", text="look at kimchi demand")
    check("at most 2 memos per analysis", "error" in run(to="buyer", act="inform", text="third"))


def test_server():
    print("\n[data room] the MCP server over stdio")
    with mcp_host.DataRoom("buyer") as room:
        check("the handshake: our server and the protocol version", room.server["serverInfo"]["name"] == "kiro729-data-room"
              and room.server["protocolVersion"] == mcp_host.PROTOCOL, room.server.get("serverInfo"))
        check("tools/list: the four tools, each with a description and an input schema",
              [t["name"] for t in room.tools] == ["ledger_query", "replay", "shop_credit", "leave_memo"]
              and all(t.get("description") and t.get("inputSchema", {}).get("type") == "object" for t in room.tools))
        check("resources/list: business, insights, reports, memos",
              sorted(r["uri"] for r in room.resources()) == ["kiro://business", "kiro://insights", "kiro://memos", "kiro://reports"])
        check("resources/read: our business now", json.loads(room.read("kiro://business"))["phase"] == "explore")
        same = json.loads(room.run("shop_credit", {"shop": "Alpha"}))
        check("tools/call answers what the tool answers in-process", same == tools.shop_credit("Alpha"))
        check("a tool the model made up comes back as an error", "error" in json.loads(room.run("drop_table", {})))
        before = len(common.read_jsonl(MD / "analysis" / "memos.jsonl"))
        room.run("leave_memo", {"to": "seller", "act": "inform", "text": "Beta's claims are mostly false"})
        memos = common.read_jsonl(MD / "analysis" / "memos.jsonl")
        check("a memo through the server is signed by --as (buyer)", len(memos) == before + 1 and memos[-1]["from"] == "buyer")

    async def official():
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        params = StdioServerParameters(command=sys.executable, args=[str(HERE / "data_room.py"), "--as", "integrated"],
                                       env=dict(os.environ, PYTHONUTF8="1"), cwd=str(HERE))
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                names = [t.name for t in (await s.list_tools()).tools]
                got = await s.call_tool("replay", {"field": "buyer.max_ratio", "value": 0.95})
                return names, json.loads(got.content[0].text)
    names, got = asyncio.run(official())
    check("the official MCP client lists the same tools and runs replay (any MCP host can use the room)",
          names == ["ledger_query", "replay", "shop_credit", "leave_memo"] and got["replayed"]["deals"] == 3, names)


def test_no_room():
    print("\n[no data room] an analysis goes on without tools")
    seen = []

    def model(messages, model, tools=None):
        seen.append((messages[0]["content"], tools))
        return {"content": json.dumps({"report": "ok", "proposals": []})}
    real = mcp_host.DataRoom.__enter__

    def broken(self):
        raise RuntimeError("no mcp package")
    mcp_host.DataRoom.__enter__ = broken
    try:
        r = analysts.role_analysis("seller", model, NOW)
    finally:
        mcp_host.DataRoom.__enter__ = real
    check("the analysis still reports, offered no tools, and says so",
          r["report"] == "ok" and r["tools"] == "none" and seen[0][1] is None and "No tools this time" in seen[0][0])
    r = analysts.role_analysis("seller", model, NOW + 10)
    check("with the room: the four tools offered and the replay instruction in the prompt",
          r["tools"] == "data room (MCP)" and len(seen[1][1]) == 4 and "replay it and cite" in seen[1][0])


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        setup()
        test_replay()
        test_credit_and_memos()
        test_server()
        test_no_room()
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
