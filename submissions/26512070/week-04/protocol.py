"""The protocol layer: what each condition tells the agents to write, and how
the harness reads what they wrote back into (performative, price).

Only two things differ between conditions, as the README fixes: the format
paragraph appended to both role prompts, and the reader below. Everything
else -- role prompts, scenarios, model, temperature, turn limit -- is shared.

    free        plain English         -> the LLM reader labels act and price
    tagged      "(act) plain English" -> regex for the act, LLM reader for the
                                         price of a propose or reject-proposal
    structured  one JSON object       -> a parser, no model call

A message the layer cannot read is a format error. It is counted, logged, and
the raw text is still forwarded to the other agent: the other LLM may well
understand it, and whether it does is part of what the lab measures. It just
never changes the protocol state (no deal, no refusal, no binding price).
"""
import json
import re

from llm import Meter, chat, extract_json

ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")

# ---------------------------------------------------------------------------
# The format paragraphs -- the independent variable. Rules chosen by 26512070:
#
#   * the negotiation opens with a propose, and propose is only the opener;
#   * every later message answers the other side's latest price with
#     accept-proposal, or with reject-proposal that carries its own counter
#     price in the SAME message (one performative field, as the README's JSON
#     contract has it -- the counter price rides in content.price);
#   * refuse only after three of one's own reject-and-counter messages, when
#     the other side's price is not moving by a reasonable amount.
#
# ACT_RULES is identical in all three conditions, so they differ in format
# only. The free and tagged tails also require the English itself to leave no
# doubt which act it performs.
# ---------------------------------------------------------------------------
ACT_RULES = """\
Every message you send performs exactly one of these four acts:
- propose: offer a price for the item. Only the buyer's opening message is a propose.
- accept-proposal: agree to the other side's most recent price. The negotiation ends with a deal at that price. Do not name a new price.
- reject-proposal: decline the other side's most recent price AND, in the same message, offer your own new price. A rejection always carries a counter-offer. The negotiation continues.
- refuse: leave the negotiation for good. It ends with no deal.

Order of play:
1. The buyer opens with a propose. Nothing comes before it: no greeting-only message and no question.
2. Every later message answers the other side's most recent price with either accept-proposal, or reject-proposal with your new price.
3. Only after you have sent reject-proposal with a counter-offer three times, and the other side's price is still not moving by a reasonable amount, may you refuse.
Prices are whole numbers of US dollars."""

FORMATS = {
    "free": ACT_RULES + """

Message format: write plain English only. Do not use tags, labels, brackets, or JSON, and do not name the act by its label. Your words alone must make it impossible to mistake which of the four acts you perform:
- a proposal states exactly one price of yours;
- an acceptance clearly agrees to the other side's price and names no new price;
- a rejection clearly declines the other side's price and states exactly one new price of yours;
- a refusal clearly says you are leaving without a deal.
Do not write anything that could be read as a different act: no questions, no conditional or ranged offers, and if you mention the other side's price, make clear it is theirs, not yours.""",

    "tagged": ACT_RULES + """

Message format: begin every message with exactly one tag in parentheses that names its act -- (propose), (accept-proposal), (reject-proposal) or (refuse) -- with nothing before it. After the tag, write plain English. With (propose) and (reject-proposal) the English must state your price as a number, like $150. The English must agree with the tag and must not be readable as a different act: no questions, no conditional or ranged offers, and if you mention the other side's price, make clear it is theirs, not yours.
Example: (reject-proposal) $190 is more than I will pay. I can offer $150.""",

    "structured": ACT_RULES + """

Message format: write every message as exactly one JSON object and nothing else -- no words before or after it and no code fences. Shape:
{"performative": "<act>", "content": {"price": <integer>}}
<act> is one of propose, accept-proposal, reject-proposal, refuse. For propose and reject-proposal, price is your price as a whole number. For accept-proposal and refuse, write "content": {}.
Example: {"performative": "reject-proposal", "content": {"price": 150}}""",
}

# The reader, used by `free` (act + price) and by `tagged` (price only, for
# propose and reject-proposal). Rules chosen by 26512070: a decline with a new
# price is reject-proposal carrying that price; with several numbers, the price
# is the speaker's own; anything else -- including a message that fits no act
# well -- is judged from the conversation and the speaker's role, with no
# fallback act spelled out. So the reader sees the speaker and the history,
# not the message alone (see reader_input).
READER_PROMPT = """\
You label messages from a price negotiation between a buyer and a seller. You are given who sent the message, the conversation before it, and the message itself. Reply with only one JSON object and nothing else:
{"performative": "<act>", "price": <integer or null>}

<act> is exactly one of these four:
- propose: the speaker offers a price. In this negotiation only the buyer's opening message is a propose.
- accept-proposal: the speaker agrees to the other side's most recent price. price is null.
- reject-proposal: the speaker declines the other side's most recent price and offers a new price of their own. price is that new price.
- refuse: the speaker leaves the negotiation with no deal. price is null.

Rules:
1. A message that declines the other side's price and states a new price is reject-proposal, and price is the new price: the speaker's counter-offer.
2. If the message mentions more than one number, price is the speaker's own price, never a price the other side named.
3. Judge from the conversation so far and the speaker's role (buyer or seller) which act the message actually performs and whose price each number is.
price is a whole number of US dollars, or null."""

LABEL_MARK = "Message to label"


def reader_input(text, context):
    """The reader's user turn: speaker, history, then the message.
    context = {"speaker": "buyer"|"seller", "history": [(speaker, text), ...]}"""
    context = context or {}
    speaker = context.get("speaker", "unknown")
    history = context.get("history") or []
    past = "\n".join(f"{who}: {msg}" for who, msg in history) or \
        "(none -- this is the opening message)"
    return (f"Speaker: {speaker}\n\nConversation so far:\n{past}\n\n"
            f"{LABEL_MARK} (sent by the {speaker}):\n{text}")


def ready() -> list:
    """Names of the prompts still unwritten; the runner refuses a real run
    while this is non-empty."""
    missing = [f"FORMATS[{c!r}]" for c, v in FORMATS.items() if not v]
    return missing + ([] if READER_PROMPT else ["READER_PROMPT"])


def _price(value):
    """An integer price, or None. Accepts 250, 250.0, "250", "$250"."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    if isinstance(value, str):
        m = re.search(r"\d[\d,]*(?:\.\d+)?", value)
        if m:
            return int(round(float(m.group().replace(",", ""))))
    return None


def _reply(performative=None, price=None, error=None, reader_calls=0, raw=None):
    return {"performative": performative, "price": price, "error": error,
            "reader_calls": reader_calls, "reader_raw": raw}


def _llm_read(text: str, meter: Meter, model, log, context=None):
    """One reader call. Returns (performative, price, raw_reply)."""
    raw = model(READER_PROMPT, [{"role": "user", "content": reader_input(text, context)}],
                meter, log)
    obj = extract_json(raw)
    if obj is None:
        return None, None, raw
    act = str(obj.get("performative", "")).strip().lower()
    return (act if act in ACTS else None), _price(obj.get("price")), raw


PRICED = ("propose", "reject-proposal")  # acts that must carry the speaker's price


def _check(act, price, calls, raw):
    if act is None:
        return _reply(error="no valid performative", reader_calls=calls, raw=raw)
    if act in PRICED and price is None:
        return _reply(act, error=f"{act} without a price", reader_calls=calls, raw=raw)
    return _reply(act, price if act in PRICED else None, reader_calls=calls, raw=raw)


def read_free(text, meter, model=chat, log=None, context=None):
    act, price, raw = _llm_read(text, meter, model, log, context)
    return _check(act, price, 1, raw)


TAG = re.compile(r"^\s*\(\s*(" + "|".join(ACTS) + r")\s*\)", re.I)


def read_tagged(text, meter, model=chat, log=None, context=None):
    m = TAG.match(text or "")
    if not m:
        return _reply(error="no leading (performative) tag")
    act = m.group(1).lower()
    if act not in PRICED:
        return _reply(act)
    # The tag carries the force; the price still lives in English, so the
    # reader is called for it -- and only its price is used. A reject-proposal
    # needs one too: in this protocol it always carries the counter-offer.
    _, price, raw = _llm_read(text, meter, model, log, context)
    return _check(act, price, 1, raw)


def read_structured(text, meter=None, model=None, log=None, context=None):
    stripped = (text or "").strip()
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        obj = None
    salvaged = False
    if not isinstance(obj, dict):
        obj = extract_json(stripped)
        salvaged = obj is not None
    if obj is None:
        return _reply(error="not a JSON object")
    act = str(obj.get("performative", "")).strip().lower()
    content = obj.get("content")
    price = _price(content.get("price")) if isinstance(content, dict) else None
    out = _check(act if act in ACTS else None, price, 0, None)
    if salvaged and out["error"] is None:
        # Readable, but not "one JSON object": text around it or a code fence.
        # Kept as a deal-able message, flagged so the report can count it.
        out["note"] = "json salvaged from surrounding text"
    return out


READERS = {"free": read_free, "tagged": read_tagged, "structured": read_structured}


def read(condition, text, meter, model=chat, log=None, context=None):
    return READERS[condition](text, meter, model=model, log=log, context=context)
