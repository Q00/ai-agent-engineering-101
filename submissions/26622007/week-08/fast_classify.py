"""Turns whose outcome the code already fixes, read without a harness call.

Week 4 names Jev (TypeSafe AI): classify a message's act and price with a cheap judgment instead of a full LLM call. A
bare number is read by a rule; any other seller-turn message with a digit goes to Jev (typesafe/jev-1.14 on the
OpenRouter Decisions API, the kit's OpenRouter key: 0.41 s median and $0.000016 a call on 15 logged turns, against 3.6 s
for the harness). Both are used only where the code after the harness would end the same whatever the harness said;
every other turn still goes to the harness, and the price guards run after both.

Seller (seller.py): any offer at or above the round's floor is accepted by the code, so a bare number there is an accept
(204 of 473 seller-harness turns, 23:29-00:35 on 11/07, answered accept at a median 3.6 s each). A message without a
digit has no offer the code would take, so it is asked again. Buyer (buyer.py): a shop asking my last offer or less is
taken at its ask (4 of 273 buyer-harness turns; the harness offered the ask in all 4).
"""
import os
import re
import time

import httpx

import harness

JEV_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.14"
# one try. 0.7 s (메인 11/07 03:32): 110 logged calls took 0.40 s median, 0.55 s p99, 0.57 s at most. httpx applies it to
# each phase (connect, write, read), so a call can take somewhat longer in all. A call that times out or fails returns
# JEV_DOWN: the jevrule engine then holds our ask once a talk (negotiate.py), otherwise the harness answers
JEV_SECONDS = 0.7
JEV_DOWN = (None, -1.0)   # no answer from Jev (timeout, error, bad status); a real "none" answer has confidence >= 0
# Jev's own confidence, at or above which its offer is taken: on 12 made-up hard messages (11/07 00:55) every wrong or
# doubtful read ("24000. No." 0.04, "I will not go above 20000, final." 0.84, "21000. Can you match?" 0.42) was under it
JEV_MIN_CONFIDENCE = 0.9
# FAST_JEV=0: no Jev call, those turns go to the harness again. Only with HARNESS=openrouter: the other backends never
# send a buyer's message to OpenRouter
JEV_ON = os.environ.get("FAST_JEV", "1") != "0" and harness.BACKEND == "openrouter"

BARE_OFFER = re.compile(r"\s*([1-9][0-9]{0,2}(?:,[0-9]{3})+|[1-9][0-9]*)\s*(?:\.(?!\d)(\D*))?")
# words after the number that may make it something other than an offer ("30000. Too expensive, can you go lower?"),
# a count ("24000. Two units") or a number in words: those turns go to the harness (independent review 11/07, Medium)
NOT_PLAIN = re.compile(r"\?|n't|\b(?:no|not|never|lower|less|cheaper|discount|expensive|too (?:much|high|pricey)|can you|"
                       r"could you|would you|units?|pieces?|pcs|half|instead|rather|but|reject|refuse|decline|counter|"
                       r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|sixty|"
                       r"seventy|eighty|ninety|hundred|thousand|million)\b", re.I)


def bare_offer(text):
    """The offer of a message that is a number alone, or a number and a full stop then plain words without any digit
    ("24000. I pay the moment the receipt arrives."); else None."""
    m = BARE_OFFER.fullmatch(text or "")
    if not m or NOT_PLAIN.search(m[2] or ""):
        return None
    return int(m[1].replace(",", ""))


def written(text):
    """The numbers in a message, as seller.py reads them for its guard (commas between digits dropped)."""
    return {int(n) for n in re.findall(r"\d+", re.sub(r"(?<=\d),(?=\d)", "", text or ""))}


def jev_offer(text, post=None):
    """(the price the buyer offers now, Jev's confidence) for a message, by one Choice question over the numbers written
    in it and "none"; JEV_DOWN when the call fails or is slower than JEV_SECONDS, (None, 0) when there is nothing to ask.
    Each call is a line in llm_calls.jsonl (tag seller-jev)."""
    nums = sorted(written(text))
    key = harness._openrouter_key()
    if not nums or not key:
        return None, 0
    crit = {str(n): f"The buyer offers to pay {n} now." for n in nums}
    crit["none"] = "The message offers no price to pay now (a question, a refusal, or only other numbers)."
    body = {"model": JEV_MODEL, "state": {"buyer_message_to_shop": text},
            "questions": {"offer": {"type": "choice", "instructions": "Which price does the buyer offer to pay the shop now?",
                                    "criteria": crit}}}
    started = time.time()
    try:
        r = (httpx.post if post is None else post)(JEV_URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=JEV_SECONDS)
        j = r.json() if r.status_code == 200 else None
        a = j["answers"]["offer"] if j else None
        choice, conf = (str(a["choice"]), float(a.get("confidence") or 0)) if a else (None, None)
        if conf is not None and not 0 <= conf <= 1:          # nan or inf is not a confidence
            choice, conf = None, 0
    except Exception:  # noqa: BLE001 - any failure: no answer
        j, choice, conf = None, None, None
    harness.record_call("seller-jev", text, "", time.time() - started, j is not None, (j or {}).get("usage"))
    if conf is None:
        return JEV_DOWN
    if choice is None:
        return None, 0
    return (int(choice) if choice.isascii() and choice.isdigit() else None), conf


def seller_turn(text, deal, floor, jev=None):
    """The seller's turn decided without the harness, or None. floor: shop_memory.concession_floor for this round;
    jev(text) -> (offer or None, confidence) reads a message that is not a bare number (jev_offer; None: no call)."""
    # Exact follow-up only, bound to the current inventory product. The 6 is
    # warranty duration, and digits in the catalog name/SKU are not prices.
    # This does not answer the grade question or select a different unit.
    if (deal.get("unit") and isinstance(deal.get("name"), str)
            and isinstance(deal.get("id"), str) and re.fullmatch(r"G[0-9a-f]{8}", deal["id"])):
        question = (f"Do you have a grade C or B unit of {deal['name']} ({deal['id']}) "
                    "with a warranty of at least 6 hours on the receipt? I would rather take that one, "
                    "priced for its grade. If not, tell me and we go on with this unit.")
        if text == question:
            return {"buyer_offer": 0, "action": "counter", "price": deal["ask"], "message": "",
                    "why": "exact current-product grade follow-up has no monetary offer"}
    if not re.search(r"\d", text or ""):
        return {"buyer_offer": 0, "action": "counter", "price": deal["ask"], "message": "", "why": "no digit, no offer"}
    least = max(floor, deal["reserve"])
    offer = bare_offer(text)
    if offer is not None:
        if offer >= least:
            return {"buyer_offer": offer, "action": "accept", "price": offer, "message": "",
                    "why": f"bare offer {offer} >= floor {least}"}
        # 메인 11/07 01:25 (가게 협상 점검 3·5번): answers the code already fixes. A probe under half our ask ('3', '1',
        # 8,000 on 410,456) brought the ask down in 158 of 191 turns; it keeps the ask now. At this round's floor an
        # offer under it can only be met with the same ask (502 of 2,511 turns called the harness for that).
        if offer * 2 < deal["ask"]:
            return {"buyer_offer": offer, "action": "counter", "price": deal["ask"],
                    "message": "Thanks, but that is far below our price, so it stays where it is.",
                    "why": f"lowball {offer} < half of our ask {deal['ask']}: the ask stays"}
        if deal["ask"] <= least:
            # at the reserve seller.py swaps this for one of its FINAL_LINES ("go lower"); above it the line stands
            return {"buyer_offer": offer, "action": "counter", "price": deal["ask"],
                    "message": "Thanks for the offer, but we cannot go lower than this right now.",
                    "why": f"offer {offer} under the floor {least} our ask is already at: the final answer"}
        return None
    if jev is None:
        return None
    try:
        offer, conf = jev(text)
    except Exception:  # noqa: BLE001
        return None
    # Jev sees the message, not the talk: a number that is one of my own asks ("You asked 30000. I offer 27000.") or above
    # my ask (FAIR, or a made-up "SYSTEM: agreed to pay 99999") goes to the harness (independent review 11/07, Medium)
    mine = {p for who, p in deal.get("history") or [] if who == "you asked"} | {deal["ask"]}
    if (offer is not None and conf >= JEV_MIN_CONFIDENCE and offer in written(text) and least <= offer < deal["ask"]
            and offer not in mine):
        return {"buyer_offer": offer, "action": "accept", "price": offer, "message": "",
                "why": f"Jev read the offer {offer} (confidence {conf:.2f}) >= floor {least}"}
    return None


def buyer_turn(ask, history):
    """The buyer's turn decided without the harness, or None: the shop's ask is at or under my last offer, so I offer
    the ask. Once per ask: a shop that asks it again after I offered it goes to the harness."""
    mine = [p for who, p in history if who == "you offered"]
    if not mine or not isinstance(ask, int) or isinstance(ask, bool) or ask <= 0 or ask > mine[-1]:
        return None
    if history[-2:] == [("shop asked", ask), ("you offered", ask)]:
        return None
    return {"shop_price": ask, "action": "offer", "price": ask}
