"""Round 3 optional: the buyer's own ACP checkout, with tools built from the shop's acp.json.

    python acp_buyer.py --pay .market/pay.json   ->  {"mandate_receipt": {...}}  or  {"reason": "..."}

The kit (patched market_kit.py, "pay" hook) hands over the order with its open mandates, the shop URL and
public key, the agreed receipt and the buyer's accept policy. This script
  1. GETs <shop>/.well-known/acp.json and turns every entry of its "tools" list into a callable
     (method + path template + body) -- nothing about the checkout flow is hard-coded here,
  2. asks the harness, from the tool descriptions alone, which tools to call in what order; code checks the
     plan opens and completes a session (fallback: create -> complete),
  3. runs the plan with the checks the kit would make: the checkout JWT is signed by the shop and matches
     item, price and task; the price is within the payment mandate's amount_range; the unit passes `accept`,
  4. signs the closed AP2 checkout and payment mandates with buyer_key.pem and completes.
"""
import argparse, base64, hashlib, json, re, time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

import harness


def log(m):
    print(f"[acp-buyer] {m}", flush=True)


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64url(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def jwt_payload(token, pubkey_b64):
    try:
        head, body, sig = token.split(".")
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey_b64)).verify(unb64url(sig), f"{head}.{body}".encode())
        return json.loads(unb64url(body))
    except (InvalidSignature, ValueError, TypeError, AttributeError):
        return None


# ---------------------------------------------------------------- tools from acp.json

def build_tools(card: dict, client: httpx.Client):
    """Each acp.json tool -> a callable(**args). Path params like {id} come from args, the rest is the JSON body."""
    base = card["endpoint"].rstrip("/")
    tools = {}
    for t in card.get("tools", []):
        params = re.findall(r"{(\w+)}", t["path"])

        def call(_t=t, _params=params, **kw):
            path = _t["path"]
            for k in _params:
                path = path.replace("{" + k + "}", str(kw.pop(k)))
            r = client.request(_t["method"], base + path, json=kw if _t["method"] != "GET" else None)
            return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:200])
        call.__name__, call.__doc__ = t["name"], f"{t['method']} {t['path']} input {t.get('input') or {}}"
        tools[t["name"]] = call
    return base, tools


def plan_with_harness(tools: dict):
    menu = "\n".join(f"- {n}: {f.__doc__}" for n, f in tools.items())
    data = harness.ask_json("You must pay for an agreed purchase through a shop's ACP checkout using ONLY these tools:\n" + menu +
                            "\nList the tool names to call, in order, to open a checkout session and pay for it. Do not cancel. "
                            'Reply with exactly one JSON object: {"plan": ["<tool>", ...]}.', log) or {}
    plan = [p for p in data.get("plan", []) if p in tools] if isinstance(data.get("plan"), list) else []
    creates = [n for n in tools if "create" in n]; completes = [n for n in tools if "complete" in n]
    if not (plan and plan[0] in creates and plan[-1] in completes):
        harness.CALLS["fallbacks"] += 1
        plan = creates[:1] + completes[:1]
        log(f"plan from the harness unusable ({data.get('plan')}); fallback {plan}")
    else:
        log(f"plan: {plan}")
    return plan


FOOD_GRADE_CUTS = (0.8, 0.45)        # the market's: share of shelf life left for food grade A, B


def check_accept(o, co, accept):
    """The kit's accept check, same reasons. The unit's grade and expiry come from the market's certificate in the
    checkout (signed with order.market_pubkey), never from what the shop claims."""
    cert, body = co.get("cert"), {}
    if cert is not None:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(o["market_pubkey"])).verify(
                base64.b64decode(cert["sig"]), canon(cert["cert"]))
            if cert["cert"].get("unit") == co.get("unit") and cert["cert"].get("sku") == co.get("item"):
                body = cert["cert"]
        except (InvalidSignature, KeyError, ValueError, TypeError):
            pass
        if not body:
            return "the unit certificate is not the market's certificate of this unit"
    if not isinstance(accept, dict) or not accept:
        return None
    hours = (body["expires_at"] - time.time()) / 3600 if body.get("expires_at") else None
    grade = body.get("grade")
    if hours is not None and o.get("shelf_hours"):
        left = hours / o["shelf_hours"]
        grade = "A" if left >= FOOD_GRADE_CUTS[0] else "B" if left >= FOOD_GRADE_CUTS[1] else "C"
    if accept.get("cert") and not body:
        return "no certificate, and the policy asks for one"
    if isinstance(accept.get("min_hours_left"), (int, float)) and o.get("kind") == "food":
        if hours is None:
            return "no certificate to show the shelf life"
        if hours < accept["min_hours_left"]:
            return f"{hours:.2f} h of shelf life left, the policy asks for {accept['min_hours_left']}"
    if isinstance(accept.get("grades"), list) and accept["grades"]:
        if grade is None:
            return "no certificate to show the grade"
        if grade not in accept["grades"]:
            return f"grade {grade}, the policy accepts {accept['grades']}"
    if isinstance(accept.get("warranty_hours"), (int, float)) and o.get("kind") == "goods":
        if (co.get("warranty_hours") or 0) < accept["warranty_hours"]:
            return f"warranty {co.get('warranty_hours') or 0} h, the policy asks for {accept['warranty_hours']}"
    return None


# ---------------------------------------------------------------- the checkout

def pay(p: dict, key):
    o, shop, receipt, accept = p["order"], p["shop"].rstrip("/"), p["receipt"], p.get("accept")
    c = httpx.Client(timeout=60)
    try:
        r = c.get(f"{shop}/.well-known/acp.json")
        if r.status_code != 200:
            return None, f"{shop} serves no acp.json ({r.status_code})"
        card = r.json()
        base, tools = build_tools(card, c)
        if urlparse(base).hostname != urlparse(shop).hostname:
            return None, f"ACP endpoint {base} is not on the shop's host"
        log(f"acp.json: {len(tools)} tools -> {list(tools)}")
        plan = plan_with_harness(tools)

        create = {"line_items": [{"id": receipt["item"], "quantity": 1}], "receipt": receipt}
        if o.get("acceptable"):
            create["acceptable"] = o["acceptable"]
        code, s = tools[plan[0]](**create)
        if code not in (200, 201) or not isinstance(s, dict):
            return None, f"checkout refused: {code} {str(s)[:200]}"
        co = jwt_payload(s.get("checkout_jwt", ""), p["shop_pub"])
        budget = next(x["max"] for x in o["mandates"]["payment"]["mandate"]["constraints"] if x["type"] == "payment.amount_range")
        if not co or not (co.get("item") == receipt["item"] or co.get("item") in (o.get("acceptable") or [])) \
                or (co.get("price"), co.get("task")) != (receipt["price"], receipt["task"]):
            return None, "the checkout does not match the agreed receipt (or is not signed by the shop)"
        if co["price"] > budget:
            return None, f"checkout total {co['price']} is above the mandate's limit {budget}"
        refuse = check_accept(o, co, accept) if o.get("acceptable") else None
        if refuse:
            cancel = next((n for n in tools if "cancel" in n), None)
            if cancel:
                tools[cancel](id=s["id"])
            return None, f"not paying: {refuse}"
        log(f"checkout {s['id']}: unit {co.get('unit')} claim {co.get('claim')} cert {co.get('cert')} price {co['price']} <= {budget}")

        h = b64url(hashlib.sha256(s["checkout_jwt"].encode()).digest())
        opens = {k: hashlib.sha256(canon(v["mandate"])).hexdigest() for k, v in o["mandates"].items()}
        cm = {"vct": "mandate.checkout.1", "order": o["order"], "open_mandate": opens["checkout"],
              "checkout_jwt": s["checkout_jwt"], "checkout_hash": h}
        pm = {"vct": "mandate.payment.1", "order": o["order"], "open_mandate": opens["payment"], "payee": shop,
              "amount": co["price"], "currency": "credits", "checkout_hash": h}
        sign = lambda m: {"mandate": m, "sig": base64.b64encode(key.sign(canon(m))).decode()}
        for step in plan[1:-1]:                                    # e.g. get_checkout_session, harmless
            tools[step](id=s["id"])
        code, done = tools[plan[-1]](id=s["id"], payment_data={"order": o["order"], "checkout_mandate": sign(cm),
                                                                "payment_mandate": sign(pm)})
        if code != 200 or not isinstance(done, dict):
            return None, f"payment not completed: {code} {str(done)[:300]}"
        mr = done["order"]["mandate_receipt"]
        log(f"paid {co['price']}, settled at ledger #{mr.get('ledger')}: {mr.get('name')} grade {mr.get('grade')}")
        return mr, None
    except (httpx.HTTPError, ValueError, KeyError, TypeError, StopIteration) as e:
        return None, f"payment failed: {type(e).__name__}: {e}"
    finally:
        c.close()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--pay", required=True); a = ap.parse_args()
    p = json.loads(Path(a.pay).read_text(encoding="utf-8"))
    key = serialization.load_pem_private_key(Path("buyer_key.pem").read_bytes(), password=None)
    mr, reason = pay(p, key)
    log(f"done: {'paid' if mr else reason}; harness calls {harness.CALLS['n']} fallbacks {harness.CALLS['fallbacks']}")
    print(json.dumps({"mandate_receipt": mr} if mr else {"reason": reason}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
