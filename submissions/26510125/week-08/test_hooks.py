"""Local self-check of the Round 3 hooks on synthetic .market files (no market, no shop needed):
    python test_hooks.py
"""
import collections, json, subprocess, sys, tempfile, time
from pathlib import Path

import restock, market_buyer as mb

md = Path(tempfile.mkdtemp()) / ".market"; md.mkdir()
cat = {"F1": {"name": "milk", "category": "food>dairy", "list_price": 100, "kind": "food", "shelf_hours": 24},
       "G1": {"name": "lamp", "category": "goods>home", "list_price": 300, "kind": "goods"},
       "G2": {"name": "desk", "category": "goods>home", "list_price": 900, "kind": "goods"}}
(md / "catalog.json").write_text(json.dumps(cat))
(md / "inventory.json").write_text(json.dumps({"balance": 20000, "bankrupt_line": 500000, "bankruptcies_left": 2, "units": [
    {"unit": "u1", "sku": "G1", "name": "lamp", "category": "goods>home", "list_price": 300, "kind": "goods", "cost": 150, "grade": "A", "supplied_at": 0, "expires_at": None, "cert": True}]}))

# restock: demand-driven, affordable, food with short shelf discounted
restock.settled_counts = lambda: collections.Counter({"F1": 10, "G1": 4, "G2": 1, "ZZ": 9})
sys.argv = ["restock", "--state", str(md)]
import io, contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    restock.main()
out = json.loads(buf.getvalue().strip().splitlines()[-1])
buy = {b["sku"]: b["qty"] for b in out["buy"]}
assert set(buy) <= set(cat) and "ZZ" not in buy, buy
assert buy.get("G1", 0) == 4 - 1, buy          # int(20000*0.05/225)=4 affordable, minus the one in stock
assert buy.get("F1", 0) == 5 and buy.get("G2") == 1, buy   # desk: int(1000/675) = 1 unit
assert "bankrupt" not in out
assert (md / "outbox.jsonl").exists() and json.loads((md / "outbox.jsonl").read_text())["as"] == "shop"

# market_buyer Round 3 helpers
order = {"order": "o1", "item": "lamp", "category": "goods>home", "budget": 400, "acceptable": ["G1", "G2"], "kind": "goods"}
shop = {"name": "s", "url": "http://x", "offers": [{"sku": "G1", "name": "lamp", "qty": 2}, {"sku": "G2", "name": "desk", "qty": 1}]}
assert mb.pick_sku(shop, order, cat, set())["sku"] == "G1"            # desk is above budget
assert mb.pick_sku(shop, dict(order, budget=1000), cat, set())["sku"] == "G2"
assert mb.pick_sku(shop, dict(order, budget=1000), cat, {"desk"})["sku"] == "G1"   # broke on me before
assert mb.accept_policy(order) == {"cert": True, "grades": ["A", "B"]}
assert mb.accept_policy({"kind": "food", "use_within_hours": 5})["min_hours_left"] == 5
(md / "payments.jsonl").write_text("\n".join(json.dumps({"shop": "http://bad", "paid": False}) for _ in range(2)) + "\n"
                                   + json.dumps({"shop": "http://ok", "paid": False}) + "\n")
assert mb.refused_shops(md) == {"http://bad"}
mb.forum_post(md, "hello"); mb.forum_post(md, "again")
assert sum(1 for l in (md / "outbox.jsonl").read_text().splitlines() if '"buyer"' in l) == 1   # 5-min limit

# review hook end to end (harness or its fallback), dishonest claim capped at 2 stars
deal = {"order": order, "receipt": {"item": "G1", "price": 250}, "settled": {"name": "lamp", "grade": "C", "claim": "A",
        "claim_honest": False, "value": 300, "warranty_hours": 0}}
(md / "deal.json").write_text(json.dumps(deal))
r = subprocess.run([sys.executable, "review.py", "--deal", str(md / "deal.json")], capture_output=True, text=True, timeout=120)
rv = json.loads(r.stdout.strip().splitlines()[-1])
assert 1 <= rv["stars"] <= 2 and rv["review"], (rv, r.stderr[-300:])
print("ok: restock", buy, "| review", rv)
