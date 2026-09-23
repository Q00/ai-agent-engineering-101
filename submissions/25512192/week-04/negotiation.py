"""Week 04 -- speech acts in practice: free, tagged, and structured negotiation.

A buyer and a seller, each an independent LLM call with its own system
prompt, negotiate a price over a fixed turn limit. Four communicative acts
are allowed: propose, accept-proposal, reject-proposal, refuse. The same
scenario, role prompts, model, and turn limit run under three conditions;
only the message format and how the protocol layer reads it change:

  free       -- plain English, an LLM reader labels every message
  tagged     -- "(performative) text", regex for the tag, LLM reader only
                for the price inside a propose
  structured -- one JSON object, a parser, no model call

Model call goes through the Claude Code CLI (`claude -p`), so it works
with the same subscription login this repo's agent is running under and
needs no API key. Set AGENT_MODEL to any model alias the CLI accepts
(default: haiku, per weeks/week-04/README.md). This CLI does not expose a
temperature parameter -- see REPORT.md for how that constraint is recorded.
"""
import json
import os
import re
import subprocess
import time

# ---------------------------------------------------------------- meter


class Meter:
    """Token and call counter."""

    def __init__(self):
        self.tokens = 0
        self.iters = 0

    def add(self, input_tokens: int, output_tokens: int):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.iters += 1


# ---------------------------------------------------------------- model call

CLI_BIN = os.environ.get("AGENT_CLI", "claude")
MODEL = os.environ.get("AGENT_MODEL", "haiku")
TEMPERATURE = os.environ.get("AGENT_TEMPERATURE")  # not settable via this CLI; see REPORT.md
MAX_TURNS = int(os.environ.get("AGENT_TURN_LIMIT", "8"))

DISALLOWED_TOOLS = ("Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,"
                     "Task,TodoWrite,NotebookEdit")


def call_model(system: str, user: str, meter: Meter, log=None, retries: int = 5) -> str:
    """One system+user turn via `claude -p`, no tools. Returns the raw text
    reply. Retries with a growing wait on a non-zero exit, a timeout, or
    unparseable CLI output (mirrors the OpenRouter 429-retry advice in
    weeks/week-04/README.md, generalized to any transient CLI failure)."""
    cmd = [CLI_BIN, "-p", user, "--model", MODEL,
           "--system-prompt", system, "--output-format", "json",
           "--disallowed-tools", DISALLOWED_TOOLS]
    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=120, encoding="utf-8")
        except subprocess.TimeoutExpired:
            last_err = "timeout"
        else:
            if proc.returncode == 0:
                try:
                    data = json.loads(proc.stdout)
                except json.JSONDecodeError:
                    last_err = f"bad JSON stdout: {proc.stdout[:200]!r}"
                else:
                    usage = data.get("usage", {})
                    meter.add(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
                    return data.get("result", "") or ""
            else:
                last_err = f"exit {proc.returncode}: {proc.stderr[:200]!r}"
        wait = 2 ** attempt
        if log:
            log(f"  [call_model] attempt {attempt} failed ({last_err}), retrying in {wait}s")
        time.sleep(wait)
    raise RuntimeError(f"call_model failed after {retries} attempts: {last_err}")


# ---------------------------------------------------------------- act vocabulary

ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")

ACT_VOCAB = (
    "\n\nYou may take exactly one of these four communicative acts each turn:\n"
    "- propose: offer a specific price\n"
    "- accept-proposal: agree to the other side's most recent price; this "
    "ends the negotiation with a deal at that price\n"
    "- reject-proposal: decline the other side's most recent price without "
    "ending the negotiation\n"
    "- refuse: end the negotiation with no deal\n"
    "Never reveal your private limit. Take exactly one act per turn."
)

BUYER_PROMPT = (
    "You are a buyer negotiating to purchase {item}. Your maximum budget is "
    "${budget}, which you must never reveal and never exceed. You want the "
    "lowest price you can get. You go first."
)
SELLER_PROMPT = (
    "You are a seller negotiating to sell {item}. Your reserve price is "
    "${reserve} -- you can never accept less than this, and you must never "
    "reveal this number. You want the highest price you can get."
)

FREE_FORMAT = (
    "\n\nWrite your reply in plain English, as you would speak to a person. "
    "Do not use any tag, label, or JSON -- just talk naturally. If you are "
    "proposing a price, state it clearly as a dollar amount somewhere in "
    "your message. Keep it to one or two short sentences."
)
TAGGED_FORMAT = (
    "\n\nPrefix every reply with exactly one performative tag in "
    "parentheses, chosen from: (propose), (accept-proposal), "
    "(reject-proposal), (refuse). Put the tag first, then one or two short "
    "plain-English sentences. Example: '(propose) I can offer $120 for it.'"
)
STRUCTURED_FORMAT = (
    "\n\nReply with exactly one JSON object and nothing else -- no prose, "
    "no markdown fences -- in exactly this shape:\n"
    '{"performative": "propose"|"accept-proposal"|"reject-proposal"|"refuse", '
    '"content": {"price": <integer> or null}}\n'
    "Set price to the dollar amount for propose, or to null for the other "
    "three acts."
)

FORMATS = {"free": FREE_FORMAT, "tagged": TAGGED_FORMAT, "structured": STRUCTURED_FORMAT}


def build_system(role_prompt: str, condition: str) -> str:
    return role_prompt + ACT_VOCAB + FORMATS[condition]


# ---------------------------------------------------------------- protocol readers

READER_SYSTEM = (
    "You are the protocol layer in a price negotiation between a buyer and "
    "a seller. You will be shown one message from one side. Classify its "
    "communicative act as exactly one of: propose, accept-proposal, "
    "reject-proposal, refuse. If the act is propose, also extract the "
    "numeric dollar price it offers. Reply with ONLY a JSON object, no "
    "prose, no markdown fences, in exactly this shape:\n"
    '{"performative": "propose"|"accept-proposal"|"reject-proposal"|"refuse", '
    '"price": <integer> or null}\n'
    "Use null for price unless the act is propose. If the message does not "
    "clearly fit any of the four acts (for example, it is a question), "
    "pick the closest one."
)
READER_PRICE_SYSTEM = (
    "You will be shown the text of one propose message from a price "
    "negotiation. Extract the numeric dollar price it offers. Reply with "
    "ONLY a JSON object, no prose: {\"price\": <integer> or null}."
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_TAG_RE = re.compile(r"^\s*\(([a-zA-Z-]+)\)")


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    match = _JSON_RE.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _valid_price(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def read_free(raw: str, meter: Meter, log=None) -> tuple[str | None, int | None, bool, int]:
    """Returns (performative, price, ok, reader_calls)."""
    reply = call_model(READER_SYSTEM, raw, meter, log=log)
    obj = _extract_json(reply)
    if obj is None:
        return None, None, False, 1
    performative = obj.get("performative")
    if performative not in ACTS:
        return None, None, False, 1
    price = _valid_price(obj.get("price")) if performative == "propose" else None
    if performative == "propose" and price is None:
        return performative, None, False, 1
    return performative, price, True, 1


def read_tagged(raw: str, meter: Meter, log=None) -> tuple[str | None, int | None, bool, int]:
    match = _TAG_RE.match(raw)
    if not match:
        return None, None, False, 0
    performative = match.group(1).lower()
    if performative not in ACTS:
        return None, None, False, 0
    if performative != "propose":
        return performative, None, True, 0
    reply = call_model(READER_PRICE_SYSTEM, raw, meter, log=log)
    obj = _extract_json(reply)
    price = _valid_price(obj.get("price")) if obj else None
    if price is None:
        return performative, None, False, 1
    return performative, price, True, 1


def read_structured(raw: str) -> tuple[str | None, int | None, bool, int]:
    obj = _extract_json(raw)  # tolerates markdown fences around the JSON; still no model call
    if obj is None:
        return None, None, False, 0
    performative = obj.get("performative")
    if performative not in ACTS:
        return None, None, False, 0
    if performative != "propose":
        return performative, None, True, 0
    price = _valid_price((obj.get("content") or {}).get("price"))
    if price is None:
        return performative, None, False, 0
    return performative, price, True, 0


READERS = {"free": read_free, "tagged": read_tagged, "structured": read_structured}


# ---------------------------------------------------------------- episode

def render_transcript(transcript: list[tuple[str, str]]) -> str:
    if not transcript:
        return "The negotiation is starting. Make your opening move."
    lines = [f"{speaker.capitalize()}: {text}" for speaker, text in transcript]
    return "\n".join(lines) + "\n\nIt is your turn now. Reply with your next message only."


def run_episode(scenario: dict, condition: str, meter: Meter, log) -> dict:
    """Runs one buyer/seller negotiation. Returns a dict matching the
    per-episode fields of results.csv (minus run/condition/scenario, which
    the caller already knows)."""
    buyer_system = build_system(
        BUYER_PROMPT.format(item=scenario["item"], budget=scenario["budget"]), condition)
    seller_system = build_system(
        SELLER_PROMPT.format(item=scenario["item"], reserve=scenario["reserve"]), condition)
    reader = READERS[condition]

    transcript: list[tuple[str, str]] = []
    last_proposed_price = None
    format_errors = 0
    reader_calls = 0
    turns = 0
    outcome = "open"
    price = None
    note = ""

    for turn in range(1, MAX_TURNS + 1):
        speaker = "buyer" if turn % 2 == 1 else "seller"
        system = buyer_system if speaker == "buyer" else seller_system
        user = render_transcript(transcript)
        raw = call_model(system, user, meter, log=log)
        log(f"[{turn}] {speaker}: {raw}")
        transcript.append((speaker, raw))
        turns = turn

        performative, msg_price, ok, calls = reader(raw, meter, log) if condition != "structured" \
            else reader(raw)
        reader_calls += calls

        if not ok:
            format_errors += 1
            outcome = "no_deal"
            note = f"unparseable {speaker} message at turn {turn}"
            log(f"  [protocol] FORMAT ERROR: {note}")
            break

        log(f"  [protocol] read: performative={performative} price={msg_price}")

        if performative == "propose":
            last_proposed_price = msg_price
            continue
        if performative == "reject-proposal":
            continue
        if performative == "accept-proposal":
            if last_proposed_price is None:
                outcome = "no_deal"
                note = f"{speaker} accepted with no prior proposal at turn {turn}"
                log(f"  [protocol] {note}")
            else:
                outcome = "deal"
                price = last_proposed_price
            break
        if performative == "refuse":
            outcome = "no_deal"
            break

    deal_possible = scenario["reserve"] <= scenario["budget"]
    if outcome == "deal":
        violation = price < scenario["reserve"] or price > scenario["budget"]
        correct = deal_possible and not violation
    else:
        violation = False
        correct = not deal_possible

    return {
        "outcome": outcome,
        "price": price,
        "correct": int(correct),
        "violation": int(violation),
        "turns": turns,
        "format_errors": format_errors,
        "reader_calls": reader_calls,
        "note": note,
        "deal_possible": int(deal_possible),
    }


def load_scenarios(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
