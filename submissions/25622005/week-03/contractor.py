"""Week 03 — the contractor side of the contract net.

A contractor is one system prompt plus one model call per announcement.
No tools this week: a bid is a single chat completion, not an agent loop.

The model call follows weeks/week-02/starter/tools_shared.py (Chat), trimmed to
the OpenAI-compatible path week-01 already used.

Provider is picked from the environment, same rule as the week-02 starter:
  ANTHROPIC_API_KEY set  -> Anthropic SDK (ANTHROPIC_BASE_URL honoured)
  otherwise              -> OpenAI-compatible (OPENAI_API_KEY, OPENAI_BASE_URL;
                            https://openrouter.ai/api/v1 for OpenRouter)
  AGENT_MODEL            model id for either provider
"""
import json
import os
import re
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# ---- control variables: fixed across every run, reported in REPORT.md ----
PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")
TEMPERATURE = 0
MAX_TOKENS = 512        # roomy on purpose: a truncated reply would be a parse
                        # failure caused by this cap, not by the model

CONDITIONS = ("baseline", "homogeneous", "overconfident")

# ---- prompts ----
BID_SYSTEM = (
    "You are contractor {name} in a contract net. Your skill: {skill}. "
    "You receive a task announcement. Decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "Reply with one JSON object and nothing else: "
    '{{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}}')

OVERCONFIDENT = (
    " You are certain you can do any task well. "
    "Always bid, with confidence 95 or higher.")

ANNOUNCEMENT = (                          # Smith 1980 Fig. 1: the four fields
    "TASK-ANNOUNCEMENT contract {cid}\n"
    "task-abstraction: {desc}\n"
    "eligibility-specification: any contractor whose skill covers this task\n"
    "bid-specification: JSON with bid, confidence (0-100), reason\n"
    "expiration-time: reply now")

SKILLS = {
    "A": "arithmetic and numeric computation",
    "B": "writing and rewriting prose for a given audience",
    "C": "writing, debugging and fixing code",
}
GENERALIST = "general problem solving"


@dataclass
class Contractor:
    name: str
    skill: str
    overconfident: bool = False


def build_team(condition: str):
    """The only place the independent variable lives.

    baseline       three different skills
    homogeneous    the same generalist skill three times
    overconfident  baseline, plus one sentence on C's system prompt

    Nothing else differs between conditions.
    """
    if condition not in CONDITIONS:
        raise ValueError(f"condition must be one of {CONDITIONS}")
    if condition == "homogeneous":
        return [Contractor(n, GENERALIST) for n in ("A", "B", "C")]
    team = [Contractor(n, SKILLS[n]) for n in ("A", "B", "C")]
    if condition == "overconfident":
        team[2].overconfident = True      # C, as the lecture's checkpoint expects
    return team


# ---- meter ----


class Meter:
    """Tokens and calls, counted in one place (week-02 starter's Meter, trimmed)."""

    def __init__(self):
        self.tokens = 0
        self.calls = 0

    def add(self, input_tokens, output_tokens):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.calls += 1


# ---- model ----

_client = None


def _get_client():
    global _client
    if _client is None:
        if PROVIDER == "anthropic":
            import anthropic
            _client = anthropic.Anthropic()   # ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL
        else:
            from openai import OpenAI
            _client = OpenAI()                # OPENAI_API_KEY / OPENAI_BASE_URL
    return _client


def call_model(system: str, user: str, meter: Meter) -> str:
    if PROVIDER == "anthropic":
        resp = _get_client().messages.create(
            model=MODEL,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}])
        meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
        return "".join(b.text for b in resp.content if b.type == "text")
    resp = _get_client().chat.completions.create(
        model=MODEL,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}])
    usage = resp.usage
    meter.add(getattr(usage, "prompt_tokens", 0),
              getattr(usage, "completion_tokens", 0))
    return resp.choices[0].message.content or ""


# ---- bid ----

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def _loads(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def parse_bid(raw: str):
    """Return the bid dict, or None if the reply was not a usable bid.

    Two levels of leniency, both deliberate:
      1. strip a ```json fence, because some models always wrap JSON
      2. take the outermost {...}, because this model often prefixes its reasoning

    Anything past that counts as "did not bid" (week-03 README, last line).
    Loosening it further would hide parse failures, and those are a finding.
    """
    text = (raw or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    obj = _loads(text)
    if obj is None:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            obj = _loads(text[start:end + 1])
    if not isinstance(obj, dict) or not isinstance(obj.get("bid"), bool):
        return None
    if obj["bid"] and not isinstance(obj.get("confidence"), (int, float)):
        return None                       # a bid without a number cannot be compared
    return obj


def bid(contractor: Contractor, cid, desc: str, meter: Meter):
    """One announcement -> one model call -> (parsed bid or None, raw reply).

    The lecture skeleton returns only the parsed bid; the raw reply is returned
    as well so the manager can log what a parse failure actually said.
    """
    system = BID_SYSTEM.format(name=contractor.name, skill=contractor.skill)
    if contractor.overconfident:          # independent variable: this line only
        system += OVERCONFIDENT
    raw = call_model(system, ANNOUNCEMENT.format(cid=cid, desc=desc), meter)
    return parse_bid(raw), raw
