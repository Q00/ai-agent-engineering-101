"""A day of the whole system in one folder, in order, as on the server: do the parts hand over?

    python test_system.py

One MARKET_DIR, as the kit and the analysis service share it. In order:
  1 trading     three buyers haggle with our seller over A2A (two deals, one walks away); the kit's
                settlement is played by taking the sold units out of inventory.json; restock.py
                turns the deals into sales; our buyer finds no shop for apples (an unmet order)
  2 analysis    one pass of the runner in the focus phase, with a scripted model: the seller
                analysis replays a price change and leaves a memo through the data room (MCP),
                then proposes it; the buyer analysis proposes a lower first offer; the integrated
                analysis adopts both and keeps an insight
  3 trading     the parts read what the analysis wrote: the seller asks more, our buyer opens
                lower, restock buys apples for the unmet order
  4 memory      the next briefings carry the memo, the insight and the fate of each proposal; the
                file check finds every file in the shape its readers expect
"""
import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(tempfile.mkdtemp(prefix="round3-system-test-"))
NOW = time.time()
os.environ.update(MARKET_DIR=str(ROOT / ".market"), ROUND_END_TS=str(NOW + 10 * 86400),
                  STRATEGY_FILE=str(ROOT / "strategy.json"), PYTHONUTF8="1")
os.environ.pop("OPENAI_API_KEY", None)                    # no model calls: the analysis model is scripted
os.environ.pop("HARNESS_FAKE_REPLY", None)

import httpx                                                                     # noqa: E402
from cryptography.hazmat.primitives import serialization                         # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

import analysis          # noqa: E402
import common            # noqa: E402
import context           # noqa: E402
import contract          # noqa: E402
import market_buyer as mb  # noqa: E402

HERE = Path(__file__).resolve().parent
MD = common.MARKET_DIR
H = 3600
KIMCHI, GEOT, APPLE = "Gaaaa0001", "Gaaaa0002", "Gcccc0001"
KCAT, ACAT = "식품>김치/반찬/즉석요리>김치", "식품>과일>사과"
CATALOG = {s["sku"]: s for s in [
    {"sku": KIMCHI, "name": "테스트 포기김치 2kg", "category": KCAT, "list_price": 20000, "kind": "food", "shelf_hours": 168},
    {"sku": GEOT, "name": "테스트 겉절이 2kg", "category": KCAT, "list_price": 13400, "kind": "food", "shelf_hours": 168},
    {"sku": APPLE, "name": "테스트 사과 3kg", "category": ACAT, "list_price": 15000, "kind": "food", "shelf_hours": 48},
]}
DEMAND = [{"category": KCAT, "item": "김치", "kind": "food", "shelf_hours": 168, "median_price": 20000, "skus": 2},
          {"category": ACAT, "item": "사과", "kind": "food", "shelf_hours": 48, "median_price": 15000, "skus": 1}]
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail != "" else ""), flush=True)


def unit(u, sku, cost):
    c = CATALOG[sku]
    return {"unit": u, "sku": sku, "name": c["name"], "category": c["category"], "list_price": c["list_price"],
            "kind": "food", "cost": cost, "grade": "A", "supplied_at": NOW - H, "expires_at": NOW + 150 * H}


def setup():
    MD.mkdir(parents=True)
    (ROOT / "strategy.json").write_text('{"phase": "focus"}', encoding="utf-8")
    key = Ed25519PrivateKey.generate()
    (ROOT / "shop_key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()))
    common.write_json(MD / "catalog.json", CATALOG)
    common.write_json(MD / "demand.json", DEMAND)
    common.write_json(MD / "forum.json", [])
    common.write_json(MD / "inventory.json", {"round": 3, "balance": 5_000_000, "units": [
        unit("u-k1", KIMCHI, 12000), unit("u-k2", KIMCHI, 12000), unit("u-g1", GEOT, 8000)]})


def start_seller():
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        port = so.getsockname()[1]
    p = subprocess.Popen([sys.executable, "seller.py", "--port", str(port)], cwd=HERE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.STDOUT, env=dict(os.environ, SHOP_KEY_FILE=str(ROOT / "shop_key.pem")))
    url = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            if httpx.get(url + "/.well-known/agent-card.json", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    return p, url


async def haggle(url, sku, offers, leave=False):
    """One outside buyer: ask for the product, make the offers; the shop's last words."""
    c = mb.A2A()
    await c.open(url)
    t = await c.send(f"I want to buy {CATALOG[sku]['name']} ({sku})")
    first = mb.meta(t).get("ask")
    for o in offers:
        t = await c.send(str(o), t["id"])
    if leave and mb.state(t) == "TASK_STATE_INPUT_REQUIRED":
        await c.cancel(t["id"])
    await c.close()
    return first, mb.state(t), mb.said(t)


def run(*args):
    p = subprocess.run([sys.executable, *args], cwd=HERE, capture_output=True, text=True, encoding="utf-8", timeout=200)
    lines = (p.stdout + p.stderr).splitlines()
    return lines, next((json.loads(x) for x in reversed(lines) if x.startswith("{")), None)


def our_order(oid, sku, url):
    """One order for our buyer, as the kit hands it over: order.json and the shops that have it."""
    c = CATALOG[sku]
    common.write_json(MD / "order.json", {"order": oid, "item": c["category"].split(">")[-1], "budget": 20000,
                                          "acceptable": [sku], "kind": "food", "use_within_hours": 24})
    offers = [{"sku": sku, "name": c["name"], "qty": 1}] if url else []
    common.write_json(MD / "shops.json", [{"name": "Other Shop", "url": url or "http://127.0.0.1:9", "reviews": [], "offers": offers}])
    return run("market_buyer.py", "--shops", str(MD / "shops.json"), "--item", c["name"], "--budget", "20000",
               "--order", str(MD / "order.json"))


def fake_ledger(after):
    rows = [{"seq": i, "ts": NOW - i * 300, "kind": "settle",
             "data": {"order": f"x{i}", "buyer": f"b{i}", "shop": "Alpha" if i % 2 else "Beta", "item": KIMCHI,
                      "amount": 14000, "grade": "A", "claim": "A", "claim_honest": True}} for i in range(1, 26)]
    return [r for r in rows if r["seq"] > after]


def fake_shops(path):
    return {"shops": [{"name": "Alpha", "stock": {KIMCHI: 3}}]}


def tool_call(name, **args):
    return {"content": None, "tool_calls": [{"id": f"c-{name}", "type": "function",
                                             "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}


def answer(obj):
    return {"content": json.dumps(obj, ensure_ascii=False)}


SEEN = {}


def model(messages, name, tools=None):
    """The scripted analysis model: what a good answer looks like, one step at a time."""
    first = messages[0]["content"]
    said = [m["content"] for m in messages if m["role"] == "tool"]
    if first.startswith("You analyse the seller"):
        SEEN["seller_tools"] = [t["function"]["name"] for t in tools or []] or SEEN.get("seller_tools")
        if not said:
            return tool_call("replay", field="seller.price_adjust", value=0.05)
        if len(said) == 1:
            SEEN["replay"] = json.loads(said[0])
            return tool_call("leave_memo", to="buyer", act="inform", text="our kimchi buyers paid at or near our first ask",
                             evidence="2 deals at 13,800 and 15,000 on asks of 15,000")
        SEEN["memo"] = json.loads(said[1])
        return answer({"report": "Both kimchi deals closed near the first ask.",
                       "proposals": [{"field": "price_adjust", "value": 0.05, "reason": "replay: see the tool"}]})
    if first.startswith("You analyse the buyer"):
        SEEN["buyer_briefing"] = first
        return answer({"report": "No shop had apples.", "proposals": [{"field": "open_ratio", "value": 0.55, "reason": "1 order"}]})
    if first.startswith("You decide which rule changes"):
        SEEN["integrated"] = first
        ids = re.findall(r'"id": "((?:seller|buyer)-\d+-0)"', first)
        return answer({"adopt": ids, "revert": [], "note": "both supported",
                       "insights": {"new": [{"role": "seller", "topic": "price", "text": "Kimchi buyers pay near our first ask",
                                             "evidence": "2 of 2 kimchi deals at 92-100% of the ask",
                                             "use": "try first asks 5% of list higher on kimchi"}]}})
    return None


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    seller = None
    try:
        setup()
        seller, url = start_seller()
        print("\n[1 trading] outside buyers, the kit's settlement, restock, our buyer")
        first, state, said = asyncio.run(haggle(url, KIMCHI, [12000, 14000]))
        check("buyer 1: asked 15,000 (75% of list for grade A), 14,000 closes on the counter of 13,800",
              first == 15000 and state == "TASK_STATE_COMPLETED" and "13800" in said, (first, said[:60]))
        _, state, _ = asyncio.run(haggle(url, KIMCHI, [15000]))
        check("buyer 2: pays the ask", state == "TASK_STATE_COMPLETED")
        _, state, _ = asyncio.run(haggle(url, GEOT, [5000, 5000], leave=True))
        check("buyer 3: offers under the floor and leaves", state == "TASK_STATE_INPUT_REQUIRED")
        inv = common.market_file("inventory.json")
        inv["units"] = [u for u in inv["units"] if u["sku"] != KIMCHI]       # the kit settled both kimchi units
        common.write_json(MD / "inventory.json", inv)
        lines, out = run("restock.py", "--state", str(MD))
        sales = common.records("sales")
        check("restock: the two settled deals become sales", sorted(s["price"] for s in sales) == [13800, 15000], [s["price"] for s in sales])
        check("restock: before any analysis, no apples (48 h shelf life is under the shop's week)", APPLE not in {b["sku"] for b in out["buy"]})
        lines, out = our_order("o-apple", APPLE, None)
        unmet = common.records("unmet")
        check("our buyer: no shop has apples; the unmet order is kept under the catalog's category",
              out["receipt"] is None and unmet and unmet[-1]["category"] == ACAT, unmet[-1:] and unmet[-1])

        print("\n[2 analysis] one pass of the runner, focus phase, scripted model, data room over MCP")
        done = analysis.run_once(NOW + 60, model, fake_ledger, fake_shops)
        check("every step ran", done == ["collect", "index", "replies", "seller", "buyer", "integrated"], done)
        check("the seller analysis was offered the data room's four tools", SEEN.get("seller_tools") == ["ledger_query", "replay", "shop_credit", "leave_memo"],
              SEEN.get("seller_tools"))
        r = SEEN.get("replay") or {}
        print("    replay: " + json.dumps({k: r.get(k) for k in ("check", "recorded", "replayed")}, ensure_ascii=False))
        check("its replay ran on today's negotiations: both deals and the walk-away, given back 3 of 3",
              r.get("negotiations") == 3 and r.get("check", "").endswith("3 of 3 recorded outcomes"), r.get("check"))
        check("its memo was saved, signed seller", (SEEN.get("memo") or {}).get("saved", {}).get("from") == "seller")
        check("the integrated analysis saw both proposals", all(f'"{x}-' in SEEN.get("integrated", "") for x in ("seller", "buyer")))
        rules = common.rules()
        check("focus phase: both changes in force (price +0.05, first offer 55%)",
              rules["seller"]["price_adjust"] == 0.05 and rules["buyer"]["open_ratio"] == 0.55, rules)
        wanted = common.read_json(MD / "analysis" / "wanted.json")["categories"]
        check("wanted categories: apples, from the unmet order", wanted.get(ACAT) == 1, wanted)
        post = next((x for x in common.read_jsonl(MD / "outbox.jsonl") if "Market Index" in x.get("post", "")), None)
        check("the market index went to the outbox as our buyer", post and post["as"] == "buyer")

        print("\n[3 trading] the parts read what the analysis wrote")
        lines, out = our_order("o-geot", GEOT, url)
        print("\n".join("    " + x for x in lines if x.startswith("[buyer]")))
        asked = [x for x in lines if "<-" in x and "Asking" in x]
        check("the seller asks 80% of list now: 10,720 for 겉절이 (13,400 x (0.75 + 0.05))", asked and "Asking 10720" in asked[0], asked[:1])
        offered = [x for x in lines if "-> '" in x and re.search(r"-> '\d+'", x)]
        check("our buyer opens at 55% of list now: 7,370", offered and "-> '7370'" in offered[0], offered[:1])
        lines, out = run("restock.py", "--state", str(MD))
        check("restock buys apples now that they are wanted", APPLE in {b["sku"] for b in out["buy"]}, out["buy"])

        print("\n[4 memory] the next briefings and the file check")
        later = NOW + 3 * H
        text =context.form("seller", later, {"our_records_24h": {}})
        check("the seller's next briefing: its proposal adopted and applied, the new insight",
              "price_adjust = 0.05: adopted and applied as 0.05" in text and "Kimchi buyers pay near our first ask" in text)
        text = context.form("buyer", later, {"our_records_24h": {}})
        check("the buyer's next briefing carries the seller's memo", "from seller (inform): our kimchi buyers paid" in text)
        found = contract.check()
        print("    present: " + ", ".join(found["present"]))
        check("the file check: every file there is in the shape its readers expect", found["problems"] == [], found["problems"])
        check("the file check catches a field the market might name differently",
              any("'amount' missing" in p for p in _broken_ledger_check()), "")
    finally:
        if seller:
            seller.kill()
        shutil.rmtree(ROOT, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


def _broken_ledger_check():
    """A settle entry with the price under another name: the check must say so."""
    path = MD / "ledger" / "settles.jsonl"
    keep = path.read_text(encoding="utf-8")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"seq": 99, "ts": NOW, "shop": "Alpha", "item": KIMCHI, "price": 14000}) + "\n")
    try:
        return contract.check()["problems"]
    finally:
        path.write_text(keep, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
