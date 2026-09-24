"""Week 04 — two-agent price negotiation in three message formats.

Buyer and seller are each one LLM with its own system prompt. The role
paragraph is identical across conditions; only the FORMAT paragraph and the
code that reads a message change (free / tagged / structured). Four FIPA acts:
propose, accept-proposal, reject-proposal, refuse. The buyer opens. An episode
ends on accept-proposal (deal), refuse (no_deal), or the turn limit (open).

Provider is picked like weeks 01-03: ANTHROPIC_API_KEY set -> Anthropic SDK,
otherwise the OpenAI-compatible API (OpenRouter). TEMPERATURE is fixed for
every call so the three conditions differ only in format.
"""
import json
import os
import re
import time

ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")
TEMPERATURE = 0.7

# ---------------------------------------------------------------- model

PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get(
    "AGENT_MODEL",
    "claude-haiku-4-5-20251001" if PROVIDER == "anthropic" else "gpt-4o-mini")
_client = None


def _get_client():
    global _client
    if _client is None:
        if PROVIDER == "anthropic":
            import anthropic
            _client = anthropic.Anthropic()
        else:
            from openai import OpenAI
            _client = OpenAI()
    return _client


class Meter:
    def __init__(self):
        self.tokens = 0
        self.calls = 0

    def add(self, input_tokens, output_tokens):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.calls += 1


def call_model(system: str, messages: list, meter: Meter, max_retries: int = 6) -> str:
    """messages: list of {"role": "user"|"assistant", "content": str}.
    Retries with a growing wait on rate limits / transient errors."""
    wait = 5
    for attempt in range(max_retries):
        try:
            if PROVIDER == "anthropic":
                resp = _get_client().messages.create(
                    model=MODEL, max_tokens=300, temperature=TEMPERATURE,
                    system=system, messages=messages)
                meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
                return "".join(b.text for b in resp.content if b.type == "text").strip()
            resp = _get_client().chat.completions.create(
                model=MODEL, temperature=TEMPERATURE, max_tokens=300,
                messages=[{"role": "system", "content": system}] + messages,
                extra_body={"reasoning": {"enabled": False}})
            u = resp.usage
            meter.add(getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0))
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            name = type(e).__name__
            if attempt == max_retries - 1 or "RateLimit" not in name and "429" not in str(e) \
                    and "Overloaded" not in name and "529" not in str(e):
                raise
            time.sleep(wait)
            wait = min(wait * 2, 120)
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------- prompts

ROLE = {
    "buyer": (
        "You are the BUYER negotiating the price of a used {item}. Your private "
        "budget is {limit}: you must never agree to pay more than {limit}, and you "
        "should try to pay clearly less. You speak first. The seller cannot see "
        "your budget. Keep every message to one or two sentences."),
    "seller": (
        "You are the SELLER negotiating the price of a used {item}. Your private "
        "reserve price is {limit}: you must never agree to sell for less than "
        "{limit}, and you should try to get clearly more. The buyer cannot see "
        "your reserve. Keep every message to one or two sentences."),
}

ACTS_TEXT = (
    "Exactly four communicative acts exist in this conversation: "
    "propose (offer a price), accept-proposal (agree to the other side's last "
    "price, which closes the deal), reject-proposal (decline and keep talking), "
    "refuse (leave the negotiation with no deal)."
)

FORMAT = {
    "free": (
        ACTS_TEXT + " Write in plain English only. Do not use tags, labels, or "
        "JSON; say what you mean in a natural sentence."),
    "tagged": (
        ACTS_TEXT + " Start every message with exactly one act tag in parentheses, "
        "then plain English. Example: (propose) I can do 120 for it. "
        "Example: (accept-proposal) Deal, 120 it is."),
    "structured": (
        ACTS_TEXT + " Reply with exactly one JSON object and nothing else, of the "
        'form {"performative": "<act>", "content": {"price": <integer or null>}}. '
        "Use a price only with propose; use null otherwise. No prose, no code fences."),
}

READER_PROMPT = (
    "You label negotiation messages. Given one message, reply with exactly one "
    'JSON object: {"performative": "<one of propose|accept-proposal|reject-proposal|refuse>", '
    '"price": <integer or null>}. price is the number offered when the act is '
    "propose, otherwise null. No prose, no code fences."
)

PRICE_READER_PROMPT = (
    "Extract the price offered in this message. Reply with exactly one JSON object: "
    '{"price": <integer or null>}. No prose.'
)


def system_prompt(role: str, condition: str, item: str, limit: int) -> str:
    return ROLE[role].format(item=item, limit=limit) + "\n\n" + FORMAT[condition]


# ---------------------------------------------------------------- protocol layer


def _loads(text: str):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _int_or_none(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def read_free(text: str, meter: Meter):
    """One reader call per message. Returns (act, price, reader_calls, ok)."""
    raw = call_model(READER_PROMPT, [{"role": "user", "content": text}], meter)
    data = _loads(raw)
    if not isinstance(data, dict) or data.get("performative") not in ACTS:
        return None, None, 1, False
    return data["performative"], _int_or_none(data.get("price")), 1, True


TAG_RX = re.compile(r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)", re.I)


def read_tagged(text: str, meter: Meter):
    """Regex for the tag; the reader only for the price inside a propose."""
    m = TAG_RX.match(text)
    if not m:
        return None, None, 0, False
    act = m.group(1).lower()
    if act != "propose":
        return act, None, 0, True
    raw = call_model(PRICE_READER_PROMPT, [{"role": "user", "content": text}], meter)
    data = _loads(raw)
    price = _int_or_none(data.get("price")) if isinstance(data, dict) else None
    return act, price, 1, True


def read_structured(text: str, meter: Meter):
    """A parser, no model call."""
    data = _loads(text)
    if not isinstance(data, dict) or data.get("performative") not in ACTS:
        return None, None, 0, False
    content = data.get("content") or {}
    price = _int_or_none(content.get("price")) if isinstance(content, dict) else None
    return data["performative"], price, 0, True


READERS = {"free": read_free, "tagged": read_tagged, "structured": read_structured}


# ---------------------------------------------------------------- episode


def run_episode(condition: str, scenario: dict, turn_limit: int = 8, log=print) -> dict:
    """One negotiation. Returns the per-episode metrics row (without run/scenario)."""
    item, reserve, budget = scenario["item"], scenario["reserve"], scenario["budget"]
    meter = Meter()
    reader = READERS[condition]

    sys_prompts = {
        "buyer": system_prompt("buyer", condition, item, budget),
        "seller": system_prompt("seller", condition, item, reserve),
    }
    # each agent sees the other's messages as "user" and its own as "assistant"
    history = {"buyer": [], "seller": []}

    outcome, price = "open", None
    last_price = {"buyer": None, "seller": None}   # last price each side proposed
    turns = format_errors = reader_calls = 0
    speaker, listener = "buyer", "seller"

    log(f"[episode] {condition} · {scenario['id']} ({item}) reserve={reserve} budget={budget}")
    for turn in range(1, turn_limit + 1):
        msgs = history[speaker] or [{"role": "user", "content": "(The negotiation begins. You speak first.)"}]
        text = call_model(sys_prompts[speaker], msgs, meter)
        turns += 1
        history[speaker].append({"role": "assistant", "content": text})
        history[listener].append({"role": "user", "content": text})

        act, p, rc, ok = reader(text, meter)
        reader_calls += rc
        log(f"  [{turn}] {speaker}: {text[:160]!r}")
        log(f"      -> read: act={act} price={p}" + ("" if ok else "  FORMAT ERROR"))

        if not ok:
            format_errors += 1
        elif act == "propose":
            if p is None:
                format_errors += 1
                log("      -> propose without a readable price: counted as format error")
            else:
                last_price[speaker] = p
        elif act == "accept-proposal":
            other = last_price[listener]
            if other is None:
                format_errors += 1
                log("      -> accept with no standing offer: counted as format error")
            else:
                outcome, price = "deal", other
                break
        elif act == "refuse":
            outcome = "no_deal"
            break
        # reject-proposal: keep going

        speaker, listener = listener, speaker

    deal_possible = int(reserve <= budget)
    if outcome == "deal":
        violation = int(price < reserve or price > budget)
        correct = int(deal_possible == 1 and violation == 0)
    else:
        violation = 0
        correct = int(deal_possible == 0 and outcome == "no_deal")

    log(f"[result] outcome={outcome} price={price} correct={correct} violation={violation} "
        f"turns={turns} format_errors={format_errors} reader_calls={reader_calls} "
        f"model_calls={meter.calls} tokens={meter.tokens}")
    return {
        "deal_possible": deal_possible, "outcome": outcome, "price": price,
        "correct": correct, "violation": violation, "turns": turns,
        "format_errors": format_errors, "reader_calls": reader_calls,
    }
