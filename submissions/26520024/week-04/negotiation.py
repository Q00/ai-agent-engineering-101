"""Shared negotiation state machine; only message reading differs by condition."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
CONDITIONS = ("free", "tagged", "structured")
ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")
MAX_TURNS = 8
HEADER = ("run", "condition", "scenario", "deal_possible", "outcome", "price", "correct",
          "violation", "turns", "format_errors", "reader_calls", "note")
TAG = re.compile(r"^\((propose|accept-proposal|reject-proposal|refuse)\)\s+(.+)$", re.DOTALL)
ANY_TAG = re.compile(r"\((?:propose|accept-proposal|reject-proposal|refuse)\)")


def load_json(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key: " + key)
        result[key] = value
    return result


def bad_constant(value):
    raise ValueError("nonfinite number: " + value)


def strict_json(raw):
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=bad_constant)


def valid_price(price):
    return type(price) is int and price >= 0


def parse_object(raw, structured=False, price_only=False):
    try:
        obj = strict_json(raw)
        keys = {"performative", "content" if structured else "price"}
        if not isinstance(obj, dict) or set(obj) != keys:
            raise ValueError("unexpected message keys")
        act = obj["performative"]
        if structured:
            content = obj["content"]
            if not isinstance(content, dict) or set(content) != {"price"}:
                raise ValueError("content must contain exactly price")
            price = content["price"]
        else:
            price = obj["price"]
        if price_only:
            if not valid_price(price):
                raise ValueError("proposal needs a nonnegative integer price")
            return "propose", price, None
        if act not in ACTS:
            raise ValueError("unknown performative")
        if price is not None and not valid_price(price):
            raise ValueError("price must be null or a nonnegative integer")
        if act == "propose" and price is None:
            raise ValueError("proposal missing price")
        return act, price, None
    except (ValueError, TypeError) as exc:
        return None, None, str(exc)


def role_system(role, scenario, condition, prompts):
    if role not in ("buyer", "seller") or condition not in CONDITIONS:
        raise ValueError("invalid role or condition")
    limit = scenario["budget" if role == "buyer" else "reserve"]
    return (prompts["roles"][role].format(item=scenario["item"], limit=limit)
            + prompts["common"] + prompts["formats"][condition])


def reader_history(transcript):
    return [{"role": "user", "content": json.dumps({"transcript": transcript}, ensure_ascii=True)}]


def read_message(condition, text, transcript, prompts, model, emit):
    if condition == "structured":
        return parse_object(text, structured=True)
    if condition == "tagged":
        match = TAG.fullmatch(text.strip())
        if match is None or len(ANY_TAG.findall(text)) != 1:
            return None, None, "expected exactly one leading performative tag and text"
        act = match.group(1)
        if act != "propose":
            return act, None, None
    elif condition != "free":
        raise ValueError("unknown condition")
    raw = model(prompts["reader"], reader_history(transcript), "reader")
    result = parse_object(raw, price_only=condition == "tagged")
    emit("reader_label", raw=raw, performative=result[0], price=result[1], error=result[2])
    return result


def initial_state():
    return dict(outcome="open", price=None, last_price={"buyer": None, "seller": None})


def advance(state, role, act, price):
    other = "seller" if role == "buyer" else "buyer"
    if act == "propose":
        state["last_price"][role] = price
    elif act == "accept-proposal":
        if state["last_price"][other] is None:
            return "acceptance without an opponent proposal"
        state["outcome"], state["price"] = "deal", state["last_price"][other]
    elif act == "refuse":
        state["outcome"] = "no_deal"
    return None


def evaluate(scenario, state):
    possible = int(scenario["reserve"] <= scenario["budget"])
    violation = int(state["outcome"] == "deal" and not
                    scenario["reserve"] <= state["price"] <= scenario["budget"])
    correct = int((state["outcome"] == "deal" and possible and not violation)
                  or (state["outcome"] == "no_deal" and not possible))
    return dict(deal_possible=possible, outcome=state["outcome"],
                price=state["price"] if state["price"] is not None else "",
                correct=correct, violation=violation)


def negotiate(scenario, condition, prompts, model, emit):
    histories = {"buyer": [{"role": "user", "content": prompts["opening"]}], "seller": []}
    systems = {role: role_system(role, scenario, condition, prompts) for role in histories}
    transcript, state, errors = [], initial_state(), 0
    for turn in range(1, MAX_TURNS + 1):
        role, other = ("buyer", "seller") if turn % 2 else ("seller", "buyer")
        text = model(systems[role], histories[role], role)
        histories[role].append(dict(role="assistant", content=text))
        histories[other].append(dict(role="user", content=text))
        transcript.append(dict(speaker=role, text=text))
        emit("message", turn=turn, speaker=role, text=text)
        act, price, error = read_message(condition, text, transcript, prompts, model, emit)
        if error is None:
            error = advance(state, role, act, price)
        errors += int(error is not None)
        emit("parse", turn=turn, performative=act, price=price, error=error,
             state=json.loads(json.dumps(state)))
        if state["outcome"] != "open":
            break
    result = evaluate(scenario, state)
    result.update(turns=turn, format_errors=errors, reader_calls=model.stats["reader_calls"])
    return result
