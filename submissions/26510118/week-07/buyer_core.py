"""Week 07 -- the buyer's decisions, written once and run in two places.

market_buyer.py and buyer.py run them on the PC with Claude Code as the harness
(the market test and the trade test check that). worker/entry.py runs the same
module on Cloudflare, driven by a cron trigger instead of the kit. Nothing here
knows which: the harness (`ask`), the A2A client and the memory's storage are
handed in.

The budget is enforced here, not in the prompt, for the reason the seller
clamps its reserve: a model cannot promise it. Four guards sit around whatever
the harness answers -- nothing above the budget is ever sent, a price already
within budget is never walked away from, the clock closes the deal, and a shop
that has stopped moving is left without asking. One exception to the second:
a shop that twice in a row will not come down from a price above the market
floor (3% over it still counts as the floor) is refusing to negotiate, so the
buyer leaves, reviews it 2 stars and avoids it after.

Choosing a shop (from reviews and memory):
    code      drops shops that are clearly bad -- verified-looking bad
              reviews, or ones my own memory caught issuing a wrong receipt or
              breaking off -- whenever a safer shop exists. This is what the
              review test checks: the bad shop is never even contacted.
    code      scores the survivors. My own past trades (first-hand) outweigh
              strangers' reviews; a cheaper shop scores higher; a shop I have
              never tried gets a small nudge so the buyer still explores.
    harness   when two or more survive, reads their newest review and picks.

The memory is keyed by shop name, because that is all the kit guarantees, with
a fingerprint of the card (description + skills) beside it: a known name whose
fingerprint changed is a different shop, and only its wrong receipts carry over.

The review is written by the harness from the real numbers in a fixed JSON
shape, and code has the last word on the stars.
"""
import hashlib
import json
import os
import re
import time

import prices

MAX_ROUNDS = int(os.environ.get("BUYER_ROUNDS", "4"))
# The kit kills a buyer still running after 300 s. Four harness calls at the
# 50 s timeout fit, but that is an estimate; past this many seconds into a
# trade the buyer stops asking the harness and finishes on rules.
DEADLINE = int(os.environ.get("BUYER_DEADLINE", "200"))
LADDER = (0.65, 0.80, 0.90, 1.00)   # fallback offers, as fractions of the budget

BAD_STARS = 2.5       # a trust-weighted average below this, with a safer shop around, is avoided
EXPLORE = 0.4         # nudge for a shop never traded with, so a new shop can win a first customer
CROWD = 0.06          # mild penalty per review: among good shops, lean to the less-crowded (hungrier) one
SHORTLIST = 3         # how many survivors the harness chooses among
RECENT = 5            # a shop that broke off 2 of my last 5 trades with it (hung, crashed) is avoided
# The market's reserves, the same for every shop (the market test's RESERVE and
# the dashboard's minimum prices): no shop may sell below them.
FLOORS = {"lamp": 100, "chair": 180, "bookshelf": 260}
FLOOR_TOLERANCE = 1.03  # within 3% of the floor counts as the floor: that is the market's own average
                        # (102, 183, 265 on 10-23), so a shop holding there is fair, not stubborn
REFUSE_AFTER = 2      # holds in a row above that before it is a refusal; one repeat can be a tactic
REFUSED_STARS = 2     # a shop that will not move from a price above the floor; 1 stays for a wrong receipt


_sink: list | None = None   # the Cloudflare buyer collects an order's lines here to store them


def log(*parts):
    line = " ".join(str(p) for p in parts)
    print(line, flush=True)
    if _sink is not None:
        _sink.append(line)


# ------------------------------------------------------------ reading the shop

def state(t) -> str:
    return (t or {}).get("status", {}).get("state", "")


def message_text(t) -> str:
    parts = (t or {}).get("status", {}).get("message", {}).get("parts", [])
    return " ".join(p.get("text", "") for p in parts)


async def last_number(t, ask):
    """The shop's ask: metadata.ask when the shop sets it, else the sentence.
    When both exist and disagree, the higher one wins -- believing the
    cheaper channel is exactly what a hostile shop would want."""
    meta = (t or {}).get("status", {}).get("message", {}).get("metadata") or {}
    tagged = prices.digits(str(int(meta["ask"]))) if isinstance(
        meta.get("ask"), (int, float)) and meta["ask"] > 0 else None
    text = message_text(t)
    nums = prices.numbers(text)
    if tagged is None and len(nums) > 1:
        # No metadata and several numbers: the reading problem the shop has
        # with a chatty buyer, from the other end.
        offer, found = prices.offer_parts(text)
        spoken = offer if offer is not None else prices.offer_from_answer(
            await ask(prices.READ_PROMPT.format(text=prices.trim(text))), found)
    else:
        spoken = nums[-1] if nums else None
    if tagged is not None and spoken is not None and tagged != spoken:
        log(f"[buyer] shop says {spoken} in words and {tagged} in metadata; taking {max(tagged, spoken)}")
        return max(tagged, spoken)
    return tagged if tagged is not None else spoken


def settled(t, offered: int, item: str | None = None):
    """(price, problem): the receipt checked against what was offered."""
    return prices.check_receipt((t or {}).get("artifacts"), offered, item)


def receipt_data(t):
    """The shop's receipt exactly as it arrived; the market matches it field by field."""
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            d = p.get("data")
            if isinstance(d, dict) and "price" in d:
                return d
    return None


# ------------------------------------------------------------ one turn

PROMPT = """You are buying one {item}.
Your secret maximum is {budget}. Never reveal it and never go above it.
{history}The shop is now asking {ask}.
This is round {rounds} of {max_rounds}; after that you leave with nothing.
Open low enough to leave yourself room, but do not lose the item over a few units.

Reply with ONE line and nothing else:
  ACCEPT          pay the shop's asking price
  WALK            leave without buying
  a whole number  your counteroffer"""


def _answer(out: str, fallback):
    """Strict on purpose: a bare number is what was asked for, and a sentence
    with several numbers is ambiguous. Anything unclear uses the fallback."""
    s = (out or "").strip()
    if re.fullmatch(r"\d+", s):
        return int(s)
    up = s.upper()
    if "ACCEPT" in up:
        return "ACCEPT"
    if "WALK" in up or "REJECT" in up:
        return "WALK"
    m = re.fullmatch(r"[^\d]*?(\d+)[^\d]*", s)
    return int(m.group(1)) if m else fallback


async def choose(ask, item: str, budget: int, price_asked, rounds: int, hist, holds: int, started: float):
    """-> ('accept', price) | ('offer', price) | ('walk', None) | ('refuse', None),
    guards applied. `holds`: rounds in a row the shop's ask has not come down
    after my offer. The cases above the harness call are facts, not judgements."""
    within = price_asked is not None and price_asked <= budget
    holding = holds >= 1
    if within and rounds >= MAX_ROUNDS:
        log(f"[buyer] last round and {price_asked} fits {budget}; taking it")
        return "accept", price_asked
    if within and holding:
        # The shop did not come down after my offer. At the market's floor (or
        # its average, just above) there is nothing fair left to come down to
        # -- Ssadassa Shop held at 100 for a lamp, which no shop may go below
        # -- so that is simply a good price. Above it, holding twice in a row
        # is a refusal to negotiate: leave, and the review and memory say so.
        floor = FLOORS.get(item)
        if floor is not None and price_asked <= int(floor * FLOOR_TOLERANCE):
            log(f"[buyer] {price_asked} is at the market floor for a {item} and fits {budget}; taking it")
            return "accept", price_asked
        if holds >= REFUSE_AFTER:
            log(f"[buyer] {price_asked} fits {budget} but the shop held {holds} times above the floor; leaving")
            return "refuse", None
    if not within and holding:
        log(f"[buyer] {price_asked} is over {budget} and not moving; leaving")
        return "walk", None
    if not within and rounds >= MAX_ROUNDS:
        # Last round, still above budget, but the shop is still coming down:
        # an offer at the limit costs nothing (a sofa was lost exactly here in
        # week 06, at 710 against a shop that would have taken 700).
        log(f"[buyer] last round and {price_asked} is over {budget}; putting the full {budget} on the table")
        return "offer", budget

    rule = int(budget * LADDER[min(rounds - 1, len(LADDER) - 1)])
    spent = time.time() - started
    if spent > DEADLINE:
        log(f"[buyer] {spent:.0f}s spent; finishing on rules")
        return ("offer", min(rule, budget)) if not within else ("accept", price_asked)

    history = "".join(f"They asked {a}, you offered {o}. " for a, o in hist[-3:])
    out = await ask(PROMPT.format(item=item, budget=budget, ask=price_asked, rounds=rounds,
                                  max_rounds=MAX_ROUNDS, history=(history + "\n") if history else ""))
    d = _answer(out, rule)
    log(f"[buyer] ask {price_asked} budget {budget} round {rounds}: harness {out!r} -> {d}")
    if within:
        if d == "WALK":
            log(f"[buyer] harness said WALK on {price_asked} <= {budget}; taking it")
            d = "ACCEPT"
        if d == "ACCEPT":
            return "accept", price_asked
        return "offer", min(d if isinstance(d, int) else rule, budget)
    if d == "ACCEPT":                       # above budget: not on the menu
        log(f"[buyer] harness said ACCEPT on {price_asked} > {budget}; not available")
        d = rule
    if d == "WALK":
        return "walk", None
    return "offer", min(d if isinstance(d, int) else rule, budget)


# ------------------------------------------------------------ choosing a shop

def fingerprint(card: dict) -> str:
    """A short id for a shop from the parts of its card the nickname cannot
    change: its description and the ids of its skills."""
    desc = (card or {}).get("description", "")
    skills = ",".join(sorted(s.get("id", "") for s in (card or {}).get("skills") or []))
    return hashlib.sha256(f"{desc}|{skills}".encode("utf-8")).hexdigest()[:10]


def weighted_stars(reviews: list) -> float | None:
    """Average stars, a later round (Round 2 reviews are the signed ones) and a
    newer review counting more. None when there are no usable reviews."""
    num = den = 0.0
    for i, r in enumerate(reviews or []):
        s = r.get("stars")
        if not isinstance(s, (int, float)):
            continue
        w = (1 + r.get("round", 0)) * (0.9 ** i)        # round weight, recency decay (newest first)
        num += s * w
        den += w
    return (num / den) if den else None


def why_bad(ws: float | None, past: dict | None) -> str | None:
    """Why a shop is to be avoided when a safer one exists, or None. My own
    memory is checked first: a wrong receipt I saw myself outweighs any number
    of good reviews from buyers I cannot check."""
    agg = (past or {}).get("agg", {})
    if agg.get("cheated"):
        return f"my memory: {agg['cheated']} wrong receipt(s)"
    if agg.get("recent_failed", 0) >= 2:
        return f"my memory: {agg['recent_failed']} of my last {RECENT} trades there broke off"
    if agg.get("recent_refused"):
        return f"my memory: it would not negotiate above the floor ({agg['recent_refused']} of my last {RECENT})"
    if ws is not None and ws < BAD_STARS:
        return f"reviews: {ws:.1f} stars"
    return None


def score(name: str, ws: float | None, past: dict | None, mem: dict, n_reviews: int) -> float:
    s = ws if ws is not None else 3.0                   # no reviews -> neutral, not punished
    agg = (past or {}).get("agg") or {}
    if agg.get("deals"):
        # First-hand price I actually paid, as a fraction of budget: cheaper is
        # better, weighted strongly because I measured it myself.
        s += (1 - agg.get("avg_price_ratio", 1.0)) * 2.0
    if name not in mem:
        s += EXPLORE                                    # never tried -> a small push to explore
    # When every buyer piles onto the most-reviewed shop it has no reason to
    # discount; a mild penalty for the crowd leans toward a solid but hungrier
    # shop. It never rescues a bad one -- those are already filtered out.
    s -= CROWD * n_reviews
    return s


PICK_PROMPT = """You are buying one {item} with a budget of {budget}.
Pick the shop most likely to sell it cheaply and honestly. What is known:

{rows}

Reply with ONE line: the exact name of one shop above."""


async def select(ask, shops: list, mem: dict, item: str, budget: int):
    """-> chosen shop dict. Code filters and scores; the harness breaks a tie."""
    scored = []
    for sh in shops:
        ws = weighted_stars(sh.get("reviews"))
        past = mem.get(sh["name"])
        scored.append({"shop": sh, "ws": ws, "bad": why_bad(ws, past),
                       "score": score(sh["name"], ws, past, mem, len(sh.get("reviews") or []))})
    survivors = [x for x in scored if not x["bad"]] or scored      # never leave zero options
    survivors.sort(key=lambda x: x["score"], reverse=True)
    shortlist = survivors[:SHORTLIST]
    for x in scored:
        log(f"[market_buyer] {x['shop']['name']}: stars {x['ws']}, score {x['score']:.2f}"
            + (f" [avoided: {x['bad']}]" if x["bad"] and len(survivors) < len(scored) else ""))
    if len(shortlist) == 1:
        return shortlist[0]["shop"]

    rows = []
    for x in shortlist:
        sh, past = x["shop"], mem.get(x["shop"]["name"])
        mine = ""
        if past and past.get("agg", {}).get("deals"):
            a = past["agg"]
            mine = f" | i paid ~{a['avg_price_ratio']:.0%} of budget over {a['deals']} deal(s)"
        recent = (sh.get("reviews") or [{}])[0].get("review", "")
        rows.append(f"{sh['name']} | stars {x['ws'] if x['ws'] is not None else 'none'}{mine} | recent: {recent}")
    answer = (await ask(PICK_PROMPT.format(item=item, budget=budget, rows="\n".join(rows))) or "").strip()
    for x in shortlist:                                 # the model must name one of the shortlist
        if x["shop"]["name"].lower() in answer.lower():
            log(f"[market_buyer] harness picked {x['shop']['name']}")
            return x["shop"]
    log(f"[market_buyer] harness gave {answer!r}, falling back to top score")
    return shortlist[0]["shop"]


# ------------------------------------------------------------ the trade

def anchor_from(past: dict | None, item: str, budget: int):
    """The lowest price this shop ever gave me for this item, capped at the
    budget -- what to open near. Per item: a lamp's price is no anchor for a
    bookshelf (live, the buyer once opened a bookshelf at a lamp's 100)."""
    if not past:
        return None
    lows = [t["price"] for t in past.get("trades", [])
            if t.get("deal") and t.get("price") and t.get("item") == item]
    return int(min(min(lows), budget)) if lows else None


async def trade(client, ask, url: str, item: str, budget: int, past: dict | None = None) -> dict:
    """Walk into one shop and negotiate. Returns the facts the memory and the
    review need -- also when the shop hangs or breaks off, which must cost the
    order its deal, never its result line."""
    started = time.time()
    r = {"deal": False, "receipt": None, "price": None, "rounds": 0, "honest": None, "fp": None,
         "error": None, "problem": None, "refused": False, "held_at": None, "low_ask": None}
    try:
        card = await client.open(url)
        r["fp"] = fingerprint(card)
        anchor = anchor_from(past, item, budget)    # memory anchoring: open near a remembered low
        t = await client.send(f"I want to buy a {item}")
        if t is None or state(t) != "TASK_STATE_INPUT_REQUIRED":
            log(f"[market_buyer] {url} did not open a negotiation")
            return r

        task_id, hist, prev_ask, last_offer, holds = t["id"], [], None, None, 0
        for rounds in range(1, MAX_ROUNDS + 1):
            r["rounds"] = rounds
            asked = await last_number(t, ask)
            if asked is not None:                       # the no-deal review names it
                r["low_ask"] = asked if r["low_ask"] is None else min(r["low_ask"], asked)
            holding = asked is not None and prev_ask is not None and asked >= prev_ask
            holds = holds + 1 if holding else 0
            if rounds == 1 and anchor is not None and asked is not None and asked > anchor:
                what, price = "offer", anchor       # start from what memory says it will take
                log(f"[market_buyer] opening at remembered low {anchor} (shop asked {asked})")
            else:
                what, price = await choose(ask, item, budget, asked, rounds, hist, holds, started)
            if what == "refuse":
                r["refused"], r["held_at"] = True, asked
                break
            if what == "walk" or price > budget:        # the budget guarantee, in code
                break
            hist.append((asked, price))
            prev_ask, last_offer = asked, price
            t = await client.send(str(price), task_id)
            if t is None or state(t) != "TASK_STATE_INPUT_REQUIRED":
                break

        if state(t) == "TASK_STATE_COMPLETED":
            r["deal"], r["receipt"] = True, receipt_data(t)
            r["price"], r["problem"] = settled(t, last_offer if last_offer is not None else budget, item)
            r["honest"] = r["problem"] is None
            if r["problem"]:
                log(f"[market_buyer] receipt problem: {r['problem']}")
        elif state(t) == "TASK_STATE_INPUT_REQUIRED":
            await client.cancel(task_id)
    except Exception as e:                              # noqa: BLE001  (a hung or broken shop)
        r["deal"], r["receipt"], r["error"] = False, None, type(e).__name__
        log(f"[market_buyer] trade with {url} broke off after {r['rounds']} round(s): {type(e).__name__}")
    return r


# ------------------------------------------------------------ the review

REVIEW_PROMPT = """You just finished shopping for a {item} (budget {budget}). Write a one-sentence
review for other buyers, naming the real numbers.

What happened: {facts}

market_floor is the lowest price any shop may sell this item at, so paying it is the
best price this market has. Paying the whole budget is not a good deal; rate it 3 or below.

Reply with ONE line of JSON and nothing else:
{{"stars": <whole number 1-5>, "review": "<one sentence: the price vs budget, the rounds, and whether the receipt matched>"}}"""


def derive_stars(r: dict) -> int:
    """A rule for stars when the harness answer is unusable, from the outcome alone."""
    if not r["deal"]:
        return 3                                        # no deal is not the shop's fault by itself
    if r["honest"] is False:
        return 1                                        # a wrong receipt is the worst case
    ratio = (r["price"] or 0) / max(1, r["budget"])
    return 5 if ratio <= 0.75 else 4 if ratio <= 0.9 else 3


def no_deal_review(r: dict) -> str:
    """A no-deal review in code. There is nothing to judge (the stars are fixed),
    and the harness, asked to say whether the receipt matched, wrote "the receipt
    did not match" into 623 of 844 no-deal reviews where there was no receipt."""
    item, budget, rounds, low = r["item"], r["budget"], r["rounds"], r.get("low_ask")
    if rounds == 0:
        return f"No deal on the {item} (budget {budget}): the shop did not open a negotiation."
    if low is not None and low > budget:
        return f"No deal on the {item}: the lowest ask was {low}, above my budget of {budget}, after {rounds} round(s)."
    if low is not None:
        return f"No deal on the {item} within my budget of {budget} after {rounds} round(s); the lowest ask was {low}."
    return f"No deal on the {item} within my budget of {budget} after {rounds} round(s)."


async def make_review(ask, r: dict) -> tuple[int, str]:
    if r.get("error"):                                  # the shop stopped answering: its fault, nothing to word
        return 2, (f"No deal on the {r['item']} (budget {r['budget']}): the shop stopped answering "
                   f"after {r['rounds']} round(s).")
    if r.get("refused"):                                # held above the floor: no negotiation to speak of
        return REFUSED_STARS, (f"No deal on the {r['item']}: the shop held at {r['held_at']} above the market "
                               f"floor and would not negotiate, so I left after {r['rounds']} round(s).")
    if not r["deal"]:
        return 3, no_deal_review(r)                     # no deal because the budget was below the price is not its fault
    facts = {"deal": r["deal"], "price": r["price"], "budget": r["budget"], "market_floor": FLOORS.get(r["item"]),
             "rounds": r["rounds"], "receipt_matched": r["honest"]}
    out = await ask(REVIEW_PROMPT.format(item=r["item"], budget=r["budget"], facts=json.dumps(facts)))
    stars, review = None, None
    m = re.search(r"\{.*\}", out or "", re.DOTALL)      # the model sometimes wraps the JSON in prose
    if m:
        try:
            got = json.loads(m.group())
            if isinstance(got.get("stars"), (int, float)):
                stars = int(got["stars"])
            if isinstance(got.get("review"), str) and got["review"].strip():
                review = got["review"].strip()
        except Exception:                               # noqa: BLE001
            pass
    stars = min(5, max(1, stars if stars is not None else derive_stars(r)))     # always a whole 1-5
    if r["honest"] is False:
        stars = 1            # a wrong receipt is never rewarded, whatever the model felt
    elif r["price"] is not None and r["price"] >= r["budget"]:
        stars = min(stars, 3)   # paid the full budget (the last-round max offer): a squeeze, not a good deal
    elif r["price"] is not None:
        # gpt-4o-mini gave 3 to 855 honest deals under budget, 576 of them at the
        # market floor: a good price is never rated below what the price alone earns
        stars = max(stars, derive_stars(r))
        floor = FLOORS.get(r["item"])
        if floor is not None and r["price"] <= int(floor * FLOOR_TOLERANCE):
            stars = max(stars, 4)   # the lowest price this market has
    if not review:                                      # never empty
        review = (f"Bought the {r['item']} at {r['price']} of {r['budget']} budget in {r['rounds']} rounds; "
                  + ("receipt matched." if r["honest"] else "receipt did not match."))
    return stars, review


# ------------------------------------------------------------ the memory

def aggregate(trades: list) -> dict:
    deals = [t for t in trades if t["deal"]]
    priced = [t for t in deals if t.get("price")]
    return {
        "deals": len(deals),
        "nodeal": sum(1 for t in trades if not t["deal"]),
        "avg_price_ratio": round(sum(t["price"] / max(1, t["budget"]) for t in priced) / len(priced), 3) if priced else 1.0,
        "cheated": sum(1 for t in trades if t["honest"] is False),
        "recent_failed": sum(1 for t in trades[-RECENT:] if t.get("error")),
        "recent_refused": sum(1 for t in trades[-RECENT:] if t.get("refused")),
    }


def remember(mem: dict, name: str, fp: str | None, url: str, r: dict, stars: int) -> None:
    e = mem.get(name)
    if fp is None and e:                                # the card was never read (the shop failed first): same shop
        fp = e.get("fp")
    if e and e.get("fp") is None:                       # learned from the kit log, card never seen: adopt it
        e["fp"] = fp
    if not e or e.get("fp") != fp:                      # new name, or same name different shop -> fresh entry
        # The good record is not handed to whoever holds the name now, but the
        # wrong receipts stay with the name: otherwise a shop that cheated
        # could wash its record off by rewriting one line of its card.
        kept = [t for t in (e or {}).get("trades", []) if t["honest"] is False]
        if e:
            log(f"[market_buyer] {name}'s card fingerprint changed; not carrying over old reputation"
                + (f", except {len(kept)} wrong receipt(s)" if kept else ""))
        e = {"fp": fp, "trades": kept}
    e["last_url"] = url
    e["trades"].append({"item": r["item"], "budget": r["budget"], "price": r["price"],
                        "rounds": r["rounds"], "deal": r["deal"], "honest": r["honest"], "stars": stars,
                        "error": r.get("error"), "refused": bool(r.get("refused"))})
    # Keep memory small, but never by forgetting a wrong receipt.
    e["trades"] = [t for t in e["trades"][:-20] if t["honest"] is False] + e["trades"][-20:]
    e["agg"] = aggregate(e["trades"])
    mem[name] = e


# ------------------------------------------------------------ one order

async def shop_once(ask, client, shops: list, mem: dict, item: str, budget: int) -> tuple[dict, str | None]:
    """Pick a shop, trade, review, remember. -> (the kit's result line, the
    name of the shop whose memory changed)."""
    if not shops:
        return {"shop": "", "receipt": None, "stars": 3, "review": "No shops in the market."}, None
    chosen = await select(ask, shops, mem, item, budget)
    log(f"[market_buyer] chose {chosen['name']} {chosen['url']}")
    r = await trade(client, ask, chosen["url"], item, budget, mem.get(chosen["name"]))
    r["item"], r["budget"] = item, budget
    stars, review = await make_review(ask, r)
    remember(mem, chosen["name"], r["fp"], chosen["url"], r, stars)
    return {"shop": chosen["url"], "receipt": r["receipt"], "stars": stars, "review": review}, chosen["name"]
