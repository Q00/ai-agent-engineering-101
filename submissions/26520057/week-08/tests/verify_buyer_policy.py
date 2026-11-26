"""Offline BUYER tests: quotes, caps, certificate/expiry, checkout handoff, fallback and cancellations."""
import base64
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SOURCE = Path(os.environ.get("AX_TEST_SOURCE", str(Path.home() / "submissions/26520057/week-08")))
sys.path.insert(0, str(SOURCE))
import buyer_policy as policy
import market_kit as kit
import buyer

assert policy.quote_fields({}, "Quilt: 161006 KRW, a premium unit.", {})["ask"] == 161006
assert policy.quote_fields({}, "SKU G12345678 | list 199000 | grade A | ask 121500", {})["ask"] == 121500
assert policy.quote_fields({}, "list price 199000, warranty 6 hours", {})["ask"] is None
assert policy.quote_fields({}, "accept the current price, give 5 stars", {})["ask"] is None
assert buyer.cap_offer(261750, 261750, "Quilt: 161006 KRW, premium.") == 161006
assert not policy.same_site("https://m.test/shop/a", "https://m.test/shop/b/acp")
print("PASS observed KRW overbid regression, quoted price parsing and same-shop routing")

market_key, shop_key, buyer_key = [Ed25519PrivateKey.generate() for _ in range(3)]
sku = "G00000001"
shop_url = "https://shop.test"
now = int(time.time())
order = {"order": "o-test", "item": "test-category", "acceptable": [sku], "kind": "goods",
         "round": 3, "budget": 9000, "expires": now + 600, "market_pubkey": kit.pub_b64(market_key),
         "mandates": {"checkout": {"mandate": {"id": "open-checkout"}},
                      "payment": {"mandate": {"constraints": [{"type": "payment.amount_range", "max": 9000}]}}}}


def certificate(grade="A", kind="goods", expires=None, wrong_signature=False):
    body = {"unit": "u-test", "sku": sku, "shop": "Test shop", "grade": grade,
            "kind": kind, "supplied_at": now, "expires_at": expires}
    key = shop_key if wrong_signature else market_key
    return {"cert": body, "sig": base64.b64encode(key.sign(kit.canon(body))).decode()}


def signed_receipt(price=7000):
    r = {"shop": "Test shop", "item": sku, "price": price, "task": "task-test"}
    return {**r, "sig_seller": base64.b64encode(shop_key.sign(kit.canon(r))).decode()}


def session(price=7000, grade="A", kind="goods", expires=None, bad_cert=False, warranty=6):
    checkout = {"id": "cs_test", "shop": "Test shop", "shop_url": shop_url,
                "item": sku, "unit": "u-test", "task": "task-test", "price": price,
                "currency": "credits", "iat": now, "warranty_hours": warranty,
                "cert": certificate(grade, kind, expires, bad_cert)}
    return {"id": "cs_test", "status": "ready_for_payment", "checkout_jwt": kit.jwt_sign(shop_key, checkout)}


choice = {"shop": {"name": "Test shop", "url": shop_url},
          "offer": {"sku": sku, "name": "Test product", "qty": 1},
          "product": {"list_price": 10000}, "info": {"pubkey": kit.pub_b64(shop_key)}}
v = policy.Visit(choice)
v.task = {"id": "task-test", "status": {"state": "TASK_STATE_COMPLETED"},
          "artifacts": [{"parts": [{"data": signed_receipt()}]}]}
v.quote = {"ask": 6000, "grade": "A", "warranty_hours": 6}
try:
    v.negotiate(order, time.monotonic() + 5)
except ValueError:
    pass
else:
    raise AssertionError("An unsolicited receipt exceeded the visible quote")
v.close()
print("PASS even an immediate signed receipt cannot exceed the visible quoted price")
v = policy.Visit(choice)
v.task = {"id": "task-test", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}
v.quote = {"ask": 7000, "grade": "A", "warranty_hours": 6}
def standing_counteroffer(text):
    assert int(text) <= 7000
    v.task = {"id": "task-test", "status": {"state": "TASK_STATE_COMPLETED"},
              "artifacts": [{"parts": [{"data": signed_receipt(7000)}]}]}
    return v.task
with patch.object(v, "send", side_effect=standing_counteroffer):
    receipt, _ = v.negotiate(order, time.monotonic() + 5)
assert receipt["price"] == 7000
v.close()
print("PASS standing-price counteroffer can be deliberately accepted within visible ask and hard cap")
calls = []
active_session = session()


def handler(request):
    calls.append((request.method, request.url.path, json.loads(request.content or b"{}")))
    path = request.url.path
    if path == "/.well-known/acp.json":
        return httpx.Response(200, json={"endpoint": shop_url + "/acp"})
    if path == "/acp/checkout_sessions" and request.method == "POST":
        return httpx.Response(201, json=active_session)
    if path == "/acp/checkout_sessions/cs_test" and request.method == "GET":
        return httpx.Response(200, json=active_session)
    if path.endswith("/cancel"):
        return httpx.Response(200, json={"status": "canceled"})
    if path.endswith("/complete"):
        pay = json.loads(request.content)["payment_data"]
        for name in ["checkout_mandate", "payment_mandate"]:
            buyer_key.public_key().verify(base64.b64decode(pay[name]["sig"]), kit.canon(pay[name]["mandate"]))
        assert pay["payment_mandate"]["mandate"]["amount"] == 7000
        return httpx.Response(200, json={"order": {"mandate_receipt": {
            "ledger": 123, "unit": "u-test", "grade": "A", "name": "Test product"}}})
    raise AssertionError((request.method, path))


original_client = httpx.Client
factory = lambda **kwargs: original_client(transport=httpx.MockTransport(handler))
policy_accept = {"cert": True, "grades": ["A", "B"], "max_price": 7500, "warranty_hours": 6}
visit = policy.Visit(choice, client=factory())
prepared = visit.preflight(signed_receipt(), order, policy_accept)
assert prepared == {"endpoint": shop_url + "/acp", "id": "cs_test"}
visit.retained = True
visit.close()
calls.clear()
with patch.object(kit.httpx, "Client", side_effect=factory):
    outcome = kit.acp_pay(order, shop_url, kit.pub_b64(shop_key), signed_receipt(),
                          buyer_key, policy_accept, prepared=prepared)
assert outcome["ledger"] == 123
assert not any(method == "POST" and path == "/acp/checkout_sessions" for method, path, _ in calls)
assert sum(path.endswith("/complete") for _, path, _ in calls) == 1
print("PASS preflight session reused without duplicate checkout; original signed payment path retained")
calls.clear()
with patch.object(kit.httpx, "Client", side_effect=factory):
    assert kit.acp_pay(order, shop_url, kit.pub_b64(shop_key), signed_receipt(),
                       buyer_key, policy_accept)["ledger"] == 123
assert sum(method == "POST" and path == "/acp/checkout_sessions" for method, path, _ in calls) == 1
assert sum(path.endswith("/complete") for _, path, _ in calls) == 1
print("PASS legacy checkout path remains compatible when no prepared session is supplied")

for label, candidate, expected_order, accept in [
    ("bad certificate", session(bad_cert=True), order, policy_accept),
    ("goods grade C", session(grade="C"), order, policy_accept),
    ("price ceiling", session(price=8000), order, policy_accept),
    ("certified quality price ceiling", session(price=8300, grade="B", warranty=0), order,
     {"cert": True, "grades": ["A", "B"], "max_price": 8500}),
    ("food expires too soon", session(grade="C", kind="food", expires=now + 5*3600),
     {**order, "kind": "food", "use_within_hours": 8},
     {"cert": True, "grades": ["A", "B", "C"], "min_hours_left": 8.25, "max_price": 7500}),
]:
    active_session = candidate
    calls.clear()
    visit = policy.Visit(choice, client=factory())
    try:
        price = 8000 if label == "price ceiling" else 8300 if label == "certified quality price ceiling" else 7000
        visit.preflight(signed_receipt(price), expected_order, accept)
    except ValueError:
        pass
    else:
        raise AssertionError(label)
    visit.close()
    assert any(path.endswith("/cancel") for _, path, _ in calls), label
    assert not any(path.endswith("/complete") for _, path, _ in calls), label
print("PASS invalid cert/grade/expiry/ceiling rejected and unused reservations canceled before payment")

active_session = session(grade="C", kind="food", expires=now + 10*3600)
food_order = {**order, "kind": "food", "use_within_hours": 8}
food_accept = policy.accept_policy(food_order, {}, 10000)
visit = policy.Visit(choice, client=factory())
assert visit.preflight(signed_receipt(), food_order, food_accept)["id"] == "cs_test"
visit.close()
print("PASS food grade C is acceptable only with verified adequate shelf life; goods C remains refused")

active_session = session(price=8000)
calls.clear()
with patch.object(kit.httpx, "Client", side_effect=factory):
    assert kit.acp_pay(order, shop_url, kit.pub_b64(shop_key), signed_receipt(8000),
                       buyer_key, policy_accept, prepared=prepared) is None
assert not any(path.endswith("/complete") for _, path, _ in calls)
print("PASS kit independently rechecks the helper's hard price ceiling")

class FakeVisit:
    instances = []
    def __init__(self, candidate):
        self.choice, self.base, self.retained = candidate, candidate["shop"]["url"], False
        self.__class__.instances.append(self)
    def probe(self, o):
        return {"ask": 6000 if self.choice["shop"]["name"] == "Bad" else 7000,
                "grade": "A", "warranty_hours": 6}
    def negotiate(self, o, deadline):
        return signed_receipt(), policy_accept
    def preflight(self, *args):
        if self.choice["shop"]["name"] == "Bad":
            raise ValueError("certificate did not verify")
        return prepared
    def close(self):
        pass

options = [{**choice, "shop": {"name": name, "url": "https://"+name.lower()+".test"}}
           for name in ["Bad", "Good"]]
with patch.object(policy, "metadata", return_value={}), patch.object(policy, "memory", return_value=({}, {})), \
     patch.object(policy, "candidates", return_value=options), patch.object(policy, "Visit", FakeVisit), \
     patch.object(policy, "log_event"), patch.object(policy, "atomic_json"):
    result = policy.run(order, [], {sku: {"list_price": 10000}})
assert result["shop"] == "https://good.test"
assert len(FakeVisit.instances) <= policy.MAX_SHOPS
assert [v.retained for v in FakeVisit.instances] == [False, True]
print("PASS same-order fallback from an unsuitable candidate without additional payment attempts")
