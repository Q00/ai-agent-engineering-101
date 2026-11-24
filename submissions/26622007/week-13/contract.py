"""Negotiation and the sealed contract (meshkit.transaction-contract/v1).

Two rule-based agents trade offers until the buyer's bid meets the seller's ask.
The agreement is sealed into the wrapper {body, hash, auth_tags} that
mock_payment.py reads: body.offer.total, body.parties.seller, body.contract_id.
Week 13 code never changes a sealed wrapper; it only refers to its id and hash.
"""
import hashlib
import hmac

from mock_payment import canonical, digest

SCHEMA = "meshkit.transaction-contract/v1"
SELLER = "nocha12"
BUYER = "user-agent"
# Demo keys, like PLATFORM_SECRET in mock_payment.py. HMAC is a shared-key tag:
# it shows integrity and that a key holder made it, not which party signed.
PARTY_KEYS = {"buyer": b"week13-demo-buyer-key", "seller": b"week13-demo-seller-key"}
MAX_ROUNDS = 1000   # a backstop; the talk ends on agreement or when neither side can move


def negotiate(item, qty, list_price, seller_floor, buyer_budget):
    """Unit-price haggling. The seller opens at the list price and comes down 500 a
    round, never under its floor; the buyer opens at 60% and goes up 700 a round,
    never over its budget. Returns {"messages": [...], "agreement": {...} | None}."""
    if min(qty, list_price, seller_floor, buyer_budget) < 1:
        raise ValueError("quantity and prices must be 1 or more")
    messages = []
    ask, bid = list_price, min(buyer_budget, round(list_price * 0.6))
    for turn in range(MAX_ROUNDS):
        messages.append({"turn": turn, "from": SELLER, "act": "offer", "unit_price": ask})
        if bid >= ask:
            messages.append({"turn": turn, "from": BUYER, "act": "accept", "unit_price": ask})
            agreement = {"item": item, "qty": qty, "unit_price": ask, "total": ask * qty,
                         "currency": "credits", "buyer": BUYER, "seller": SELLER,
                         "text": f"{BUYER} buys {qty} x {item} from {SELLER} at {ask} credits each, "
                                 f"{ask * qty} credits in total."}
            return {"messages": messages, "agreement": agreement}
        if ask == seller_floor and bid == buyer_budget:
            turn += 1
            break
        messages.append({"turn": turn, "from": BUYER, "act": "counter", "unit_price": bid})
        ask = max(seller_floor, ask - 500)
        bid = min(buyer_budget, bid + 700)
    else:
        turn = MAX_ROUNDS
    messages.append({"turn": turn, "from": BUYER, "act": "walk_away"})
    return {"messages": messages, "agreement": None}


def party_tag(role, body):
    return hmac.new(PARTY_KEYS[role], canonical(body), hashlib.sha256).hexdigest()


def seal(negotiation, contract_id, created_at):
    """Seal an agreement into the contract wrapper. The body carries the hash of the
    negotiation messages, so the contract points back at the talk that produced it."""
    a = negotiation["agreement"]
    if a is None:
        raise ValueError("no agreement to seal")
    body = {
        "schema": SCHEMA,
        "contract_id": contract_id,
        "created_at": created_at,
        "parties": {"buyer": a["buyer"], "seller": a["seller"]},
        "offer": {"item": a["item"], "qty": a["qty"], "unit_price": a["unit_price"],
                  "total": a["total"], "currency": a["currency"]},
        "terms": {"refund": "full refund to the payer; the fee is returned too"},
        "negotiation_hash": digest({"messages": negotiation["messages"]}),
    }
    return {"body": body, "hash": digest(body),
            "auth_tags": {role: party_tag(role, body) for role in PARTY_KEYS}}


def check_wrapper(wrapper):
    """Problems with a sealed wrapper; an empty list means it holds."""
    body, problems = wrapper["body"], []
    if body.get("schema") != SCHEMA:
        problems.append("unsupported contract schema")
    if digest(body) != wrapper["hash"]:
        problems.append("contract hash mismatch")
    for role in PARTY_KEYS:
        if not hmac.compare_digest(party_tag(role, body), wrapper["auth_tags"].get(role, "")):
            problems.append(f"{role} auth tag does not verify")
    return problems
