import re
import json
from model import call_model

VALID_PERFORMATIVES = {"propose", "accept-proposal", "reject-proposal", "refuse"}

READER_SYSTEM = (
    "You are an observer reading a price negotiation between a buyer and a seller. "
    "Label the LAST message only. Reply with exactly one JSON object and nothing else: "
    '{"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", '
    '"price": <integer or null>}. '
    "If the last message proposes a specific price, set price to that integer. "
    "Otherwise set price to null."
)


def _extract_json(text):
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i+1])
                except json.JSONDecodeError:
                    return None
    return None


def read_structured(text):
    obj = _extract_json(text)
    if obj is None:
        return None, None, False
    perf = obj.get("performative", "")
    if perf not in VALID_PERFORMATIVES:
        return None, None, False
    price = None
    content = obj.get("content", {})
    if isinstance(content, dict):
        price = content.get("price")
    if price is not None:
        try:
            price = int(price)
        except (ValueError, TypeError):
            price = None
    return perf, price, True


def read_tagged(text, transcript, meter):
    match = re.match(r'^\s*\((\S+)\)', text)
    if not match:
        return None, None, False, 0
    tag = match.group(1).lower()
    if tag not in VALID_PERFORMATIVES:
        return None, None, False, 0
    if tag == "propose":
        price = _read_price_with_llm(transcript, meter)
        return tag, price, True, 1
    return tag, None, True, 0


def read_free(transcript, meter):
    msgs = []
    for role, text in transcript:
        msgs.append({"role": "user", "content": f"[{role}] {text}"})
    msgs.append({"role": "user", "content": "Label the last message above."})
    raw = call_model(READER_SYSTEM, msgs, meter)
    return _parse_reader_output(raw)


def _read_price_with_llm(transcript, meter):
    msgs = []
    for role, text in transcript:
        msgs.append({"role": "user", "content": f"[{role}] {text}"})
    msgs.append({"role": "user", "content": "What price does the last message propose? Reply with exactly one JSON: {\"price\": <integer or null>}."})
    raw = call_model(READER_SYSTEM, msgs, meter)
    match = re.search(r'\{[^}]*\}', raw)
    if match:
        try:
            obj = json.loads(match.group())
            p = obj.get("price")
            if p is not None:
                return int(p)
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    nums = re.findall(r'\d+', raw)
    if nums:
        return int(nums[-1])
    return None


def _parse_reader_output(raw):
    match = re.search(r'\{[^}]*\}', raw)
    if not match:
        return None, None, False, 1
    try:
        obj = json.loads(match.group())
    except json.JSONDecodeError:
        return None, None, False, 1
    perf = obj.get("performative", "")
    if perf not in VALID_PERFORMATIVES:
        return None, None, False, 1
    price = obj.get("price")
    if price is not None:
        try:
            price = int(price)
        except (ValueError, TypeError):
            price = None
    return perf, price, True, 1


def read_message(condition, text, transcript, meter):
    """Returns (performative, price, ok, reader_calls)."""
    if condition == "structured":
        perf, price, ok = read_structured(text)
        return perf, price, ok, 0
    elif condition == "tagged":
        return read_tagged(text, transcript, meter)
    else:
        perf, price, ok, calls = read_free(transcript, meter)
        return perf, price, ok, calls
