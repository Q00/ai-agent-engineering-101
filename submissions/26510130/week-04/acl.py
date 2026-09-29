"""Week 04 — the model call, the meter, and the three ways of reading a message.

The model goes through the Claude Code CLI (`claude -p`) rather than an HTTP
API, which is the route the assignment's own reference run used: a free-tier
OpenRouter key stops at 50 requests a day and one pass here needs a few hundred.
`--system-prompt` replaces Claude Code's own prompt, so the process answers as
the negotiating agent and not as a coding assistant, and every tool is denied so
it cannot go and do something instead of replying.

Provider notes that belong in the report:
  * temperature is not settable through this CLI. Recorded as such, not faked.
  * `--output-format json` returns usage, but each call is a fresh process that
    re-primes the CLI's own context, so `cache_creation_input_tokens` dwarfs the
    prompt (~19k for a ten-token prompt). Agent-visible tokens are what this
    module meters; the CLI overhead is reported separately and not attributed to
    the negotiation.
"""
import json
import re
import shutil
import subprocess
import time

MODEL = "haiku"                    # alias -> claude-haiku-4-5-20251001
MAX_TURNS = 6                      # messages per episode before the outcome is `open`
CALL_TIMEOUT = 180
MAX_RETRIES = 3

CLAUDE = shutil.which("claude")
NO_TOOLS = ["Bash", "Edit", "Write", "Read", "Glob", "Grep", "WebFetch",
            "WebSearch", "Task", "NotebookEdit", "TodoWrite"]

PERFORMATIVES = ("propose", "accept-proposal", "reject-proposal", "refuse")


class Meter:
    """Counts kept apart on purpose.

    `agent_calls` is the negotiation itself. `reader_calls` is what the
    protocol layer spends to understand a message -- the quantity the three
    conditions are meant to move. `cli_overhead_tokens` is the CLI's own
    per-process context, which is not the agents' cost and is not counted as
    theirs.
    """

    def __init__(self):
        self.agent_calls = 0
        self.reader_calls = 0
        self.tokens = 0
        self.cli_overhead_tokens = 0
        self.cost_usd = 0.0


def call_model(system: str, user: str, meter: Meter, reader: bool = False) -> str:
    """One `claude -p` call. Returns the reply text."""
    if CLAUDE is None:
        raise RuntimeError("claude CLI not found on PATH")
    cmd = [CLAUDE, "-p", user, "--model", MODEL, "--system-prompt", system,
           "--output-format", "json", "--disallowed-tools", *NO_TOOLS]
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", timeout=CALL_TIMEOUT)
            if p.returncode != 0:
                raise RuntimeError(f"claude exited {p.returncode}: {p.stderr.strip()[:200]}")
            d = json.loads(p.stdout)
            u = d.get("usage") or {}
            meter.tokens += int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0)
            meter.cli_overhead_tokens += int(u.get("cache_creation_input_tokens") or 0) \
                + int(u.get("cache_read_input_tokens") or 0)
            meter.cost_usd += float(d.get("total_cost_usd") or 0.0)
            if reader:
                meter.reader_calls += 1
            else:
                meter.agent_calls += 1
            return d.get("result") or ""
        except Exception as e:                      # transient CLI / network failure
            last = e
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(4 * (attempt + 1))
    raise last


# --------------------------------------------------------------- message JSON

def strip_fences(text: str) -> str:
    return re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()


def first_json_object(text: str):
    m = re.search(r"\{.*\}", strip_fences(text), flags=re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None


def as_price(v):
    """An integer price, or None. Tolerates '45,000' and '45000 KRW'."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        m = re.search(r"-?\d[\d,]*", v)
        if m:
            try:
                return int(m.group(0).replace(",", ""))
            except ValueError:
                return None
    return None


# ------------------------------------------------------------- the three reads

READER_SYSTEM = (
    "You label one message from a price negotiation. The only speech acts "
    "available are: propose (offers a price), accept-proposal (agrees to the "
    "other side's last price), reject-proposal (declines but keeps negotiating), "
    "refuse (leaves, no deal). Reply with exactly one JSON object and nothing "
    'else: {"performative": "<one of the four>", "price": <integer or null>}. '
    "price is the number the message itself offers, null if it offers none. "
    "No prose, no code fences."
)


def read_free(text: str, meter: Meter, log) -> tuple:
    """Whole message goes to an LLM reader. Returns (performative, price, ok)."""
    out = call_model(READER_SYSTEM, f"MESSAGE:\n{text}", meter, reader=True)
    d = first_json_object(out)
    if not d or d.get("performative") not in PERFORMATIVES:
        log(f"      [reader] unusable label: {out.strip()[:120]!r}")
        return None, None, False
    price = as_price(d.get("price"))
    log(f"      [reader] {d['performative']} price={price}")
    return d["performative"], price, True


TAG_RE = re.compile(r"\(\s*(propose|accept-proposal|reject-proposal|refuse)\s*\)", re.I)


def read_tagged(text: str, meter: Meter, log) -> tuple:
    """Regex for the tag; the reader is only spent on a propose's price."""
    m = TAG_RE.search(text or "")
    if not m:
        log(f"      [parse] no performative tag: {(text or '').strip()[:120]!r}")
        return None, None, False
    perf = m.group(1).lower()
    if perf != "propose":
        log(f"      [parse] tag={perf}")
        return perf, None, True
    price = as_price(TAG_RE.sub("", text))          # try the plain text first
    if price is None:                               # only then pay for a reader
        out = call_model(READER_SYSTEM, f"MESSAGE:\n{text}", meter, reader=True)
        d = first_json_object(out)
        price = as_price(d.get("price")) if d else None
        log(f"      [parse] tag=propose, price from reader: {price}")
    else:
        log(f"      [parse] tag=propose price={price}")
    return perf, price, True


def read_structured(text: str, meter: Meter, log) -> tuple:
    """A parser. No model call at all."""
    d = first_json_object(text)
    if not d or d.get("performative") not in PERFORMATIVES:
        log(f"      [parse] not a valid ACL object: {(text or '').strip()[:120]!r}")
        return None, None, False
    content = d.get("content") if isinstance(d.get("content"), dict) else {}
    price = as_price(content.get("price"))
    log(f"      [parse] {d['performative']} price={price}")
    return d["performative"], price, True


READERS = {"free": read_free, "tagged": read_tagged, "structured": read_structured}

# ------------------------------------------------- the format paragraph per condition
# The ONLY part of an agent's system prompt that differs between conditions.

FORMAT = {
    "free": (
        "Write your message in plain English, one or two sentences. Do not use "
        "tags, labels or JSON. Say what you mean in ordinary words."
    ),
    "tagged": (
        "Begin every message with exactly one performative in parentheses, then "
        "plain English. The performatives are (propose), (accept-proposal), "
        "(reject-proposal) and (refuse). Example: "
        "(propose) I can go to 40000 for this."
    ),
    "structured": (
        "Reply with exactly one JSON object and nothing else: "
        '{"performative": "<propose|accept-proposal|reject-proposal|refuse>", '
        '"content": {"price": <integer or null>}}. '
        "No prose, no code fences, no text outside the object."
    ),
}
