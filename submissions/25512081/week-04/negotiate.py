"""One negotiation episode: buyer vs seller, and the protocol layer that reads
each message in one of the three formats.

The protocol layer is the whole experiment: `free` needs a model reader on every
message, `tagged` reads the act with a regex and only prices a `propose` with the
reader, `structured` parses JSON with no model call. An unreadable message is
counted (`format_errors`) and still forwarded to the other agent, exactly as a
real ACL layer would pass along a malformed message.
"""
import json
import re
from dataclasses import dataclass, field

import acl
import llm

ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")
MAX_TURNS = 8
_JSON = re.compile(r"\{.*\}", re.DOTALL)
_TAG = re.compile(r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)", re.I)


def _extract_json(text):
    m = _JSON.search(text or "")
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _as_price(v):
    if v is None:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


@dataclass
class Episode:
    scenario: int
    condition: str
    deal_possible: int
    outcome: str = "open"
    price: int = None
    correct: int = 0
    violation: int = 0
    turns: int = 0
    format_errors: int = 0
    reader_calls: int = 0
    note: str = "ok"


def _reader(transcript, meter, ep):
    """Same model, same temperature: label the last message as JSON."""
    ep.reader_calls += 1
    text = llm.call_model(acl.READER_SYSTEM, [{"role": "user", "content": transcript}], meter)
    obj = _extract_json(text)
    if not isinstance(obj, dict):
        return None, None
    perf = obj.get("performative")
    return (perf if perf in ACTS else None), _as_price(obj.get("price"))


def read(condition, text, transcript, meter, ep):
    """Return (performative, price, ok). ok=False means the layer could not read
    the message (a format error)."""
    if condition == "structured":
        obj = _extract_json(text)
        if not isinstance(obj, dict) or obj.get("performative") not in ACTS:
            return None, None, False
        perf = obj["performative"]
        price = _as_price((obj.get("content") or {}).get("price"))
        if perf == "propose" and price is None:      # a propose must name a price
            return perf, None, False
        return perf, price, True

    if condition == "tagged":
        m = _TAG.match(text or "")
        if not m:
            return None, None, False
        perf = m.group(1).lower()
        price = None
        if perf == "propose":                        # price only, via the reader
            _, price = _reader(transcript, meter, ep)
        return perf, price, True

    # free: the reader labels both the act and the price from the whole dialogue
    perf, price = _reader(transcript, meter, ep)
    if perf is None:
        return None, None, False
    return perf, price, True


def run_episode(scenario, condition, meter, log=print, buyer_extra=""):
    # buyer_extra: text appended to the buyer's system prompt. Empty for the
    # required baseline; the extension uses it to make the buyer inject.
    item, reserve, budget = scenario["item"], scenario["reserve"], scenario["budget"]
    ep = Episode(scenario=scenario["id"], condition=condition,
                 deal_possible=int(reserve <= budget))
    systems = {"buyer": acl.system_prompt("buyer", item, budget, condition) + buyer_extra,
               "seller": acl.system_prompt("seller", item, reserve, condition)}
    history = {"buyer": [], "seller": []}
    last_price = {"buyer": None, "seller": None}
    transcript = []

    log(f"scenario {scenario['id']} ({item}), reserve {reserve}, budget {budget}, "
        f"deal_possible={ep.deal_possible}")
    role, other = "buyer", "seller"
    for _ in range(MAX_TURNS):
        msgs = history[role] or [{"role": "user", "content": "Please send your opening message."}]
        text = llm.call_model(systems[role], msgs, meter)
        ep.turns += 1
        history[role].append({"role": "assistant", "content": text})
        history[other].append({"role": "user", "content": text})
        transcript.append(f"[{role}] {text}")
        perf, price, ok = read(condition, text, "\n".join(transcript), meter, ep)
        log(f"  [{role}] {text.strip()}")
        log(f"    [reader/parse] performative={perf} price={price} ok={ok}")
        if not ok:
            ep.format_errors += 1                    # unreadable, but still forwarded
        elif perf == "propose":
            last_price[role] = price
        elif perf == "accept-proposal":
            ep.outcome, ep.price = "deal", last_price[other]
            break
        elif perf == "refuse":
            ep.outcome = "no_deal"
            break
        # reject-proposal: keep going
        role, other = other, role

    if ep.outcome == "deal":
        if ep.price is None:
            ep.note = "accept without a priced proposal"
            ep.violation, ep.correct = 0, 0
        else:
            ep.violation = int(ep.price < reserve or ep.price > budget)
            ep.correct = int(ep.deal_possible and not ep.violation)
    else:
        ep.violation = 0
        ep.correct = int(not ep.deal_possible)       # correct to reach no deal when impossible

    log(f"  [result] outcome={ep.outcome} price={ep.price} correct={ep.correct} "
        f"violation={ep.violation} turns={ep.turns} format_errors={ep.format_errors} "
        f"reader_calls={ep.reader_calls}")
    return ep
