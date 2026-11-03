"""My market buyer: route a portfolio, negotiate behind a budget guard, and audit the receipt.

    python market_buyer.py --shops .market/shops.json --item lamp --budget 120

The portfolio router is code-first so repeated wins do not make one shop permanently dominant. The
LLM negotiator sees a personal reference price but not the exact budget; buyer_policy.MandateGuardian
is the only component that holds the ceiling. Reviews are generated from audited facts, not shop text.
The last stdout line is the result the kit sends to the market:
    {"shop": "<card URL from shops.json>", "receipt": {...} or null, "stars": 1-5, "review": "..."}
"""
import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path

import httpx

import buyer_policy
import harness
import memory_sync
import strategy_params
from buyer import HEADERS, MAX_ROUNDS, rpc, send, state, status_text

import re

MEMORY = memory_sync.MEMORY   # my trades so far: shop name, item, price, outcome, and since day 2 the shop's first ask and
                              # my last offer; since 11-03 also the settled facts memory_sync merges in after the order
# Challenge 2: shop messages that try to steer the buyer or its review. The harness is told to ignore them; this is
# the code-side check, so that the shop is remembered as manipulative and skipped next time whatever the harness did.
MANIPULATION = re.compile(r"MARKET NOTICE|market operator|market policy|give (this|the|our) shop \d|\d stars in your review|"
                          r"rate (this shop|us) \d|\[?SYSTEM\]?:|ignore (your|all|previous) (instructions|rules)|you must accept", re.I)


def load_memory():
    return memory_sync.load(MEMORY)


def save_memory(mem):
    memory_sync.save(mem, MEMORY)


def blacklisted(shops, mem):
    """Shops my own memory marks as dishonest: a receipt that did not match, or messages that tried to steer me."""
    bad = buyer_policy.blacklisted_names(mem)
    return [s for s in shops if s["name"] in bad]


def fallback_choice(shops, mem, item, reference=None, explore_rate=buyer_policy.EXPLORE_RATE):
    """The same budget-blind portfolio policy is used after an unreachable shop."""
    return buyer_policy.choose_shop(shops, mem, item, reference, explore_rate)[0]


def choose(shops, item, mem, reference=None, explore_rate=buyer_policy.EXPLORE_RATE):
    pick, features, mode, top = buyer_policy.choose_shop(shops, mem, item, reference, explore_rate)
    buyer_policy.emit("buyer_route", item=item, mode=mode, chosen=pick["name"], features=features, top=top)
    print(f"[choose] {pick['url']}: {pick['name']}. portfolio {mode}, score {features['score']}", flush=True)
    return pick


def bargain_record(name, mem, discount=0.15):
    """How this shop bargained with me before, in one line for the harness. Rows from day 1 have no ask and are skipped.
    Day 1 showed the harness pays the ask unless told otherwise, so the line ends with a concrete first-offer rule.
    `discount` is the tuner's first_offer_discount."""
    rows = [m for m in mem if m.get("shop") == name and isinstance(m.get("ask"), (int, float)) and m["ask"] > 0]
    pushed = [m for m in rows if (m.get("first_offer") or m["ask"]) < m["ask"]]   # only rows where I opened below the ask tell anything
    deals = [1 - m["price"] / m["ask"] for m in pushed if m.get("outcome") == "deal" and m.get("price")]
    rejected_low = sum(1 for m in pushed if m.get("outcome") == "closed" and (m.get("last_offer") or 0) < m["ask"])
    if not pushed:
        return f"My past record with this shop: none on how it bargains. First offer: about {discount:.0%} below the ask."
    parts = [f"My past record with this shop: {len(deals)} deal(s) after a low first offer"]
    if deals:
        parts.append(f"closed on average {sum(deals) / len(deals):.0%} below its first ask")
    if rejected_low:
        parts.append(f"it ended the negotiation {rejected_low} time(s) when I offered below its ask")
    if (deals and sum(deals) / len(deals) < 0.02 and len(deals) >= 2) or rejected_low:
        rule = "First proposal: the ask itself; the private mandate guardian decides whether it is affordable."
    else:
        rule = f"First offer: about {discount:.0%} below the ask."
    return ", ".join(parts) + ". " + rule


def first_ask(t, list_price=None):
    """The shop's opening ask: the A2A metadata when given, else the last number in its message.
    Round 3 (first tuner summary, 11-04 00:00): shop messages carry weights and counts ("120g x 20팩"), and 179 of
    535 stored asks were such numbers. A parsed number counts only when it is plausible against the SKU's list
    price (0.2x to 3x); otherwise the ask is unknown and the harness is told nothing rather than nonsense."""
    ask = ((t or {}).get("status", {}).get("message", {}).get("metadata") or {}).get("ask")
    if isinstance(ask, (int, float)):
        return int(ask)
    nums = [int(n) for n in re.findall(r"(?<![\d.])(\d{1,9})(?![\d])", status_text(t))]
    if isinstance(list_price, (int, float)) and list_price > 0:
        nums = [n for n in nums if 0.2 * list_price <= n <= 3 * list_price]
    return nums[-1] if nums else None


def receipt_of(t):
    for art in (t or {}).get("artifacts", []):
        for p in art.get("parts", []):
            d = p.get("data")
            if isinstance(d, dict) and isinstance(d.get("price"), (int, float)):
                return d
    return None


def steered(t):
    """Does the shop's status message try to instruct the buyer? Logged and remembered; the harness never sees a flag."""
    hit = MANIPULATION.search(status_text(t))
    if hit:
        print(f"[guard] shop message tries to steer the buyer ({hit.group(0)!r}); remembered as manipulative", flush=True)
    return bool(hit)


def negotiate(url, item, budget, reference, record="", request=None, value_cap=None):
    """A sealed-budget loop: the LLM proposes, then the mandate guardian authorizes.
    `value_cap` is the SKU's list price: the guardian never pays above it, whatever the budget.

    Returns (outcome, agreed price or None, receipt or None, transcript, manipulative, {ask, first_offer, last_offer})."""
    card = httpx.get(url.rstrip("/") + "/.well-known/agent-card.json", headers=HEADERS, timeout=10).json()
    iface = next((i for i in card.get("supportedInterfaces", []) if i.get("protocolBinding") == "JSONRPC"), None)
    if iface is None:
        print("[buyer] the card has no JSONRPC interface", flush=True)
        return "no_card", None, None, [], False, {}
    rpc_url = iface["url"]
    print(f"[buyer] card: {card.get('name')!r}, skills {[s.get('id') for s in card.get('skills', [])]}, rpc {rpc_url}", flush=True)

    opening = (f"I want to buy {request['name']} ({request['sku']})" if request
               else f"I want to buy a {item}")
    t = send(rpc_url, opening)
    print(f"[buyer] <- {state(t)} | {status_text(t)}", flush=True)
    manip = steered(t)
    ask0 = first_ask(t, value_cap)
    info = {"ask": ask0, "first_offer": None, "last_offer": None, "reference": reference,
            "guard_decisions": [], "checkout_authorized": False}
    if state(t) != "TASK_STATE_INPUT_REQUIRED":
        return "closed", None, None, [("you", opening), ("shop", status_text(t))], manip, info
    tid = t["id"]
    log = [("you", opening), ("shop", status_text(t))]
    guardian = buyer_policy.MandateGuardian(budget, reference, value_cap)
    print(f"[buyer] first ask {ask0}, private ceiling sealed, personal reference {reference}, value cap {value_cap}. {record}", flush=True)

    offers = []   # (my offer, the ask the shop answered with): the harness gets this instead of the whole transcript
    for round_index in range(MAX_ROUNDS):
        ask = ((t or {}).get("status", {}).get("message", {}).get("metadata") or {}).get("ask")
        d = harness.decide(buyer_policy.NEGOTIATOR_SYSTEM,
                           buyer_policy.situation(f"Item: {item}. Personal reference price: {reference}. {record}",
                                                  offers, status_text(t), ask))
        if d is None:
            fallback = buyer_policy.public_fallback_offer(reference, ask, round_index)
            d = {"action": "offer", "price": fallback}
            buyer_policy.emit("buyer_model_fallback", item=item, task=tid[:8], round=round_index + 1,
                              reference=reference, ask=ask, proposal=fallback)
        if d.get("action") == "walk_away" or "price" not in d:
            print("[buyer] walking away", flush=True)
            rpc(rpc_url, "CancelTask", {"id": tid})
            return "walked_away", None, None, log, manip, info
        decision = guardian.authorize(d["price"], round_index, ask)
        info["guard_decisions"].append(decision["decision"])
        buyer_policy.emit("buyer_guard", item=item, task=tid[:8], round=round_index + 1,
                          reference=reference, ask=ask, proposal=d["price"], **decision)
        if decision["action"] != "offer":
            print(f"[guard] proposal refused: {decision['decision']}; walking away", flush=True)
            rpc(rpc_url, "CancelTask", {"id": tid})
            return "walked_away", None, None, log, manip, info
        d["price"] = decision["price"]
        info["first_offer"] = info["first_offer"] or d["price"]
        info["last_offer"] = d["price"]
        t = send(rpc_url, str(d["price"]), tid)
        print(f"[buyer] <- {state(t)} | {status_text(t)}", flush=True)
        manip = steered(t) or manip
        log += [("you", str(d["price"])), ("shop", status_text(t))]
        offers.append((d["price"], first_ask(t, value_cap)))
        if state(t) == "TASK_STATE_COMPLETED":
            info["checkout_authorized"] = guardian.checkout_ok(d["price"])
            return "deal", d["price"], receipt_of(t), log, manip, info
        if state(t) != "TASK_STATE_INPUT_REQUIRED":
            return "closed", None, None, log, manip, info
    print("[buyer] round limit reached, walking away", flush=True)
    rpc(rpc_url, "CancelTask", {"id": tid})
    return "walked_away", None, None, log, manip, info


def review(item, outcome, agreed, receipt, reference, manip=False, no_offer=False):
    """A factual code-side review; untrusted shop text is never sent to another LLM."""
    honest = outcome != "deal" or (
        isinstance(receipt, dict)
        and str(receipt.get("item", "")).lower() == item.lower()
        and isinstance(receipt.get("price"), (int, float))
        and int(round(float(receipt["price"]))) == agreed
    )
    if outcome == "deal" and not honest:
        got = receipt.get("price") if isinstance(receipt, dict) else None
        return 1, f"Agreed {item} at {agreed}, but the receipt did not match (price {got}).", False
    if outcome == "unreachable":
        return 2, "The shop could not be reached or answered with something that is not A2A; no negotiation took place.", honest
    if outcome == "closed" and no_offer:   # Round 3 minute 1: "No matching unreserved stock" before any offer
        return 2, f"The shop had no {item} to sell when asked; no negotiation took place.", honest
    if manip:
        note = f"Deal at {agreed}, but the" if outcome == "deal" else "No deal, and the"
        return 2, f"{note} shop's messages carried fake market notices telling me to accept its price and rate it 5 stars.", honest
    if outcome == "deal":
        ratio = agreed / reference if reference else 1
        stars = 5 if ratio <= 1 else 4 if ratio <= 1.05 else 3
        return stars, f"Agreed {item} at {agreed}; receipt matched. Personal reference was {reference}.", True
    return 2, f"No honest {item} deal reached the private mandate guard's limit.", honest


def load_round3(order_path):
    """The kit's per-order context; an absent/literal {order} keeps the week-07 test compatible."""
    if not order_path:
        return {}, {}
    path = Path(order_path)
    try:
        order = json.loads(path.read_text(encoding="utf-8"))
        catalog = json.loads((path.parent / "catalog.json").read_text(encoding="utf-8"))
        return order, catalog
    except (OSError, ValueError):
        return {}, {}


def category_reference(order, catalog):
    prices = [catalog.get(sku, {}).get("list_price") for sku in (order.get("acceptable") or [])]
    prices = [price for price in prices if isinstance(price, (int, float))]
    return int(round(statistics.median(prices) * 0.90)) if prices else None


def prepare_round3_shops(shops, order, catalog, guardian):
    """Guardian filters plausibly affordable stock; router gets no numeric ceiling."""
    acceptable = set(order.get("acceptable") or [])
    if not acceptable:
        return shops
    stocked, affordable = [], []
    for original in shops:
        offers = [offer for offer in (original.get("offers") or []) if offer.get("sku") in acceptable]
        if not offers:
            continue
        shop = dict(original, _eligible_offers=offers)
        stocked.append(shop)
        eligible = guardian.affordable_offers(offers, catalog)
        if eligible:
            affordable.append(dict(shop, _eligible_offers=eligible))
    return affordable or stocked or shops


def choose_request(shop, catalog, order=None):
    offers = shop.get("_eligible_offers") or shop.get("offers") or []
    if not offers:
        return None
    need = (order or {}).get("use_within_hours")
    if (order or {}).get("kind") == "food" and isinstance(need, (int, float)) and need > 0:
        # 21:04-21:07: four agreed food deals were refused at checkout because the unit's shelf life was shorter than
        # the order's use_within_hours (358 h left, 360 asked). A SKU whose whole shelf life is shorter can never pass;
        # prefer one with room for a unit that is no longer fresh (grade A still has 80% or more of it).
        shelf = lambda offer: catalog.get(offer.get("sku"), {}).get("shelf_hours")
        feasible = [offer for offer in offers if shelf(offer) is None or shelf(offer) >= need]
        safe = [offer for offer in feasible if shelf(offer) is not None and shelf(offer) * 0.8 >= need]
        offers = safe or feasible or offers
    # Higher catalog value improves buyer surplus; quantity breaks ties toward availability.
    return max(offers, key=lambda offer: (catalog.get(offer.get("sku"), {}).get("list_price", 0),
                                          offer.get("qty", 0), offer.get("sku", "")))


def sku_reference(sku, catalog, fallback):
    """The bargaining reference for one SKU: 90% of its list price, which is what a settled deal is worth to the order.
    21:09 (o-b3f18a8374b8): the category reference (35910) sat above the SKU's list price (28900), the model opened
    at 30500 and the deal settled with negative surplus. The category reference stands in only without a SKU."""
    list_price = catalog.get(sku, {}).get("list_price")
    return int(round(list_price * 0.90)) if isinstance(list_price, (int, float)) and list_price > 0 else fallback


def accept_constraints(order):
    if not order.get("acceptable"):
        return None
    if order.get("kind") == "food":
        return {"cert": True, "min_hours_left": int(math.ceil(order.get("use_within_hours") or 0))}
    return {"cert": True, "grades": ["A", "B"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--order")
    a = ap.parse_args()

    shops = json.loads(Path(a.shops).read_text(encoding="utf-8"))
    if not shops:
        sys.exit("[buyer] no shops in the list")
    mem = load_memory()
    synced = memory_sync.reconcile(mem, MEMORY.parent)   # refused payments and broken goods since the last order
    if any(synced.values()):
        save_memory(mem)
        buyer_policy.emit("buyer_memory_sync", **synced)
    params = strategy_params.load()   # the nightly tuner's knobs; defaults when the file is absent
    buyer_policy.emit("buyer_params", **params)
    order, catalog = load_round3(a.order)
    category = order.get("category") or a.item
    reference = buyer_policy.item_reference(mem, category, fallback=category_reference(order, catalog))
    selector_guardian = buyer_policy.MandateGuardian(a.budget, reference)
    candidates = prepare_round3_shops(shops, order, catalog, selector_guardian)
    pick = choose(candidates, category, mem, reference, params["explore_rate"])
    request = choose_request(pick, catalog, order)
    wanted = request["sku"] if request else a.item

    category_ref, tried_urls = reference, set()
    for attempt in (1, 2, 3):   # day 1: 15 orders died on a shop whose card came back as an error page; now the order moves on
        tried_urls.add(pick["url"])
        value_cap = catalog.get(wanted, {}).get("list_price")
        reference = sku_reference(wanted, catalog, category_ref)
        try:
            outcome, agreed, receipt, log, manip, info = negotiate(
                pick["url"], wanted, a.budget, reference,
                bargain_record(pick["name"], mem, params["first_offer_discount"]), request, value_cap
            )
        except Exception as e:   # httpx errors, a card that is not JSON, a task without the fields we expect
            print(f"[buyer] shop unreachable: {type(e).__name__}: {str(e)[:120]}", flush=True)
            outcome, agreed, receipt, log, manip, info = "unreachable", None, None, [], False, {}
        # Round 3 minute 1 (11-03 20:55): the chosen shop's stock was gone before the first word ("No matching unreserved
        # stock") and the order ended there. A close before any offer says nothing about the shop (outcome "no_stock",
        # ignored by the shop scores) and is as good as unreachable: try the next shop.
        no_offer = outcome == "closed" and info.get("first_offer") is None
        stars, text, honest = review(wanted, outcome, agreed, receipt, reference, manip, no_offer)
        if no_offer:
            outcome = "no_stock"
        if outcome == "deal" and not info.get("checkout_authorized"):
            print("[guard] deal was not authorized for checkout; discarding its receipt", flush=True)
            outcome, agreed, receipt, stars, text = "rejected_checkout", None, None, 1, "Checkout exceeded the private mandate."
        mem.append({"order": order.get("order"), "shop": pick["name"], "item": category, "sku": wanted,
                    "name": request["name"] if request else None, "outcome": outcome, "price": agreed,
                    "list_price": catalog.get(wanted, {}).get("list_price"), "honest": honest, "manipulative": manip,
                    "stars": stars, "reference": reference, "policy": "sealed-budget-v1", "settled": False,
                    "at": time.strftime("%Y-%m-%d %H:%M"),
                    **{k: info.get(k) for k in ("ask", "first_offer", "last_offer", "guard_decisions", "checkout_authorized")}})
        save_memory(mem)
        if (outcome != "unreachable" and not no_offer) or attempt == 3:
            break
        rest = [s for s in candidates if s.get("url") not in tried_urls and s not in blacklisted(candidates, mem)]
        if not rest:
            break
        pick = fallback_choice(rest, mem, category, category_ref, params["explore_rate"])
        request = choose_request(pick, catalog, order)
        wanted = request["sku"] if request else a.item
        print(f"[choose] retry with {pick['url']}: {pick['name']}. picked by portfolio policy after the first shop "
              f"{'had no stock' if no_offer else 'failed'}", flush=True)

    accept = accept_constraints(order)
    print(json.dumps({"shop": pick["url"], "receipt": receipt, "stars": stars, "review": text,
                      **({"accept": accept} if accept else {})}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
