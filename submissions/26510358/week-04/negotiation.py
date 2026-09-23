"""Week 04 buyer/seller experiment: only format and reader vary by condition."""

import json
import re


CONDITIONS = ("free", "tagged", "structured")
ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")
MAX_TURNS = 8

ROLE = {
    "buyer": (
        "You are the buyer of {item}. Your private maximum budget is {limit}. "
        "Never disclose your budget and never accept a price above it. "
        "Try to reach a deal at the lowest possible price within your limit."
    ),
    "seller": (
        "You are the seller of {item}. Your private minimum reserve is {limit}. "
        "Never disclose your reserve and never accept a price below it. "
        "Try to reach a deal at the highest possible price within your limit."
    ),
}
COMMON = (
    " You exchange one message per turn with the other party. Exactly four acts are "
    "available: propose (offer one integer price), accept-proposal (accept the other "
    "party's last offered price, ending in a deal), reject-proposal (decline an offer "
    "and continue), and refuse (leave permanently without a deal). "
    "Use exactly one act in each message. A proposal needs a price. "
    "Do not accept before the other party has proposed a price. "
    "Do not reveal your private limit or internal reasoning."
)
FORMAT = {
    "free": " Write one or two plain English sentences, with no tags or JSON.",
    "tagged": (
        " Start with exactly one tag, (propose), (accept-proposal), "
        "(reject-proposal), or (refuse), followed by one space and a plain English "
        "sentence. If you propose, state exactly one integer price."
    ),
    "structured": (
        ' Reply with exactly one JSON object and nothing else: '
        '{"performative":"propose|accept-proposal|reject-proposal|refuse",'
        '"content":{"price":integer-or-null}}. Use an integer only for propose; '
        "use null for all other acts. No Markdown or explanatory text."
    ),
}
READER_SYSTEM = (
    "You observe a buyer/seller price negotiation. Label only the LAST message "
    "provided, using its intended speech act rather than the previous speaker's act. "
    "Reply with exactly one JSON object and nothing else: "
    '{"performative":"propose|accept-proposal|reject-proposal|refuse",'
    '"price":integer-or-null}. Use one of the four literal act names, not the '
    "pipe-separated display above. Use an integer only for a proposed price and "
    "null otherwise. If a message has several prices, choose the speaker's offered "
    "price, not a price they reject or a private limit."
)

TAG = re.compile(r"^\((propose|accept-proposal|reject-proposal|refuse)\) ([^\n]+)$")


def system_prompt(role, item, limit, condition):
    return ROLE[role].format(item=item, limit=limit) + COMMON + FORMAT[condition]


def _price(value):
    return type(value) is int and value > 0


def _read_reader_json(raw):
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(obj, dict) or set(obj) != {"performative", "price"}:
        return None
    act, price = obj["performative"], obj["price"]
    if act not in ACTS or (act == "propose" and not _price(price)):
        return None
    if act != "propose" and price is not None:
        return None
    return act, price


def _read_structured(raw):
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(obj, dict) or set(obj) != {"performative", "content"}:
        return None
    content = obj["content"]
    if not isinstance(content, dict) or set(content) != {"price"}:
        return None
    return _read_reader_json(json.dumps({"performative": obj["performative"],
                                         "price": content["price"]}))


def read_message(condition, raw, transcript, chat, log):
    """Return (act, price), or None. A failed reading never changes protocol state."""
    if condition == "structured":
        parsed = _read_structured(raw)
        log(f"[parser] {parsed!r}")
        return parsed
    if condition == "tagged":
        match = TAG.fullmatch(raw.strip())
        if not match:
            log("[tag] invalid")
            return None
        act, body = match.groups()
        log(f"[tag] {act}")
        if act != "propose":
            return act, None
        prompt = f"Last message:\n{transcript[-1][0]}: {body}"
    else:
        prompt = "Conversation (label the LAST message only):\n" + "\n".join(
            f"{role}: {message}" for role, message in transcript
        )
    raw_label = chat.complete(READER_SYSTEM, [{"role": "user", "content": prompt}], log)
    log(f"[reader-raw] {json.dumps(raw_label, ensure_ascii=False)}")
    if condition == "tagged":
        try:
            label = json.loads(raw_label)
        except (TypeError, ValueError):
            label = None
        # The tag is authoritative. The reader supplies only the proposed price.
        parsed = ((act, label["price"]) if isinstance(label, dict) and
                  _price(label.get("price")) else None)
    else:
        parsed = _read_reader_json(raw_label)
    log(f"[reader] {parsed!r}")
    return parsed


def score(scenario, outcome, price):
    possible = int(scenario["reserve"] <= scenario["budget"])
    violation = int(outcome == "deal" and
                    not scenario["reserve"] <= price <= scenario["budget"])
    correct = int((outcome == "deal" and possible and not violation) or
                  (outcome == "no_deal" and not possible))
    return possible, correct, violation


def episode(scenario, condition, chat, log):
    """Run one eight-message negotiation, preserving every utterance in the log."""
    systems = {
        "buyer": system_prompt("buyer", scenario["item"], scenario["budget"], condition),
        "seller": system_prompt("seller", scenario["item"], scenario["reserve"], condition),
    }
    history = {"buyer": [{"role": "user", "content": "Begin with your opening message."}],
               "seller": []}
    last_price = {"buyer": None, "seller": None}
    transcript = []
    reader_calls = 0
    format_errors = 0
    outcome = "open"
    deal_price = None
    for turn in range(MAX_TURNS):
        role = "buyer" if turn % 2 == 0 else "seller"
        other = "seller" if role == "buyer" else "buyer"
        raw = chat.complete(systems[role], history[role], log)
        history[role].append({"role": "assistant", "content": raw})
        history[other].append({"role": "user", "content": raw})
        transcript.append((role, raw))
        log(f"[{role} turn={turn + 1}] {json.dumps(raw, ensure_ascii=False)}")
        before = chat.calls
        parsed = read_message(condition, raw, transcript, chat, log)
        reader_calls += chat.calls - before
        if parsed is None:
            format_errors += 1
            log("[protocol] unreadable; message still delivered")
            continue
        act, price = parsed
        if act == "propose":
            last_price[role] = price
        elif act == "accept-proposal":
            if last_price[other] is None:
                format_errors += 1
                log("[protocol] acceptance without a previous opposite proposal")
                continue
            outcome, deal_price = "deal", last_price[other]
            break
        elif act == "refuse":
            outcome = "no_deal"
            break
    possible, correct, violation = score(scenario, outcome, deal_price)
    result = dict(deal_possible=possible, outcome=outcome, price=deal_price,
                  correct=correct, violation=violation, turns=len(transcript),
                  format_errors=format_errors, reader_calls=reader_calls)
    log(f"[result] {json.dumps(result, ensure_ascii=False)}")
    return result
