"""act.*: the actions that end an agent's turn (HARNESS.md 3.2, 3.4), and rec.label.

Each act.* tool runs the same checks as check.* again and refuses what breaks a limit, with the reason
and what would pass. It acts on ctx.situation, the decision at hand that the entry point filled in
(the unit and the buyer's offer for the seller, the order and the shops for the buyer, ...).

What an act.* tool returns is the decision; the entry point carries it out (an A2A reply, the result
line for the kit). act.post and act.vote write to .market/outbox.jsonl themselves, which the kit posts.
"""
import json
import math
import re

from core import atomic, paths
from tools import check
from tools import market_files as mf
from tools.calc import _SUSPECT, extract
from tools.registry import Refused, tool

_SAY = {"type": "string", "maxLength": 400}
_PRICE = {"type": "integer", "minimum": 1}
_AS = {"type": "string", "enum": ["shop", "buyer"]}


def _need(ctx, key, what):
    v = ctx.situation.get(key)
    if v is None:
        raise Refused(f"no {what} in this decision", "this action does not fit the decision at hand")
    return v


def _own_text(text):
    """What we write must not look like an instruction to other agents (core: limits)."""
    if _SUSPECT.search(text or ""):
        raise Refused("the text reads like an instruction or a notice aimed at other agents",
                      "say only true things about the deal, the item or the market")


def _say_price(ctx, say, price, unit=None, label="Price"):
    """The message the other side reads must carry the price the tool checked: a different number is refused, a
    message without one gets it added. A grade named in the message must be true now (check.claim)."""
    ex = extract(ctx, say or "", 0)
    if ex["price"] is not None and ex["price"] != price:
        raise Refused(f"your message names {ex['price']} but the price is {price}",
                      "write the same number in the message (for example '... 21000 KRW'), or leave the number out")
    if unit and ex["claimed_grade"]:
        check.claim(ctx, unit, ex["claimed_grade"])
    return say if ex["price"] is not None else f"{(say or '').rstrip()} {label}: {price}.".strip()


# --- seller

def _seller_unit(ctx, unit):
    chosen = ctx.situation.get("unit")
    unit = unit or chosen
    if unit is None:
        raise Refused("no unit chosen yet", "name one of the candidate units in 'unit'")
    if chosen is None and unit not in (ctx.situation.get("candidates") or []):
        raise Refused(f"unit {unit} does not fit what this buyer asked for",
                      f"candidates: {ctx.situation.get('candidates')}")
    if chosen is not None and unit != chosen:
        raise Refused(f"this negotiation is for unit {chosen}, not {unit}", "keep the unit, or end and start over")
    return unit


def list_floor(strategy, first_time, list_price):
    """The list-price floor for a price, or 0 (one rule for act.counter/accept and the seller's prompt)."""
    rb = strategy.get("repeat_buyer") or {}
    at_cost = (strategy.get("first_visit") or {}).get("price") == "at_cost"
    if rb.get("min_price") != "list" or (at_cost and first_time is not False) or not list_price:
        return 0        # a first visit at cost has its own rule; without that promise every buyer starts from list
    return math.ceil(list_price * rb.get("min_share_of_list", 1.0))


def unit_list_price(ctx, unit):
    u = mf.unit(ctx, unit) or {}
    return u.get("list_price") or (mf.catalog(ctx).get(u.get("sku")) or {}).get("list_price")


def unit_floor(ctx, unit, first_time):
    """The list floor for this unit, or 0 for a unit held longer than playbook.seller.clearance_hours (11/4 05:0x:
    unsold stock is cleared at cost x (1 + min_margin) so its cash goes back into the best sellers)."""
    pb = ((ctx.strategy.get("playbook") or {}).get("seller") or {})
    u = mf.unit(ctx, unit) or {}
    if pb.get("clearance_hours") and u.get("supplied_at") and ctx.now() - u["supplied_at"] >= pb["clearance_hours"] * 3600:
        return 0
    return list_floor(ctx.strategy, first_time, unit_list_price(ctx, unit))


def _repeat_floor(ctx, unit, price):
    """User 11/3: a buyer that is not on its first visit negotiates from the list price, down to min_share_of_list
    of it at most (user 22:25: 0.9 - at list, returning buyers stopped at 90% and walked: 1 sale in 25 min)."""
    lp = unit_list_price(ctx, unit)
    floor = unit_floor(ctx, unit, ctx.situation.get("first_time"))
    if floor and price < floor:
        share = (ctx.strategy.get("repeat_buyer") or {}).get("min_share_of_list", 1.0)
        raise Refused(f"{price} is below {floor} ({share:.0%} of the list price {lp}); "
                      "a returning buyer pays at least that (strategy)", f"ask {floor} or more")


@tool("act.counter", "Seller: answer with a price for the unit (and choose the unit on the first turn). Checks the "
      "price against cost and the limits first.",
      {"type": "object", "required": ["price", "say"],
       "properties": {"price": _PRICE, "say": _SAY, "unit": {"type": "string"}}}, side_effect="A2A reply")
def counter(ctx, price, say, unit=None):
    unit = _seller_unit(ctx, unit)
    check.price(ctx, unit, price)
    fv = ctx.strategy.get("first_visit") or {}
    if fv.get("price") == "at_cost" and ctx.situation.get("first_time") is True:
        cost = (mf.unit(ctx, unit) or {}).get("cost")
        if cost is not None and price != cost:
            raise Refused(f"a first visit is offered at our cost ({cost}), not {price} (our shop card promises it)",
                          f"counter at {cost}; if the buyer offers more, act.accept takes its offer")
    _repeat_floor(ctx, unit, price)
    _own_text(say)
    return {"action": "counter", "unit": unit, "price": price, "say": _say_price(ctx, say, price, unit)}


@tool("act.accept", "Seller: accept and sign the receipt at the buyer's last offer, or at our last ask when the buyer "
      "accepted it in words (then pass price = that ask). Optionally claim the unit's grade "
      "(must be true now) and give a warranty in hours (goods; refunds the full price if it breaks within it). The "
      "market's certificate always goes with the unit: our shop card promises it.",
      {"type": "object", "required": ["say"],
       "properties": {"say": _SAY, "unit": {"type": "string"}, "price": _PRICE,
                      "claim": {"type": "string", "enum": ["A", "B", "C"]},
                      "warranty_hours": {"type": "number", "minimum": 0, "maximum": 168}},
       "additionalProperties": False}, side_effect="signed receipt")
def accept(ctx, say, unit=None, price=None, claim=None, warranty_hours=None):
    unit = _seller_unit(ctx, unit)
    offer, ask = ctx.situation.get("offer"), ctx.situation.get("ask")
    price = offer if price is None else price
    if price is None:
        raise Refused("the buyer has not offered a price", "if it accepted your ask in words, pass price = your ask; "
                      "else counter or end")
    if price not in (offer, ask):
        raise Refused(f"{price} is neither the buyer's offer ({offer}) nor our last ask ({ask})",
                      "accept one of those, or counter")
    check.price(ctx, unit, price)
    _repeat_floor(ctx, unit, price)
    if claim:
        check.claim(ctx, unit, claim)
    _own_text(say)
    wr = ctx.strategy.get("warranty") or {}
    if wr and (mf.unit(ctx, unit) or {}).get("kind") in (wr.get("kinds") or ["goods"]):
        warranty_hours = max(warranty_hours or 0, wr["hours"])     # our card promises it: always at least this
    return {"action": "accept", "unit": unit, "price": price, "say": _say_price(ctx, say, price, unit, "Deal at"),
            "claim": claim, "warranty_hours": warranty_hours or None, "cert": True}


@tool("act.reject", "Seller: end the negotiation without a deal.",
      {"type": "object", "required": ["say"], "properties": {"say": _SAY}}, side_effect="A2A reply")
def reject(ctx, say):
    _own_text(say)
    return {"action": "reject", "say": say}


# --- buyer

@tool("act.pick", "Buyer: choose the shop and the SKU to negotiate for. Only shops in this order's list that have "
      "the SKU and are not blocked.",
      {"type": "object", "required": ["shop", "sku"],
       "properties": {"shop": {"type": "string"}, "sku": {"type": "string"}, "why": _SAY}})
def pick(ctx, shop, sku, why=""):
    shops = _need(ctx, "shops", "shop list")
    s = shops.get(shop)
    if s is None:
        raise Refused(f"{shop!r} is not a shop we may pick for this order", f"shops: {sorted(shops)[:20]}")
    if sku not in (s.get("skus") or []):
        raise Refused(f"{shop} has no {sku} that this order accepts", f"{shop} has: {s.get('skus')}")
    return {"action": "pick", "shop": shop, "sku": sku, "why": why}


@tool("act.offer", "Buyer: offer a price (to take the shop's ask, offer exactly the ask). Never above the budget.",
      {"type": "object", "required": ["price"], "properties": {"price": _PRICE, "say": _SAY}}, side_effect="A2A message")
def offer(ctx, price, say=""):
    check.offer(ctx, price, _need(ctx, "budget", "budget"))
    _own_text(say)
    return {"action": "offer", "price": price, "say": _say_price(ctx, say, price, label="Offer")}


@tool("act.walk_away", "Buyer: end this negotiation without a deal.",
      {"type": "object", "properties": {"why": _SAY}}, side_effect="A2A cancel")
def walk_away(ctx, why=""):
    return {"action": "walk_away", "why": why}


@tool("act.finish", "Buyer: set what the checkout must show before the kit pays for the agreed receipt: the market's "
      "certificate, the least shelf life in hours (food), the grades we accept, the least warranty in hours (goods). "
      "The kit refuses to pay when the unit does not meet it. Leave a field out to not require it. The kit can check "
      "grades and min_hours_left only from the market's certificate: without one it refuses to pay any policy that "
      "asks for them (and the deal is lost).",
      {"type": "object", "required": ["accept"],
       "properties": {"accept": {"type": "object", "properties": {
           "cert": {"type": "boolean"}, "min_hours_left": {"type": "number", "minimum": 0},
           "grades": {"type": "array", "items": {"type": "string", "enum": ["A", "B", "C"]}},
           "warranty_hours": {"type": "number", "minimum": 0}}, "additionalProperties": False},
           "why": _SAY}})
def finish(ctx, accept, why=""):
    _need(ctx, "receipt", "receipt")
    if accept.get("grades") == []:
        raise Refused("an empty grade list accepts nothing", "leave grades out, or list the grades we accept")
    return {"action": "finish", "accept": accept, "why": why}


# --- restock

@tool("act.buy", "Restock: buy this plan (an empty plan buys nothing). Runs check.buy first.",
      {"type": "object", "required": ["plan"],
       "properties": {"plan": {"type": "array", "items": {
           "type": "object", "required": ["sku", "qty"],
           "properties": {"sku": {"type": "string"}, "qty": {"type": "integer", "minimum": 1, "maximum": 20}}}},
           "why": _SAY}}, side_effect="kit buys stock")
def buy(ctx, plan, why=""):
    plan, trimmed = fit_plan(ctx, plan)
    checked = check.buy(ctx, plan) if plan else {"lines": 0}
    return {"action": "buy", "plan": plan, "why": why, "checked": checked, "trimmed": trimmed or None}


def allocate(room, wants):
    """[(sku, units wanted)] in priority order -> [(sku, units we can buy, why short or None)] within the room:
    per-SKU room, then the cash and the stock cap at the expected cost, and the cash floor at the worst cost
    (check.room)."""
    cash, stock = max(0, room["cash"] or 0), max(0, room["stock"])
    hard = max(0, room["cash_worst"] if room.get("cash_worst") is not None else cash)
    out = []
    for sku, n in wants:
        w = room["worst"](sku)
        if w <= 0:
            out.append((sku, 0, "not in the catalog"))
            continue
        wu = room["unit_worst"](sku) if room.get("unit_worst") else w
        per = max(0, room["per_sku"] - room["have"].get(sku, 0))
        q = min(n, per, int(cash // w), int(stock // w), int(hard // wu))
        why = (None if q == n else "per-SKU limit" if q == per else "cash" if q in (int(cash // w), int(hard // wu))
               else "stock cap")
        cash, stock, hard = cash - q * w, stock - q * w, hard - q * wu
        out.append((sku, q, why))
    return out


def fit_plan(ctx, plan):
    """11/4 01:3x: 32 restock plans in 4 h bought 4 times - most were refused for a few units over the per-SKU limit
    or the cash floor, and with model turns of 40-60 s there was no time to plan again. Lines of one SKU are merged,
    then each line gets the most units the room allows (check.room, read once), and check.buy confirms the result.
    (plan, what was cut)."""
    room = check.room(ctx)
    if room["cash"] is None:
        raise Refused("no balance in inventory.json yet", "wait for the kit's next sync (every 10 s)")
    merged = {}
    for line in plan:
        merged[line["sku"]] = merged.get(line["sku"], 0) + line["qty"]
    out, cut = [], []
    for sku, q, why in allocate(room, list(merged.items())):
        if q < merged[sku]:
            cut.append(f"{sku}: {merged[sku]} -> {q} ({why})")
        if q > 0:
            out.append({"sku": sku, "qty": q})
    if out:
        check.buy(ctx, out)
    return out, cut


# --- review

@tool("act.review", "Review: the stars (1-5) and one true sentence for the deal. When the stars differ from the fact "
      "baseline (calc.fact_stars), 'why' must say the strategic reason.",
      {"type": "object", "required": ["stars", "text"],
       "properties": {"stars": {"type": "integer", "minimum": 1, "maximum": 5},
                      "text": {"type": "string", "minLength": 1, "maxLength": 300}, "why": _SAY}},
      side_effect="review sent with the result")
def review(ctx, stars, text, why=""):
    base = ctx.situation.get("fact_stars")
    if base is not None and stars != base and not (why or "").strip():
        raise Refused(f"{stars} stars differ from the fact baseline {base}", "give the reason in 'why', or use the baseline")
    _own_text(text)
    return {"action": "review", "stars": stars, "text": text, "why": why, "fact_stars": base}


# --- forum

def _outbox(ctx, line):
    atomic.append_line(ctx.market_dir / "outbox.jsonl", json.dumps(line, ensure_ascii=False))


@tool("act.post", "Forum: post a true, useful fact as our shop or our buyer: about ledger settles we hold (refs), or "
      "about one fact line of ours (fact: a wanted order of our buyer, or goods our shop holds). The kit sends it "
      "(one post per 5 minutes per side). Ends the forum turn.",
      {"type": "object", "required": ["text", "refs", "as"],
       "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 300},
                      "refs": {"type": "array", "items": {"type": "integer"}}, "as": _AS,
                      "fact": {"type": ["string", "null"]},
                      "thread": {"type": ["integer", "string", "null"]}}}, side_effect="outbox")
def post(ctx, text, refs, thread=None, fact=None, **kw):
    side = kw["as"]
    known = set(ctx.situation.get("known_ledger") or [])
    bad = [r for r in refs if r not in known]
    if bad:
        raise Refused(f"refs {bad} are not ledger entries we know of", f"known: {sorted(known)[-20:]}")
    _own_text(text)
    # User 11/3: only posts built on a settle line get votes. A post cites at least one settle we hold, every
    # number in it must be in the cited lines, and the tool (not the model) appends those lines as they are.
    # User 11/3 22:10: more posts - a post may instead stand on one fact line of ours (our buyer's wanted order,
    # goods our shop holds), with the same rule for numbers; fact lines are not appended.
    settles, facts = ctx.situation.get("settles") or {}, ctx.situation.get("facts") or {}
    if not refs and not fact:
        raise Refused("a post needs a ledger settle in refs or one of our fact lines",
                      "cite a settle or a fact from the list, or act.skip")
    want = ctx.situation.get("post_as")
    if want and side != want:
        raise Refused(f"this turn posts as {want}", f"post as {want}, or act.skip")
    if fact is not None and (fact not in facts or facts[fact][0] != side):
        raise Refused(f"no fact {fact!r} for our {side}",
                      f"cite one of {[k for k, v in facts.items() if v[0] == side][:10]}")
    mine = (ctx.situation.get("side_refs") or {}).get(side)
    if refs and mine is not None and not set(refs) & set(mine):
        raise Refused(f"none of {refs} is a deal of our {side}", f"cite one of {sorted(mine)[-10:]}")
    cited = [settles[r] for r in refs if r in settles]
    if len(cited) != len(refs):
        raise Refused(f"no settle line on hand for {[r for r in refs if r not in settles]}",
                      f"cite one of {sorted(settles)[-10:]}")
    evidence = "\n".join(cited)
    plain = (evidence + "\n" + (facts[fact][1] if fact else "") + f"\n{thread or ''}").replace(",", "")
    loose = [n for n in re.findall(r"(?<![A-Za-z0-9.])\d[\d,]*(?:\.\d+)?(?![A-Za-z0-9])", text)
             if len(n.replace(",", "")) >= 2 and n.replace(",", "") not in plain]
    if loose:
        raise Refused(f"numbers {loose} are not in the cited lines", "write only the values the lines show")
    text = f"{text.strip()}\n\n{evidence}" if evidence else text.strip()
    _outbox(ctx, {"post": text, "refs": refs, "thread": thread, "as": side})
    return {"action": "post", "as": side, "refs": refs, "fact": fact}


@tool("act.vote", "Forum: like a post (1) as our shop or our buyer; dislikes (-1) are refused (they cost money). "
      "Does not end the turn.",
      {"type": "object", "required": ["post", "value", "as"],
       "properties": {"post": {"type": ["integer", "string"]}, "value": {"type": "integer", "enum": [1, -1]}, "as": _AS}},
      side_effect="outbox")
def vote(ctx, post, value, **kw):
    if value < 0:
        # patch 3.4 (11/6): from 12:00 KST the 4th new dislike stance in an hour costs 20,000 x (k - 3) from the shop's
        # cash, so it lowers book; one dislike stance weighs 1 however many posts it touches (user 11/6 09:4x: no
        # dislikes - a limit enforced here, not in the prompt, R10)
        raise Refused("dislikes are off: they cost the shop's cash from 11/6 12:00 (patch 3.4)",
                      "like a useful post, or leave it")
    if post in (ctx.situation.get("own_posts") or []):
        raise Refused("that is our own post", "vote on other participants' posts")
    _outbox(ctx, {"vote": post, "value": value, "as": kw["as"]})
    return {"voted": post, "value": value}


@tool("act.skip", "End the turn without acting (nothing worth buying or posting now), with the reason.",
      {"type": "object", "required": ["why"], "properties": {"why": _SAY}})
def skip(ctx, why):
    return {"action": "skip", "why": why}


# --- records

@tool("rec.label", "Record a judgment about a participant (for example a customer type: partner, regular, first-timer, "
      "lost, black) with the evidence. A label that changes treatment waits for the user to confirm it.",
      {"type": "object", "required": ["kind", "name", "value", "evidence"],
       "properties": {"kind": {"type": "string", "enum": ["customer_type", "note"]},
                      "name": {"type": "string", "minLength": 1}, "value": {"type": "string", "minLength": 1},
                      "evidence": {"type": "string", "minLength": 5}}}, side_effect="memory/judgments")
def label(ctx, kind, name, value, evidence):
    line = {"ts": ctx.now(), "by": ctx.caller, "kind": kind, "name": name, "value": value, "evidence": evidence,
            "confirmed": False}
    atomic.append_line(paths.memory() / "judgments" / f"{ctx.caller}.jsonl", json.dumps(line, ensure_ascii=False))
    return {"recorded": True, "waits_for_user": kind == "customer_type"}
