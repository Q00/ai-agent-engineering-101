"""Two-agent price negotiation in three message formats (free / tagged / structured).

Buyer and seller are one system prompt each. The other agent's messages are
the conversation. Between them sits the protocol layer: it reads every message
into (performative, price), and that reading - not the text - decides how the
episode ends. Only the format paragraph and the reader change per condition.
"""

import json
import os
import re
import time

# ---------------------------------------------------------------- fixed settings
# Control variables. Identical in every condition and every run.

MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")
TEMPERATURE = 0.0
AGENT_MAX_TOKENS = 200
READER_MAX_TOKENS = 60
TURN_LIMIT = 10  # messages in total, buyer and seller together
PROVIDER = "openai"

CONDITIONS = ("free", "tagged", "structured")
ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")

ROLE = {
    "buyer": (
        "You are a buyer negotiating to buy a {item}. Your budget is ${limit}: you must never "
        "pay more than that. The budget is private; do not reveal it. Try to pay as little as possible."
    ),
    "seller": (
        "You are a seller negotiating to sell a {item}. Your reserve price is ${limit}: you must "
        "never sell for less than that. The reserve is private; do not reveal it. Try to sell for as "
        "much as possible."
    ),
}

RULES = (
    " You are talking to the {other}. The buyer speaks first. Every message you send performs exactly "
    "one of four acts: propose (offer a price), accept-proposal (agree to the {other}'s last proposed "
    "price; this ends the negotiation with a deal), reject-proposal (decline the {other}'s last proposal "
    "and keep negotiating), refuse (walk away; this ends the negotiation with no deal). The negotiation "
    "also ends with no deal after {limit} messages in total."
)

# the independent variable: the only paragraph of the system prompt that changes
FORMAT = {
    "free": (
        " Write your message in plain English, as you would to a person. "
        "Do not use tags, labels, or JSON."
    ),
    "tagged": (
        " Start your message with exactly one tag in parentheses: (propose), (accept-proposal), "
        "(reject-proposal), or (refuse). After the tag, write the rest in plain English. "
        "When you propose, state the price in the text."
    ),
    "structured": (
        " Reply with one JSON object and nothing else: "
        '{"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", '
        '"content": {"price": integer dollars or null}}. '
        "price is required for propose and null for the other acts."
    ),
}

KICKOFF = "(The negotiation starts now. Send your first message to the seller.)"

# free: the reader labels the act and the price
READER_SYSTEM = (
    "You read one message from a two-party price negotiation between a buyer and a seller. "
    "Label it with exactly one performative from this list: "
    "propose (the speaker offers a specific price), "
    "accept-proposal (the speaker agrees to the other party's last offered price), "
    "reject-proposal (the speaker declines the other party's last offer but keeps negotiating), "
    "refuse (the speaker ends the negotiation without a deal). "
    'Reply with one JSON object and nothing else: {"performative": "...", "price": integer or null}. '
    "price is the price the speaker offers in this message, or null if there is none."
)

# tagged: the act comes from the tag; the reader only pulls the price out of a propose
PRICE_READER_SYSTEM = (
    "You read one message from a price negotiation. The speaker is proposing a price. "
    "Reply with only the price in whole dollars that the speaker offers in this message, "
    "as digits and nothing else. If the message offers no price, reply NONE."
)


def system_prompt(role: str, condition: str, scenario: dict) -> str:
    other = "seller" if role == "buyer" else "buyer"
    limit = scenario["budget"] if role == "buyer" else scenario["reserve"]
    return (ROLE[role].format(item=scenario["item"], limit=limit)
            + RULES.format(other=other, limit=TURN_LIMIT)
            + FORMAT[condition])


# ---------------------------------------------------------------- model

_client = None


def call_model(system: str, messages: list, max_tokens: int) -> str:
    """One chat completion. Free/cheap endpoints return 429 in bursts, so rate
    limits and dropped connections are retried with a growing wait."""
    global _client
    from openai import OpenAI, RateLimitError, APIConnectionError, APITimeoutError
    if _client is None:
        _client = OpenAI()
    wait = 5
    for attempt in range(6):
        try:
            resp = _client.chat.completions.create(
                model=MODEL, temperature=TEMPERATURE, max_tokens=max_tokens,
                messages=[{"role": "system", "content": system}] + messages)
            return resp.choices[0].message.content or ""
        except (RateLimitError, APIConnectionError, APITimeoutError):
            if attempt == 5:
                raise
            time.sleep(wait)
            wait *= 2


# ---------------------------------------------------------------- protocol layer
# Each reader returns (act, price, reader_calls, note). act is None when the
# message could not be read: that is a format error.

TAG = re.compile(r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)", re.IGNORECASE)


def _int_price(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


def read_free(speaker: str, text: str):
    raw = call_model(READER_SYSTEM, [{"role": "user", "content": f"{speaker}: {text}"}],
                     READER_MAX_TOKENS)
    m = re.search(r"\{.*\}", raw, re.DOTALL)  # lenient: the reader is harness, not subject
    try:
        obj = json.loads(m.group(0))
        act = str(obj["performative"]).strip().lower()
        price = _int_price(obj.get("price"))
    except (AttributeError, ValueError, KeyError, TypeError):
        return None, None, 1, f"reader output unreadable: {raw!r}"
    if act not in ACTS:
        return None, None, 1, f"reader gave unknown act: {raw!r}"
    return act, price, 1, f"reader: {raw.strip()}"


def read_tagged(speaker: str, text: str):
    m = TAG.match(text)
    if not m:
        return None, None, 0, "no tag at the start"
    act = m.group(1).lower()
    if act != "propose":
        return act, None, 0, f"tag: {act}"
    raw = call_model(PRICE_READER_SYSTEM, [{"role": "user", "content": text}], READER_MAX_TOKENS)
    pm = re.fullmatch(r"\s*\$?\s*([\d,]+)(\.0+)?\s*", raw)
    price = int(pm.group(1).replace(",", "")) if pm else None
    return act, price, 1, f"tag: propose, price reader: {raw.strip()!r}"


def read_structured(speaker: str, text: str):
    try:  # strict on purpose: no fence stripping, the JSON must be the whole message
        obj = json.loads(text.strip())
        act = str(obj["performative"]).strip().lower()
        price = _int_price((obj.get("content") or {}).get("price"))
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return None, None, 0, f"parse error: {type(e).__name__}"
    if act not in ACTS:
        return None, None, 0, f"unknown performative {act!r}"
    return act, price, 0, "parsed"


READERS = {"free": read_free, "tagged": read_tagged, "structured": read_structured}


# ---------------------------------------------------------------- episode


def run_episode(condition: str, scenario: dict, log=print) -> dict:
    """Buyer opens, turns alternate until accept-proposal, refuse, or TURN_LIMIT.
    Returns the per-episode fields for results.csv."""
    reserve, budget = scenario["reserve"], scenario["budget"]
    prompts = {r: system_prompt(r, condition, scenario) for r in ("buyer", "seller")}
    history = {"buyer": [{"role": "user", "content": KICKOFF}], "seller": []}
    last_offer = {"buyer": None, "seller": None}  # last readable propose price per side
    format_errors = reader_calls = 0
    outcome, price, note = "open", None, ""

    log(f"\n=== {condition} {scenario['id']} {scenario['item']} "
        f"reserve={reserve} budget={budget} deal_possible={int(reserve <= budget)} ===")
    for r in ("buyer", "seller"):
        log(f"SYSTEM {r}: {prompts[r]}")

    turns = 0
    speaker, other = "buyer", "seller"
    while turns < TURN_LIMIT:
        text = call_model(prompts[speaker], history[speaker], AGENT_MAX_TOKENS)
        turns += 1
        history[speaker].append({"role": "assistant", "content": text})
        history[other].append({"role": "user", "content": text})
        log(f"\n[{turns}] {speaker.upper()}: {text}")

        act, p, calls, how = READERS[condition](speaker, text)
        reader_calls += calls
        if act is None:
            format_errors += 1
            log(f"    READ -> FORMAT-ERROR ({how})")
        elif act == "propose" and p is None:
            format_errors += 1
            log(f"    READ -> FORMAT-ERROR (propose without a readable price; {how})")
        elif act == "accept-proposal" and last_offer[other] is None:
            format_errors += 1
            log(f"    READ -> FORMAT-ERROR (accept-proposal with no {other} proposal to accept; {how})")
        else:
            log(f"    READ -> {act}" + (f" price={p}" if p is not None else "") + f" ({how})")
            if act == "propose":
                last_offer[speaker] = p
            elif act == "accept-proposal":
                outcome, price = "deal", last_offer[other]
                if p is not None and p != price:
                    note = f"accept names {p}, last {other} proposal was {price}"
                break
            elif act == "refuse":
                outcome = "no_deal"
                note = f"{speaker} refused at turn {turns}"
                break
        speaker, other = other, speaker

    deal_possible = int(reserve <= budget)
    violation = int(outcome == "deal" and (price < reserve or price > budget))
    if deal_possible:
        correct = int(outcome == "deal" and reserve <= price <= budget)
    else:
        correct = int(outcome != "deal")
    result = dict(deal_possible=deal_possible, outcome=outcome, price=price, correct=correct,
                  violation=violation, turns=turns, format_errors=format_errors,
                  reader_calls=reader_calls, note=note)
    log(f"EPISODE {scenario['id']}: {result}")
    return result
