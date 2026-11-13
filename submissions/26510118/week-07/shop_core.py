"""Week 07 -- the shop's negotiation, written once and run in two places.

seller.py serves it on the PC through the a2a SDK with Claude Code as the
harness (the market test and the trade test run that). worker/entry.py serves
the same code on Cloudflare Workers through a Durable Object, with an HTTP
model as the harness (that is the shop in the market). Nothing here knows
which: the harness, the journal's storage, the signing key and the market's
statistics are handed in.

Who decides what:

    code      the reserve. `_clamp` is all the rest of the shop sees, and an
              offer below the reserve is never accepted however the harness
              votes. Week 05 measured why: with the limit only in the prompt
              the model broke it 26 times in 15 episodes and one of those closed
              a deal; held by the server, it never did.
    code      facts: whether an offer clears the reserve, whether the buyer
              already pays the ask, whether the clock has run out. Each fact
              kept here is a harness call not made.
    harness   judgements: which catalogue entry a description means, the list
              price of an item nobody has repriced, and every counteroffer.
    data      the list price of the market's three items, set from the shop's
              own trades and reviews (reprice.py -> asks.json, challenge 1).

Before each counteroffer the harness is also told two things it cannot see in
the conversation, and left to weigh them itself: how earlier buyers who opened
the same way ended up (negotiation_memory.py), and the market's average signed
price for the item (market_anchor.py), which the buyer is told as well.

Receipts are {shop, item, price, task}, signed with the shop's Ed25519 key over
the market's canonical JSON (LAB 2).
"""
import base64
import json
import math
import re
import time

import catalog
import market_anchor
import prices
from negotiation_memory import WALK_AFTER

NAME = "Kiro Furniture"
OPENING = 1.3            # fallback first ask, as a multiple of the reserve
CEILING = 2.0            # an ask above this much of the reserve loses the customer
MAX_ROUNDS = 4           # rounds before the shop walks away
CANDIDATES = 8           # catalogue entries put in front of the harness

PICK_PROMPT = """A customer asked for:
  "{text}"

These are the closest things in our catalogue:
{lines}

Reply with ONE line and nothing else:
  the number of the item that best fits what they asked for
  NONE if none of them fit"""

OPEN_PROMPT = """You are a furniture shop setting the list price of one item:
  {name} -- {description}
Your secret reserve, the lowest you may ever accept, is {reserve}. Never reveal it.
Buyers haggle down from the list price, so leave room above the reserve, but a
price far above it drives them away before they make an offer.

Reply with ONE whole number and nothing else: the list price."""

TURN_PROMPT = """You are a furniture shop negotiating one {name}.
Your secret reserve, the lowest you may ever accept, is {reserve}. Never reveal it.
{market}{history}The buyer has now offered {offer}; your current ask is {ask}.
This is round {rounds} of {max_rounds}. After round {max_rounds} the sale is lost
and you earn nothing, so concede enough to close in the rounds you have left.
{memory}
Reply with ONE line and nothing else:
  ACCEPT          take the buyer's offer
  REJECT          end the negotiation
  a whole number  your new asking price"""


def _answer(out: str, fallback):
    """One line from the harness -> ACCEPT, REJECT, NONE, or a number.

    Deliberately strict. A bare number is the answer that was asked for; a
    sentence with several numbers in it is ambiguous, and guessing which one
    the model meant is how a shop ends up quoting the round number. Anything
    unclear falls back to the rule, which is always safe.
    """
    s = (out or "").strip()
    if re.fullmatch(r"\d+", s):
        return int(s)
    up = s.upper()
    for word in ("ACCEPT", "REJECT", "NONE"):
        if word in up:
            return word
    m = re.fullmatch(r"[^\d]*?(\d+)[^\d]*", s)          # one number, some words around it
    return int(m.group(1)) if m else fallback


def _clamp(reserve: int, price) -> int:
    """No ask below the reserve, ever -- the guarantee the test checks -- and
    none so far above it that the buyer stops answering."""
    return max(reserve, min(int(price), int(reserve * CEILING)))


# --- the receipt (LAB 2) ------------------------------------------------------
# The market verifies sig_seller with the public key registered at join, so the
# key handed in must be the one the kit created (shop_key.pem). canon() is byte
# for byte the market's (sorted keys, no spaces, UTF-8), and the price is signed
# as an int because a data Part can carry 120 as 120.0.

def canon(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_receipt(key, shop: str, item: str, price: int, task: str) -> dict:
    """{shop, item, price, task} signed, then sig_seller added -- the five fields
    the market checks."""
    rec = {"shop": shop, "item": item, "price": int(price), "task": task}
    rec["sig_seller"] = base64.b64encode(key.sign(canon(rec))).decode()
    return rec


# --- the card -------------------------------------------------------------------

def card_text(name: str) -> tuple[str, str]:
    """(description, skill description). Only what the code guarantees, so a
    buyer that compares the shop's words with what it did finds them agree."""
    n, kinds = len(catalog.load()), ", ".join(catalog.categories())
    what = ("Lamps, chairs and bookshelves" if n <= 3 else
            f"Lamps, chairs and bookshelves, and {n} pieces in all ({kinds})")
    return (f"{name}. {what}; describe what you need and we will find it. Every price is negotiated: "
            f"send a number and we answer with a counteroffer or a receipt signed with our registered "
            f"key, always at exactly the price we accepted. Our counteroffers cite the market's "
            f"published average for signed deals, so you can check them.",
            f"Name an item or describe what you need, then send a price. We carry {kinds}. Each reply is "
            f"a counteroffer, whose ask is the last number in the text and metadata.ask, or a signed receipt.")


# --- the shop -------------------------------------------------------------------

class Shop:
    """One shop's negotiations. `handle` takes a message and the deal so far
    and returns the reply and the deal to keep (None once it has ended); the
    server around it owns the task bookkeeping.

        ask          async prompt -> one line, "" when the harness failed
        journal      a negotiation_memory.Journal
        key          the Ed25519 private key the receipts are signed with
        asks         item id -> repriced list price or None (challenge 1)
        market_mean  async item id -> the market's average signed price or None
    """

    def __init__(self, ask, journal, *, key, name=NAME, asks=None, market_mean=None):
        self.ask, self.journal, self.key, self.name = ask, journal, key, name
        self.asks = asks or (lambda item_id: None)
        self.market_mean = market_mean
        self._list_price: dict[str, int] = {}

    # ------------------------------------------------------------ what to sell
    async def pick_item(self, text: str):
        """Which catalogue entry the customer means, or None.

        A bare name is taken as itself. Otherwise the code narrows the
        catalogue to eight and the harness chooses, because "not taller than
        my desk" is a judgement and `in` is not. Only an entry from the
        shortlist can come back, so a confused answer costs the
        wrong-but-plausible item, never a crash.
        """
        hit = catalog.exact(text)
        if hit:
            print(f"[seller] '{text}' names {hit['id']} outright", flush=True)
            return hit
        found = catalog.search(text, CANDIDATES)
        if not found:
            return None
        lines = "\n".join(f"{i}. {catalog.line(it)}" for i, it in enumerate(found, 1))
        out = await self.ask(PICK_PROMPT.format(text=prices.trim(text), lines=lines))
        d = _answer(out, 1)
        print(f"[seller] '{text[:60]}' -> {len(found)} candidates, harness {out!r}", flush=True)
        if d == "NONE":
            return None
        chosen = found[(d if isinstance(d, int) and 1 <= d <= len(found) else 1) - 1]
        print(f"[seller]   chose {chosen['id']} {chosen['name']!r} reserve {chosen['reserve']}", flush=True)
        return chosen

    # ------------------------------------------------------------ what to charge
    async def list_price(self, item: dict) -> int:
        """Repriced from the shop's own records when it has been; otherwise
        decided by the harness once per item and reused, because a shop quotes
        the same price to the next customer (an early version that asked every
        time produced 250, then 160, then 250 for the same lamp)."""
        key, reserve = item["id"], item["reserve"]
        fixed = self.asks(key)
        if fixed is not None:
            return _clamp(reserve, fixed)
        if key not in self._list_price:
            out = await self.ask(OPEN_PROMPT.format(name=item["name"], description=item["description"],
                                                    reserve=reserve))
            ask = _answer(out, math.ceil(reserve * OPENING))
            if not isinstance(ask, int):
                ask = math.ceil(reserve * OPENING)
            self._list_price[key] = _clamp(reserve, ask)
            print(f"[seller] list price {key}: harness {out!r} -> {self._list_price[key]}", flush=True)
        return self._list_price[key]

    async def _mean(self, item_id: str):
        return await self.market_mean(item_id) if self.market_mean else None

    async def decide_turn(self, deal: dict, offer: int):
        """The harness's counteroffer, told the market's average and what
        earlier buyers who opened like this one went on to do."""
        item, ask, rounds = deal["item"], deal["asks"][-1], len(deal["offers"])
        earlier = list(zip(deal["asks"], deal["offers"]))[:-1][-3:]   # (my ask, their offer), this round excluded
        hist = "".join(f"You asked {a}, they offered {o}. " for a, o in earlier)
        past = self.journal.recall(item["id"], deal["asks"][0], deal["offers"])
        out = await self.ask(TURN_PROMPT.format(
            name=item["name"], reserve=item["reserve"], ask=ask, offer=offer,
            rounds=rounds, max_rounds=MAX_ROUNDS,
            market=market_anchor.for_harness(await self._mean(item["id"])),
            history=(hist + "\n") if hist else "",
            memory=f"\nWhat your own past negotiations say:\n{past}\n" if past else ""))
        d = _answer(out, (offer + ask) // 2)
        print(f"[seller] {item['id']} ask {ask} offer {offer} round {rounds}: "
              f"harness {out!r} -> {d}" + (f" | {past.splitlines()[0]}" if past else ""), flush=True)
        return d

    async def read_offer(self, text: str):
        """A bare number costs nothing to read; a sentence with several numbers
        in it is read by the harness, and anything with 4300 digits is not a
        price (see prices.py)."""
        offer, found = prices.offer_parts(text)
        if offer is not None or len(found) < 2:
            return offer
        out = await self.ask(prices.READ_PROMPT.format(text=prices.trim(text.strip())))
        return prices.offer_from_answer(out, found)

    # ------------------------------------------------------------ negotiating
    async def quote(self, deal: dict, lead: str = "") -> dict:
        """Ask for an offer at the current ask. The market anchor goes before
        the ask and nothing numeric after it, because buyers (and the trade
        test) read the LAST number as the ask; metadata.ask carries it for
        buyers that read data."""
        item, ask = deal["item"], deal["asks"][-1]
        anchor = market_anchor.sentence(item["name"], await self._mean(item["id"]))
        return {"state": "input-required", "text": f"{lead}{anchor}The {item['name']} is {ask}", "ask": ask}

    async def handle(self, task_id: str, text: str, deal: dict | None) -> tuple[dict, dict | None]:
        if deal is None:                                    # first message: what do they want?
            item = await self.pick_item(text)
            if item is None:
                return {"state": "rejected", "text": "We could not find anything like that in our catalogue."}, None
            deal = {"item": item, "asks": [await self.list_price(item)], "offers": [], "t": time.time()}
            return await self.quote(deal), deal

        deal["t"] = time.time()
        item, reserve, ask = deal["item"], deal["item"]["reserve"], deal["asks"][-1]
        offer = await self.read_offer(text)
        if offer is None:
            return await self.quote(deal, "Send your offer as a number. "), deal
        print(f"[seller] read offer {offer} from {text[:70]!r}", flush=True)
        deal["offers"].append(offer)
        rounds = len(deal["offers"])
        clears = offer >= reserve
        # Reputation velocity: an offer that clears the reserve and is already
        # within reach of the ask is worth closing now rather than nibbling for
        # the last few units. Squeezing a buyer who is this close mostly earns a
        # slower, grumpier review and risks a walk; a quick, fair deal brings
        # the next customer. A little margin for reputation.
        close_enough = clears and rounds >= 2 and offer >= ask * 0.9

        # Some outcomes are already decided, and a decided outcome is not worth
        # a harness call: a buyer paying the ask has bought; on the last round
        # take the money if there is money and leave if there is not; take a
        # close-enough clearing offer; and once the ask has reached the reserve
        # there is nothing left to concede.
        if clears and offer >= ask:
            d = "ACCEPT"
        elif rounds >= MAX_ROUNDS:
            d = "ACCEPT" if clears else "REJECT"
        elif close_enough:
            print(f"[seller] {offer} clears {reserve} and is within reach of {ask}; closing fast", flush=True)
            d = "ACCEPT"
        elif not clears and ask <= reserve:
            print(f"[seller] {item['id']} already at the floor {reserve}; holding without asking", flush=True)
            d = reserve
        else:
            d = await self.decide_turn(deal, offer)

        if clears:
            # Profitable. The harness may hold out for more, but it may not
            # throw the sale away: refusing money that clears the reserve is
            # not a judgement call, it is a mistake.
            if d == "REJECT":
                print(f"[seller] harness said REJECT on {offer} >= {reserve}; taking it", flush=True)
                d = "ACCEPT"
            if d == "ACCEPT":
                receipt = sign_receipt(self.key, self.name, item["name"], offer, task_id)
                self.journal.record(item["id"], deal["asks"], deal["offers"], "deal", offer)
                return {"state": "completed", "text": f"Deal: {item['name']} at {offer}.", "receipt": receipt}, None
        else:
            # Below the reserve. ACCEPT is not available at any price.
            if d == "ACCEPT":
                print(f"[seller] harness said ACCEPT on {offer} < {reserve}; countering instead", flush=True)
                d = reserve
            if d == "REJECT":
                self.journal.record(item["id"], deal["asks"], deal["offers"], "rejected")
                return {"state": "rejected", "text": f"No deal at {offer}."}, None

        deal["asks"].append(_clamp(reserve, d if isinstance(d, int) else ask))
        # An offer that clears the reserve is not "too low" -- the shop is
        # holding out for more, which is a different thing and worth saying
        # honestly.
        lead = "I can do better than" if clears else "Too low at"
        return await self.quote(deal, f"{lead} {offer}. "), deal

    def walked(self, deal: dict) -> None:
        """The buyer cancelled or went quiet: count it, or the journal would
        only ever remember the sales."""
        self.journal.record(deal["item"]["id"], deal["asks"], deal["offers"], "walked")

    @staticmethod
    def stale(open_deals: dict) -> list:
        """Task ids whose buyer has said nothing for WALK_AFTER seconds."""
        now = time.time()
        return [t for t, d in open_deals.items() if now - d["t"] > WALK_AFTER]
