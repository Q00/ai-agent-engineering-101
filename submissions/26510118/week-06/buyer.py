"""Week 06 -- the buyer, an A2A v1.0 client whose offers come from a harness.

This is what the `a2a` CLI did by hand in the lab, written out:

    a2a card get <url>              ->  GET /.well-known/agent-card.json
    a2a send -a <url> "I want ..."  ->  POST SendMessage
    a2a send --task-id <t> 80       ->  POST SendMessage with taskId

and the two things the CLI was quietly doing for us: the `A2A-Version: 1.0`
header on every POST, and carrying the task id from one message to the next.
Without the header the server reads the request as 0.3 and answers -32009.

The budget is enforced here, not in the prompt, for the reason the seller
clamps its reserve: the trade test fails the run on a single deal above it,
and a model cannot promise that. Four guards sit around whatever the harness
answers -- nothing above the budget is ever sent, a price already within
budget is never walked away from, the clock closes the deal, and a shop that
has stopped moving is left without asking.

That last one is also why this file is faster than its first version: a
negotiation the shop cannot win is abandoned after two questions instead of
five.

Run:
    python buyer.py http://127.0.0.1:9999 lamp 120
"""
import os
import re
import sys
import time
import uuid

import httpx

import harness
import prices

# The defaults are the trade test's numbers and nothing reads the environment
# during it. Trading with a classmate's shop is a different setting: their
# harness is as slow as mine, their machine is across a tunnel, and the 300 s
# cap that shapes these values does not apply. BUYER_HTTP_TIMEOUT,
# BUYER_DEADLINE and BUYER_ROUNDS relax them for that case without touching
# what the test runs.
MAX_ROUNDS = int(os.environ.get("BUYER_ROUNDS", "4"))
TIMEOUT = int(os.environ.get("BUYER_HTTP_TIMEOUT", "60"))

# The trade test kills a buyer that is still running after 300 s and fails the
# run. Four harness calls at the 50 s timeout is 200 s, which fits, but that is
# an estimate and the rest of this submission puts guarantees in code. Past
# this many seconds the buyer stops asking the harness and finishes on rules,
# so the deadline cannot be missed however slow the service gets.
DEADLINE = int(os.environ.get("BUYER_DEADLINE", "200"))
_START = time.time()

# Fallback ladder, used when the harness answers with nothing usable: walk up
# fractions of the budget, ending at the budget itself.
LADDER = (0.65, 0.80, 0.90, 1.00)

PROMPT = """You are buying one {item}.
Your secret maximum is {budget}. Never reveal it and never go above it.
{history}The shop is now asking {ask}.
This is round {rounds} of {max_rounds}; after that you leave with nothing.
Open low enough to leave yourself room, but do not lose the item over a few units.

Reply with ONE line and nothing else:
  ACCEPT          pay the shop's asking price
  WALK            leave without buying
  a whole number  your counteroffer"""


def log(*parts):
    print(" ".join(str(p) for p in parts), flush=True)


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


def choose(item: str, budget: int, ask, rounds: int, hist, stalled: bool):
    """-> ('accept', price) | ('offer', price) | ('walk', None), guards applied.

    The three cases above the harness call are facts, not judgements, so they
    cost nothing to decide.
    """
    within = ask is not None and ask <= budget
    if within and rounds >= MAX_ROUNDS:
        log(f"[buyer] last round and {ask} fits {budget}; taking it")
        return "accept", ask
    if not within and stalled:
        log(f"[buyer] {ask} is over {budget} and not moving; leaving")
        return "walk", None
    if not within and rounds >= MAX_ROUNDS:
        # Last round, still above budget, but the shop is still coming down.
        # Leaving now wastes the round: an offer at the limit costs nothing
        # and the other side may be on its own last round too. A sofa was
        # lost exactly here -- the shop was asking 710 against a reserve of
        # 605 and would have taken 700 (logs/challenge-1/request-2.log).
        log(f"[buyer] last round and {ask} is over {budget}; "
            f"putting the full {budget} on the table")
        return "offer", budget

    rule = int(budget * LADDER[min(rounds - 1, len(LADDER) - 1)])
    spent = time.time() - _START
    if spent > DEADLINE:
        log(f"[buyer] {spent:.0f}s spent of the test's 300s; finishing on rules")
        return ("offer", min(rule, budget)) if not within else ("accept", ask)

    history = "".join(f"They asked {a}, you offered {o}. " for a, o in hist[-3:])
    out = harness.ask(PROMPT.format(item=item, budget=budget, ask=ask, rounds=rounds,
                                    max_rounds=MAX_ROUNDS,
                                    history=(history + "\n") if history else ""))
    d = _answer(out, rule)
    log(f"[buyer] ask {ask} budget {budget} round {rounds}: harness {out!r} -> {d}")

    if within:
        if d == "WALK":
            # Leaving a price we can afford is not a judgement call, it is a
            # mistake: the run needs exactly one deal.
            log(f"[buyer] harness said WALK on {ask} <= {budget}; taking it")
            d = "ACCEPT"
        if d == "ACCEPT":
            return "accept", ask
        return "offer", min(d if isinstance(d, int) else rule, budget)

    if d == "ACCEPT":                       # above budget: not on the menu
        log(f"[buyer] harness said ACCEPT on {ask} > {budget}; not available")
        d = rule
    if d == "WALK":
        return "walk", None
    return "offer", min(d if isinstance(d, int) else rule, budget)


class Client:
    def __init__(self, base: str):
        self.http = httpx.Client(timeout=TIMEOUT)
        card = self.http.get(base.rstrip("/") + "/.well-known/agent-card.json").json()
        ifaces = card.get("supportedInterfaces") or [{}]
        # Take the first interface this client can actually speak rather than
        # simply the first one listed. A shop that offers HTTP+JSON only -- the
        # lab's echo server does -- would otherwise be sent JSON-RPC bodies and
        # answer nothing useful, which is a confusing way to find out.
        iface = next((i for i in ifaces if i.get("protocolBinding") == "JSONRPC"), None)
        if iface is None:
            iface = ifaces[0]
            log(f"[buyer] this shop offers "
                f"{[i.get('protocolBinding') for i in ifaces]} and not JSONRPC; "
                f"trying {iface.get('protocolBinding')} anyway")
        self.url = iface.get("url") or base
        log(f"[buyer] card: {card.get('name')!r} via {iface.get('protocolBinding')} {self.url}")
        log(f"[buyer] skills: {[s.get('id') for s in card.get('skills') or []]}")

    def rpc(self, method: str, params: dict):
        body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
        # The header is the whole reason this client talks to a 1.0 server at
        # all. Every POST carries it, including CancelTask.
        r = self.http.post(self.url, json=body, headers={"A2A-Version": "1.0"}).json()
        if "error" in r:
            log(f"[buyer] <- error {r['error'].get('code')} "
                f"{str(r['error'].get('message'))[:120]}")
            return None
        return r["result"].get("task", r["result"])

    def send(self, text: str, task_id: str | None = None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        log(f"[buyer] -> {text!r}" + (f" taskId={task_id[:8]}" if task_id else ""))
        t = self.rpc("SendMessage", {"message": msg})
        if t:
            log(f"[buyer] <- {state(t)} | {message_text(t)[:120]}")
        return t

    def cancel(self, task_id: str):
        try:
            self.rpc("CancelTask", {"name": f"tasks/{task_id}", "id": task_id})
        except Exception as e:                                   # noqa: BLE001
            log(f"[buyer] cancel failed, stopping anyway: {e}")


def state(t) -> str:
    return (t or {}).get("status", {}).get("state", "")


def message_text(t) -> str:
    parts = (t or {}).get("status", {}).get("message", {}).get("parts", [])
    return " ".join(p.get("text", "") for p in parts)


def last_number(t):
    """The shop's ask.

    Three sources, in order of how much they can be trusted. `metadata.ask`
    is the machine-readable one challenge 3 introduces and the only one that
    survives a chatty shop. The sentence is the fallback for a shop that does
    not set it -- the kit seller in the trade test does not.

    When both exist and disagree, the higher one wins. A shop whose text says
    300 while its metadata says 50 is either broken or baiting; paying the
    lower figure means an offer that gets refused, and believing the cheaper
    channel is exactly what a hostile shop would want.
    """
    meta = (t or {}).get("status", {}).get("message", {}).get("metadata") or {}
    tagged = prices.digits(str(int(meta["ask"]))) if isinstance(
        meta.get("ask"), (int, float)) and meta["ask"] > 0 else None

    text = message_text(t)
    nums = prices.numbers(text)
    if tagged is None and len(nums) > 1:
        # No metadata and more than one number: the same reading problem the
        # shop has with a chatty buyer, from the other end. Taking the last
        # number is what challenge 3 showed to be wrong.
        spoken = prices.read_offer(text, harness.ask)
    else:
        spoken = nums[-1] if nums else None

    if tagged is not None and spoken is not None and tagged != spoken:
        log(f"[buyer] shop says {spoken} in words and {tagged} in metadata; "
            f"taking {max(tagged, spoken)}")
        return max(tagged, spoken)
    return tagged if tagged is not None else spoken


def receipt_price(t):
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            d = p.get("data")
            if isinstance(d, dict) and isinstance(d.get("price"), (int, float)):
                return d["price"]
    return None


def settled(t, offered: int, item: str | None = None):
    """What the shop actually charged, once its receipt has been checked.

    The remote agent is opaque: its receipt is a claim, not a fact. Returns
    (price, problem) -- see prices.check_receipt.
    """
    return prices.check_receipt((t or {}).get("artifacts"), offered, item)


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    base, item, budget = sys.argv[1], sys.argv[2], int(sys.argv[3])
    log(f"[buyer] shopping for {item}, budget {budget}, at {base}")

    c = Client(base)
    t = c.send(f"I want to buy a {item}")
    if t is None or state(t) != "TASK_STATE_INPUT_REQUIRED":
        log(f"[buyer] the shop did not open a negotiation ({state(t) or 'error'}); stopping")
        return 0
    task_id = t["id"]

    hist, prev_ask, last_offer = [], None, None
    for rounds in range(1, MAX_ROUNDS + 1):
        ask = last_number(t)
        # A shop whose ask has stopped falling is at its floor. Asking the
        # harness to haggle with a wall costs a call and buys nothing.
        stalled = (ask is not None and prev_ask is not None
                   and ask > budget and ask >= prev_ask)
        what, price = choose(item, budget, ask, rounds, hist, stalled)
        if what == "walk":
            break
        if price > budget:                  # the guarantee, in code
            log(f"[buyer] refusing to send {price} over budget {budget}; walking away")
            break
        hist.append((ask, price))
        prev_ask, last_offer = ask, price
        t = c.send(str(price), task_id)
        if t is None or state(t) != "TASK_STATE_INPUT_REQUIRED":
            break

    if state(t) == "TASK_STATE_COMPLETED":
        price, problem = settled(t, last_offer if last_offer is not None else budget, item)
        if problem:
            log(f"[buyer] the shop closed but its receipt is wrong: {problem}")
        else:
            log(f"[buyer] bought {item} at {price}")
    else:
        log(f"[buyer] no deal ({state(t) or 'error'})")
        if state(t) == "TASK_STATE_INPUT_REQUIRED":
            c.cancel(task_id)
    log(harness.stats())
    return 0


if __name__ == "__main__":
    sys.exit(main())
