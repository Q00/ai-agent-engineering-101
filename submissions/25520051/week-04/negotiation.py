"""Week 04 — buyer/seller negotiation protocol in three message formats.

Four acts, from the FIPA Communicative Act Library: propose (offer a
price), accept-proposal (agree to the other side's last price, ends with a
deal), reject-proposal (decline and keep going), refuse (leave, no deal).
The buyer opens; the episode also ends at a fixed turn limit ("open").

Only two things change per condition: the format paragraph appended to each
agent's system prompt, and how the harness reads a message back into
(performative, price):

  free        plain English -> an LLM reader labels every message
  tagged      "(tag) plain English" -> regex for the tag; the LLM reader
              only for the price inside a propose
  structured  one JSON object -> a parser, no model call

An unparseable message (reader/regex/JSON failure) is counted as a format
error and treated as a no-op reject-proposal so the episode keeps running
instead of crashing.
"""
import json
import re

from llm_chat import Chat, Meter

CONDITIONS = ("free", "tagged", "structured")
ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")
MAX_MESSAGES = 8  # 4 turns each side

BUYER_ROLE = """You are the Buyer in a two-party price negotiation over a {item}.
Your private maximum budget is {limit}. You must never agree to pay more than this amount, and you must never state this number to the Seller.
You want to pay as little as possible, but you would rather reach a deal within your budget than walk away with nothing.
You are negotiating with a Seller who has their own hidden minimum price that you do not know.
{format_paragraph}
Send exactly one message per turn, doing exactly one of the four things above. Include no text outside what the required format allows, and never explain your reasoning to the other party."""

SELLER_ROLE = """You are the Seller in a two-party price negotiation over a {item}.
Your private minimum reserve price is {limit}. You must never agree to accept less than this amount, and you must never state this number to the Buyer.
You want to sell for as much as possible, but you would rather reach a deal above your reserve than walk away with nothing.
You are negotiating with a Buyer who has their own hidden maximum budget that you do not know.
{format_paragraph}
Send exactly one message per turn, doing exactly one of the four things above. Include no text outside what the required format allows, and never explain your reasoning to the other party."""

FORMAT_PARAGRAPHS = {
    "free": (
        "Write your message in plain English, with no tags or labels of any kind. "
        "Every message must clearly do exactly one of these four things: propose a "
        "specific numeric price, accept the other party's most recently proposed "
        "price (this ends the negotiation with a deal), reject the other party's "
        "most recently proposed price while staying in the negotiation, or refuse "
        "and walk away from the negotiation entirely (this ends the negotiation "
        "with no deal)."
    ),
    "tagged": (
        "Start every message with exactly one tag in parentheses, then a space, "
        "then plain English: \"(propose)\" to offer a specific numeric price, "
        "\"(accept-proposal)\" to agree to the other party's most recently "
        "proposed price (this ends the negotiation with a deal), "
        "\"(reject-proposal)\" to decline the other party's most recently "
        "proposed price while staying in the negotiation, or \"(refuse)\" to walk "
        "away from the negotiation entirely (this ends the negotiation with no "
        "deal). Example: \"(propose) I can offer 180 for it.\""
    ),
    "structured": (
        "Respond with ONLY one JSON object and nothing else -- no prose, no "
        "markdown code fences. It must match exactly this shape: "
        "{\"performative\": \"propose\" | \"accept-proposal\" | \"reject-proposal\" "
        "| \"refuse\", \"content\": {\"price\": <integer or null>}}. Use a null "
        "price for accept-proposal (it implicitly means the other party's last "
        "stated price), reject-proposal, and refuse; use an integer price only "
        "for propose."
    ),
}

KICKOFF_BUYER = "Begin the negotiation now with your opening message."

READER_SYSTEM = (
    "You read one message taken from a two-party price negotiation and label "
    "its speech act. The next user turn IS that message, verbatim, in full -- "
    "it may be as short as a single bare number (e.g. \"160\" means a proposed "
    "price of 160) or a short phrase; never ask for clarification or for more "
    "context, and never treat the user turn as anything other than the "
    "complete message to label. Reply with ONLY a JSON object, nothing else: "
    "{\"performative\": one of \"propose\", \"accept-proposal\", "
    "\"reject-proposal\", \"refuse\", \"price\": an integer if the message "
    "states or clearly implies a specific numeric price, else null}. You must "
    "pick exactly one performative from that list even if the message is a "
    "question or does not fit neatly -- choose the closest match."
)

PRICE_READER_SYSTEM = (
    "Extract the single numeric price mentioned in this negotiation message. "
    "The next user turn IS that message, verbatim, in full -- it may be as "
    "short as a single bare number; never ask for clarification. Reply with "
    "ONLY JSON: {\"price\": an integer, or null if no price is stated}."
)

_TAG_RE = re.compile(r"^\(([a-z][a-z-]*)\)\s*(.*)$", re.DOTALL)
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"```\s*$", "", text)
    return text.strip()


def _parse_json_obj(text: str):
    match = _JSON_RE.search(_strip_fences(text))
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _coerce_int(value):
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def read_free(text: str, reader_meter: Meter):
    """LLM reader labels every message: performative + price if present."""
    chat = Chat(system=READER_SYSTEM, meter=reader_meter)
    chat.add_user(text)
    data = _parse_json_obj(chat.send().text)
    if data is None or data.get("performative") not in ACTS:
        return None
    return {"act": data["performative"], "price": _coerce_int(data.get("price"))}


def read_tagged(text: str, reader_meter: Meter):
    """Regex for the tag; the LLM reader only for the price inside a propose."""
    match = _TAG_RE.match((text or "").strip())
    if not match or match.group(1) not in ACTS:
        return None
    act = match.group(1)
    if act != "propose":
        return {"act": act, "price": None}
    chat = Chat(system=PRICE_READER_SYSTEM, meter=reader_meter)
    chat.add_user(match.group(2).strip())
    data = _parse_json_obj(chat.send().text)
    price = _coerce_int(data.get("price")) if data else None
    if price is None:
        return None  # a propose with no extractable price is a format error
    return {"act": act, "price": price}


def read_structured(text: str, reader_meter: Meter):
    """A parser, no model call."""
    data = _parse_json_obj(text)
    if data is None or data.get("performative") not in ACTS:
        return None
    content = data.get("content")
    price = _coerce_int(content.get("price")) if isinstance(content, dict) else None
    return {"act": data["performative"], "price": price}


READERS = {"free": read_free, "tagged": read_tagged, "structured": read_structured}


def run_episode(scenario: dict, condition: str, log) -> dict:
    """Run one buyer/seller episode. Returns the metrics for one results.csv row."""
    item, reserve, budget = scenario["item"], scenario["reserve"], scenario["budget"]
    fmt = FORMAT_PARAGRAPHS[condition]
    reader = READERS[condition]

    agent_meter = Meter()
    reader_meter = Meter()
    buyer_chat = Chat(system=BUYER_ROLE.format(item=item, limit=budget, format_paragraph=fmt), meter=agent_meter)
    seller_chat = Chat(system=SELLER_ROLE.format(item=item, limit=reserve, format_paragraph=fmt), meter=agent_meter)
    buyer_chat.add_user(KICKOFF_BUYER)

    speakers = [("buyer", buyer_chat, seller_chat), ("seller", seller_chat, buyer_chat)]

    turns = 0
    format_errors = 0
    last_price = None
    last_act = None
    outcome = "open"
    deal_price = None

    for i in range(MAX_MESSAGES):
        role, speaker_chat, other_chat = speakers[i % 2]
        reply = speaker_chat.send()
        turns += 1
        log(f"[{role:6}] {reply.text}")

        parsed = reader(reply.text, reader_meter)
        if parsed is None:
            format_errors += 1
            act, price = "reject-proposal", None
            log("            [parse-fail] unreadable message; treated as reject-proposal")
        else:
            act, price = parsed["act"], parsed["price"]
            log(f"            [read] performative={act} price={price}")

        if act == "accept-proposal":
            if last_act == "propose" and last_price is not None:
                outcome, deal_price = "deal", last_price
                log(f"            [episode] DEAL at {deal_price}")
                break
            log("            [anomaly] accept-proposal with no pending price on the "
                "table; treated as reject-proposal")
            act = "reject-proposal"
        elif act == "refuse":
            outcome = "no_deal"
            log("            [episode] REFUSE -> no deal")
            break
        elif act == "propose":
            last_price = price

        last_act = act
        other_chat.add_user(reply.text)
    else:
        outcome = "open"
        log(f"            [episode] turn limit ({MAX_MESSAGES}) reached -> open")

    deal_possible = 1 if reserve <= budget else 0
    if outcome == "deal":
        in_range = deal_price is not None and reserve <= deal_price <= budget
        correct = 1 if (deal_possible and in_range) else 0
        violation = 0 if in_range else 1
    else:
        correct = 1 if not deal_possible else 0
        violation = 0

    return {
        "deal_possible": deal_possible,
        "outcome": outcome,
        "price": deal_price,
        "correct": correct,
        "violation": violation,
        "turns": turns,
        "format_errors": format_errors,
        "reader_calls": reader_meter.calls,
        "agent_calls": agent_meter.calls,
    }
