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

# ---- acp_buyer: tools built from the kit's acp.json shape, against a mock shop (no market, no LLM)
import base64, hashlib, httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import acp_buyer, harness

shop_key, buyer_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
shop_pub = base64.b64encode(shop_key.public_key().public_bytes_raw()).decode()
card = {"protocol": "acp", "endpoint": "https://shop.test/acp", "tools": [
    {"name": "create_checkout_session", "method": "POST", "path": "/checkout_sessions", "input": {"line_items": "...", "receipt": "..."}},
    {"name": "get_checkout_session", "method": "GET", "path": "/checkout_sessions/{id}", "input": {}},
    {"name": "complete_checkout_session", "method": "POST", "path": "/checkout_sessions/{id}/complete", "input": {"payment_data": "..."}},
    {"name": "cancel_checkout_session", "method": "POST", "path": "/checkout_sessions/{id}/cancel", "input": {}}]}
receipt = {"shop": "s", "item": "G1", "price": 250, "task": "t1", "sig_seller": "x"}
seen = []
def jwt(payload):
    h = acp_buyer.b64url(b'{"alg":"EdDSA"}'); b = acp_buyer.b64url(json.dumps(payload).encode())
    return f"{h}.{b}." + acp_buyer.b64url(shop_key.sign(f"{h}.{b}".encode()))
def mock(req: httpx.Request):
    seen.append((req.method, req.url.path))
    if req.url.path == "/.well-known/acp.json":
        return httpx.Response(200, json=card)
    if req.url.path == "/acp/checkout_sessions":
        return httpx.Response(201, json={"id": "cs1", "status": "ready_for_payment",
                                         "checkout_jwt": jwt({"item": "G1", "price": 250, "task": "t1", "unit": "u9", "claim": mock.grade, "cert": True})})
    if req.url.path.endswith("/complete"):
        pd = json.loads(req.content)["payment_data"]
        assert pd["checkout_mandate"]["mandate"]["order"] == "o1" and pd["payment_mandate"]["mandate"]["amount"] == 250
        return httpx.Response(200, json={"order": {"mandate_receipt": {"ledger": 7, "name": "lamp", "grade": "A"}}})
    if req.url.path.endswith("/cancel"):
        return httpx.Response(200, json={"status": "canceled"})
    return httpx.Response(404, json={})
mock.grade = "A"
_Client = httpx.Client
acp_buyer.httpx.Client = lambda **kw: _Client(transport=httpx.MockTransport(mock))
harness.ask_json = lambda *a, **k: {"plan": ["create_checkout_session", "get_checkout_session", "complete_checkout_session"]}
payload = {"order": {"order": "o1", "acceptable": ["G1"], "kind": "goods", "mandates": {
               "checkout": {"mandate": {"a": 1}}, "payment": {"mandate": {"constraints": [{"type": "payment.amount_range", "max": 300}]}}}},
           "shop": "https://shop.test", "shop_pub": shop_pub, "receipt": receipt, "accept": {"cert": True, "grades": ["A", "B"]}}
mr, why = acp_buyer.pay(payload, buyer_key)
assert mr and mr["ledger"] == 7 and why is None, (mr, why)
assert [p for _, p in seen][1:] == ["/acp/checkout_sessions", "/acp/checkout_sessions/cs1", "/acp/checkout_sessions/cs1/complete"], seen
mock.grade = "C"; seen.clear()                      # accept policy refuses -> cancel, no complete
mr, why = acp_buyer.pay(payload, buyer_key)
assert mr is None and "grade C" in why and seen[-1][1].endswith("/cancel"), (why, seen)
mock.grade = "A"
mr, why = acp_buyer.pay(dict(payload, order=dict(payload["order"], mandates={"checkout": {"mandate": {}}, "payment": {"mandate": {"constraints": [{"type": "payment.amount_range", "max": 200}]}}})), buyer_key)
assert mr is None and "above the mandate" in why, why
harness.ask_json = lambda *a, **k: {"plan": ["cancel_checkout_session"]}      # bad plan -> code fallback
mr, why = acp_buyer.pay(payload, buyer_key)
assert mr and harness.CALLS["fallbacks"] >= 1
# market_buyer additions
import market_buyer as mb2
mem = {"shops": {"liar": {"trades": [{"dishonest": True}]}}}
mb2.median_price = lambda sku: (240, 12)
txt = mb2.price_report("G1", "lamp", 250, "s", mem)
assert "median 240" in txt and "liar" in txt, txt
print("ok: acp_buyer mock checkout + refusal + mandate cap + plan fallback; price_report")
