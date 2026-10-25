"""Buyer/seller negotiation and three message-format protocol readers."""
from dataclasses import dataclass
import json
import re


PERFORMATIVES = {"propose", "accept-proposal", "reject-proposal", "refuse"}
TAG_RE = re.compile(r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)\s*(.*)$", re.I | re.S)


@dataclass(frozen=True)
class ParsedMessage:
    performative: str
    price: int | None = None


def _price(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("price must be an integer")
    return value


def _make_message(performative, price=None):
    performative = str(performative).lower()
    if performative not in PERFORMATIVES:
        raise ValueError(f"unknown performative: {performative}")
    if performative == "propose":
        return ParsedMessage(performative, _price(price))
    return ParsedMessage(performative, None)


def parse_structured(raw: str) -> ParsedMessage:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError("structured message is not valid JSON") from error
    if not isinstance(value, dict) or not isinstance(value.get("content"), dict):
        raise ValueError("structured message needs an object content")
    return _make_message(value.get("performative"), value["content"].get("price"))


def parse_tagged(raw: str, price_reader) -> ParsedMessage:
    match = TAG_RE.match(raw or "")
    if not match:
        raise ValueError("missing performative tag")
    performative, body = match.groups()
    if performative.lower() == "propose":
        return _make_message(performative, price_reader(body))
    return _make_message(performative)


def parse_reader_output(raw: str) -> ParsedMessage:
    """Parse the reader's JSON, accepting either nested or flat price fields."""
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError("reader output is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("reader output must be an object")
    content = value.get("content", value)
    if not isinstance(content, dict):
        raise ValueError("reader content must be an object")
    return _make_message(value.get("performative"), content.get("price"))


def evaluate_outcome(scenario: dict, outcome: str, price: int | None) -> tuple[int, int]:
    possible = scenario["reserve"] <= scenario["budget"]
    if outcome == "deal":
        violation = int(price is None or price < scenario["reserve"] or price > scenario["budget"])
        return int(possible and not violation), violation
    return int(outcome == "no_deal" and not possible), 0


def format_instruction(condition: str) -> str:
    if condition == "free":
        return "Write one or two plain English sentences. Do not use a performative tag or JSON."
    if condition == "tagged":
        return (
            "Start every reply with exactly one tag: (propose), (accept-proposal), "
            "(reject-proposal), or (refuse). After the tag, write plain English."
        )
    if condition == "structured":
        return (
            'Reply with exactly one JSON object and nothing else: '
            '{"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", '
            '"content": {"price": whole number or null}}.'
        )
    raise ValueError(f"unknown condition: {condition}")


def role_prompt(role: str, scenario: dict, condition: str) -> str:
    private = (
        f"Your private maximum budget is {scenario['budget']}."
        if role == "buyer"
        else f"Your private minimum reserve price is {scenario['reserve']}."
    )
    return (
        f"You are the {role} in a negotiation for {scenario['item']}. {private} "
        "Never reveal your private limit. The buyer opens. Allowed acts are propose "
        "(offer a price), accept-proposal (agree to the other side's last price), "
        "reject-proposal (decline and continue), and refuse (leave with no deal). "
        "Keep proposals inside your own limit. Be concise. " + format_instruction(condition)
    )


READER_PROMPT = (
    "You are an observer reading a price negotiation between a buyer and a seller. "
    "Label the LAST message only with exactly one of propose, accept-proposal, "
    "reject-proposal, or refuse. Extract an integer price only when the speaker "
    "proposes one. Return exactly one JSON object and nothing else: "
    '{"performative":"...", "price": integer or null}.'
)


def run_episode(scenario: dict, condition: str, model, log, max_turns: int = 8):
    """Run one episode with role-specific Chat history and a protocol reader."""
    transcript = []
    last_price = {"buyer": None, "seller": None}
    format_errors = 0
    reader_calls = 0
    outcome = "open"
    price = None
    histories = {
        "buyer": [{
            "role": "user",
            "content": f"Begin the negotiation for {scenario['item']}. You open. Reply now.",
        }],
        "seller": [],
    }

    def transcript_prompt():
        conversation = "\n".join(f"{role}: {message}" for role, message in transcript)
        return [{
            "role": "user",
            "content": (
                "Conversation so far:\n"
                f"{conversation or '(none)'}\n"
                "Label the LAST message only."
            ),
        }]

    for turn in range(1, max_turns + 1):
        role = "buyer" if turn % 2 else "seller"
        other = "seller" if role == "buyer" else "buyer"
        system = role_prompt(role, scenario, condition)
        messages = histories[role]
        raw = model(system, messages)
        log({"event": "message", "turn": turn, "role": role, "raw": raw})

        # The message enters both agents' histories before protocol reading. An
        # unreadable message is still part of the conversation, as specified by
        # the lab, and the next agent gets a chance to respond to it.
        histories[role].append({"role": "assistant", "content": raw})
        histories[other].append({"role": "user", "content": raw})
        transcript.append((role, raw))

        try:
            if condition == "structured":
                parsed = parse_structured(raw)
            elif condition == "tagged":
                def read_price(text):
                    nonlocal reader_calls
                    reader_calls += 1
                    read_raw = model(
                        READER_PROMPT,
                        [{"role": "user", "content": f"Extract the price from this proposal:\n{text}"}],
                    )
                    log({"event": "reader", "text": text, "raw": read_raw})
                    result = parse_reader_output(read_raw)
                    if result.performative != "propose" or result.price is None:
                        raise ValueError("reader did not return a proposed price")
                    return result.price
                parsed = parse_tagged(raw, read_price)
            elif condition == "free":
                reader_calls += 1
                read_raw = model(READER_PROMPT, transcript_prompt())
                log({"event": "reader", "raw": read_raw})
                parsed = parse_reader_output(read_raw)
            else:
                raise ValueError(f"unknown condition: {condition}")
        except ValueError as error:
            format_errors += 1
            log({"event": "parse_error", "turn": turn, "error": str(error)})
            continue

        log({"event": "parsed", "turn": turn, **parsed.__dict__})
        if parsed.performative == "propose":
            last_price[role] = parsed.price
        elif parsed.performative == "accept-proposal":
            agreed_price = last_price[other]
            if agreed_price is None:
                format_errors += 1
                log({"event": "parse_error", "turn": turn,
                     "error": "accept without other side's prior price"})
                continue
            outcome, price = "deal", agreed_price
            break
        elif parsed.performative == "refuse":
            outcome = "no_deal"
            break

    correct, violation = evaluate_outcome(scenario, outcome, price)
    result = {
        "outcome": outcome,
        "price": price,
        "correct": correct,
        "violation": violation,
        "turns": len(transcript),
        "format_errors": format_errors,
        "reader_calls": reader_calls,
    }
    log({"event": "episode_result", **result})
    return result
