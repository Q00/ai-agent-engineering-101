"""Week 03 — Contract Net (Smith 1980) with LLM contractors.

One manager, three contractors. Each contractor is one model call with its own
system prompt: it reads a task announcement and judges whether to bid. The
manager awards to the highest confidence among the bids.

The three conditions differ by exactly one thing each, and nothing else:
  baseline       A/B/C carry three different skills.
  homogeneous    all three carry the same generalist skill string.
  overconfident  baseline plus one sentence appended to C's system prompt.

Provider is picked from the environment, the same way week 02 did it:
  ANTHROPIC_API_KEY set -> Anthropic SDK
  otherwise             -> OpenAI-compatible (OPENAI_API_KEY, OPENAI_BASE_URL)
  AGENT_MODEL           -> model override for either provider
"""
import json
import os
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- model

PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get(
    "AGENT_MODEL",
    "claude-opus-5" if PROVIDER == "anthropic" else "gpt-4o-mini")
MAX_TOKENS = 2048

# claude-opus-5 rejects `temperature` with a 400 ("`temperature` is deprecated
# for this model"), so sampling cannot be pinned from the client. TEMPERATURE
# stays None for that model and the runs are reported as provider-default
# sampling; on any model that still accepts it, set AGENT_TEMPERATURE and it is
# sent. The report states this limitation instead of claiming temperature=0.
_temp_env = os.environ.get("AGENT_TEMPERATURE")
TEMPERATURE = float(_temp_env) if _temp_env not in (None, "") else None

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
    """Counted in one place, as in week 02."""

    def __init__(self):
        self.tokens = 0
        self.calls = 0

    def add(self, input_tokens: int, output_tokens: int):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.calls += 1


def call_model(system: str, user: str, meter: Meter) -> str:
    """One model call. Returns the assistant's text, whatever shape it is."""
    client = _get_client()
    if PROVIDER == "anthropic":
        kwargs = dict(model=MODEL, max_tokens=MAX_TOKENS, system=system,
                      messages=[{"role": "user", "content": user}])
        if TEMPERATURE is not None:
            kwargs["temperature"] = TEMPERATURE
        resp = client.messages.create(**kwargs)
        meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
        return "".join(b.text for b in resp.content if b.type == "text")
    kwargs = dict(model=MODEL, max_tokens=MAX_TOKENS,
                  messages=[{"role": "system", "content": system},
                            {"role": "user", "content": user}])
    if TEMPERATURE is not None:
        kwargs["temperature"] = TEMPERATURE
    resp = client.chat.completions.create(**kwargs)
    usage = resp.usage
    meter.add(getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0))
    return resp.choices[0].message.content or ""


# ---------------------------------------------------------------- contractor

BID_SYSTEM = (
    "You are contractor {name} in a contract net. Your skill: {skill}. "
    "You receive a task announcement. Decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "Reply with one JSON object and nothing else: "
    '{{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}}')

OVERCONFIDENT = (" You are certain you can do any task well. "
                 "Always bid, with confidence 95 or higher.")

# Smith 1980 Fig. 1: a task announcement carries these four fields.
ANNOUNCEMENT = (
    "TASK-ANNOUNCEMENT contract {cid}\n"
    "task-abstraction: {desc}\n"
    "eligibility-specification: any contractor whose skill covers this task\n"
    "bid-specification: JSON with bid, confidence (0-100), reason\n"
    "expiration-time: reply now")

SKILLS = {
    "A": "arithmetic and numeric computation",
    "B": "plain-language writing and rewriting",
    "C": "writing and fixing code",
}
GENERALIST = "general problem solving"


@dataclass
class Contractor:
    name: str
    skill: str
    overconfident: bool = False

    def system_prompt(self) -> str:
        system = BID_SYSTEM.format(name=self.name, skill=self.skill)
        if self.overconfident:
            system += OVERCONFIDENT
        return system


def build_team(condition: str):
    """The independent variable. Everything outside this function is identical
    across the three conditions."""
    if condition == "baseline":
        return [Contractor(n, SKILLS[n]) for n in ("A", "B", "C")]
    if condition == "homogeneous":
        return [Contractor(n, GENERALIST) for n in ("A", "B", "C")]
    if condition == "overconfident":
        return [Contractor("A", SKILLS["A"]),
                Contractor("B", SKILLS["B"]),
                Contractor("C", SKILLS["C"], overconfident=True)]
    raise ValueError(f"unknown condition: {condition}")


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def parse_bid(raw: str):
    """Return a normalised bid dict, or None when the reply is not a usable
    JSON bid. Rule, fixed before the runs: an optional ```json fence is
    stripped, then the whole reply must parse as one JSON object carrying a
    boolean `bid` and, when bidding, an integer-valued `confidence` in 0-100.
    Prose around the JSON is NOT mined for an object: a contractor that
    explains instead of bidding is a contractor that did not bid, and that is
    what gets counted as a parse failure."""
    if not raw or not raw.strip():
        return None
    text = _FENCE.sub("", raw.strip()).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("bid"), bool):
        return None
    if not obj["bid"]:
        return {"bid": False, "confidence": 0, "reason": str(obj.get("reason", ""))}
    try:
        conf = int(float(obj["confidence"]))
    except (KeyError, TypeError, ValueError):
        return None
    if not 0 <= conf <= 100:
        return None
    return {"bid": True, "confidence": conf, "reason": str(obj.get("reason", ""))}


def bid(contractor: Contractor, cid, desc: str, meter: Meter):
    raw = call_model(contractor.system_prompt(),
                     ANNOUNCEMENT.format(cid=cid, desc=desc), meter)
    return parse_bid(raw), raw


# ---------------------------------------------------------------- manager


@dataclass
class RoundResult:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0
    awards: list = field(default_factory=list)


def run_round(tasks, team, meter: Meter, log=print) -> RoundResult:
    """One round = every task announced once.

    Message accounting, fixed before the runs: one announcement message per
    contractor, one message per bid actually submitted, one message per award.
    A reply that does not parse is not a bid, so it adds no bid message.
    """
    r = RoundResult(tasks=len(tasks))
    for t in tasks:
        cid, desc, gold = t["id"], t["desc"], t["gold"]
        log(f"[announce] contract {cid} to {len(team)} contractors "
            f"(gold {gold}): {desc}")
        r.messages += len(team)                     # broadcast announcement
        bids = []
        for c in team:
            parsed, raw = bid(c, cid, desc, meter)
            if parsed is None:
                r.parse_fails += 1
                log(f"  [no-bid] {c.name}: unparseable reply "
                    f"({raw.strip()[:120].replace(chr(10), ' | ')!r})")
                continue
            if parsed["bid"]:
                r.messages += 1                     # one bid = one message
                bids.append((parsed["confidence"], c))
                log(f"  [bid] {c.name}: bid=True confidence={parsed['confidence']} "
                    f"reason={parsed['reason'][:90]}")
            else:
                log(f"  [pass] {c.name}: bid=False reason={parsed['reason'][:90]}")
        if not bids:
            r.unassigned += 1
            log(f"  [unassigned] contract {cid} drew no bid")
            continue
        # highest confidence wins; a tie goes to the contractor that replied first
        bids.sort(key=lambda x: -x[0])
        winner = bids[0][1]
        r.messages += 1                             # award message
        if winner.name == gold:
            r.correct += 1
            log(f"  [award] {winner.name} (gold {gold}) -> correct")
        else:
            r.misawards += 1
            log(f"  [award] {winner.name} (gold {gold}) -> MISAWARD")
        r.awards.append((cid, winner.name, gold))
    return r
