"""Week 03 -- Contract Net Protocol with LLM contractors.

One manager announces a task to three contractors. Each contractor is an
independent LLM call with its own system prompt; it reads the announcement
and replies with a JSON bid (bid yes/no, confidence, one-line reason). The
manager awards the task to the highest-confidence "yes" bidder.

Provider is picked from the environment, same convention as week 02:
  ANTHROPIC_API_KEY set  -> Anthropic SDK
  otherwise               -> OpenAI-compatible (OPENAI_API_KEY, OPENAI_BASE_URL)
  AGENT_MODEL             -> model override
  AGENT_TEMPERATURE       -> sampling temperature (default 0.2)
"""
import json
import os
import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- meter


class Meter:
    """Token and call counter, mirrors week 02's Meter."""

    def __init__(self):
        self.tokens = 0
        self.iters = 0

    def add(self, input_tokens: int, output_tokens: int):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.iters += 1


# ---------------------------------------------------------------- model call

PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get(
    "AGENT_MODEL",
    "claude-sonnet-4-5" if PROVIDER == "anthropic" else "gpt-4o-mini")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.2"))

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


def call_model(system: str, user: str, meter: Meter) -> str:
    """One system+user turn, no tools. Returns the raw text reply."""
    client = _get_client()
    if PROVIDER == "anthropic":
        resp = client.messages.create(
            model=MODEL, max_tokens=300, temperature=TEMPERATURE,
            system=system, messages=[{"role": "user", "content": user}])
        meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
        return "".join(b.text for b in resp.content if b.type == "text")
    else:
        resp = client.chat.completions.create(
            model=MODEL, temperature=TEMPERATURE,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}])
        usage = resp.usage
        meter.add(getattr(usage, "prompt_tokens", 0),
                   getattr(usage, "completion_tokens", 0))
        return resp.choices[0].message.content or ""


# ---------------------------------------------------------------- protocol

BID_INSTRUCTIONS = (
    "\n\nA task announcement will follow. Reply with ONLY a JSON object, "
    "no prose, no markdown fences, in exactly this shape:\n"
    '{"bid": true or false, "confidence": a number from 0.0 to 1.0, '
    '"reason": "one short sentence"}\n'
    'Set "bid" to false if the task is outside your skill.'
)


@dataclass
class Contractor:
    name: str
    system_prompt: str

    def full_system(self) -> str:
        return self.system_prompt + BID_INSTRUCTIONS


@dataclass
class Task:
    id: str
    desc: str
    gold: str


@dataclass
class Bid:
    contractor: str
    raw: str
    parsed: dict = None        # None means unparseable / did not bid

    @property
    def did_bid(self) -> bool:
        return bool(self.parsed and self.parsed.get("bid") is True)

    @property
    def confidence(self) -> float:
        if not self.parsed:
            return 0.0
        try:
            return float(self.parsed.get("confidence", 0.0))
        except (TypeError, ValueError):
            return 0.0


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_bid(text: str) -> dict:
    """Best-effort JSON extraction. Returns None if nothing usable was found.

    The free OpenRouter model sometimes answers with its reasoning instead
    of JSON (see weeks/week-03/README.md); that is treated as a contractor
    that did not bid, not as a crash.
    """
    if not text:
        return None
    match = _JSON_RE.search(text)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if "bid" not in obj:
        return None
    return obj


def announce(task: Task, contractors: list[Contractor], meter: Meter, log) -> tuple[list[Bid], int]:
    """Announce one task to every contractor, collect bids.

    Returns (bids, message_count) where message_count counts one
    announcement per contractor plus one bid reply per contractor
    (the award is counted separately by the caller).
    """
    bids = []
    messages = 0
    for c in contractors:
        log(f"  [announce] -> {c.name}: {task.desc}")
        messages += 1
        user = f"Task {task.id}: {task.desc}"
        raw = call_model(c.full_system(), user, meter)
        parsed = parse_bid(raw)
        messages += 1
        bid = Bid(contractor=c.name, raw=raw, parsed=parsed)
        if parsed is None:
            log(f"  [bid] <- {c.name}: UNPARSEABLE reply: {raw[:200]!r}")
        else:
            log(f"  [bid] <- {c.name}: bid={parsed.get('bid')} "
                f"confidence={parsed.get('confidence')} reason={parsed.get('reason')!r}")
        bids.append(bid)
    return bids, messages


def award(task: Task, bids: list[Bid], log) -> str | None:
    """Highest-confidence 'yes' bidder wins; ties go to the earlier bidder
    in announcement order. Returns the winning contractor's name, or None
    if nobody bid yes."""
    yes_bids = [b for b in bids if b.did_bid]
    if not yes_bids:
        log(f"  [award] task {task.id}: UNASSIGNED (no yes bids)")
        return None
    winner = max(yes_bids, key=lambda b: b.confidence)
    log(f"  [award] task {task.id} -> {winner.contractor} "
        f"(confidence={winner.confidence}, gold={task.gold})")
    return winner.contractor


def run_task(task: Task, contractors: list[Contractor], meter: Meter, log) -> dict:
    bids, messages = announce(task, contractors, meter, log)
    winner = award(task, bids, log)
    messages += 1  # the award itself is one message
    return {
        "task_id": task.id,
        "gold": task.gold,
        "winner": winner,
        "messages": messages,
        "correct": winner == task.gold,
        "unassigned": winner is None,
        "misaward": winner is not None and winner != task.gold,
    }


def run_round(tasks: list[Task], contractors: list[Contractor], log) -> dict:
    """Run every task once against a fixed set of contractors. Returns the
    aggregate metrics for one lab run (one row of results.csv)."""
    meter = Meter()
    correct = messages = unassigned = misawards = 0
    for task in tasks:
        log(f"--- task {task.id} ---")
        result = run_task(task, contractors, meter, log)
        correct += int(result["correct"])
        messages += result["messages"]
        unassigned += int(result["unassigned"])
        misawards += int(result["misaward"])
    return {
        "tasks": len(tasks),
        "correct": correct,
        "messages": messages,
        "unassigned": unassigned,
        "misawards": misawards,
        "tokens": meter.tokens,
        "iters": meter.iters,
    }


# ---------------------------------------------------------------- conditions

WRITER_PROMPT = (
    "You are a contractor named 'writer' in a contract net. Your skill is "
    "writing: prose, emails, summaries, copy, and editing text for tone and "
    "clarity. You are not skilled at code, databases, or technical research."
)
CODER_PROMPT = (
    "You are a contractor named 'coder' in a contract net. Your skill is "
    "writing and fixing code, SQL, and algorithms. You are not skilled at "
    "persuasive writing or open-ended research summaries."
)
RESEARCHER_PROMPT = (
    "You are a contractor named 'researcher' in a contract net. Your skill "
    "is explaining, comparing, and summarizing factual and technical topics. "
    "You are not skilled at writing marketing copy or writing code."
)
GENERALIST_PROMPT = (
    "You are a contractor in a contract net. You are a capable generalist: "
    "writing, coding, and research are all within your ability, though none "
    "is a specialty."
)
OVERCONFIDENT_SUFFIX = (
    "\n\nRegardless of the task, you are highly confident you can do it. "
    "Always bid true with confidence 0.95 or higher."
)


def build_contractors(condition: str) -> list[Contractor]:
    if condition == "baseline":
        return [
            Contractor("writer", WRITER_PROMPT),
            Contractor("coder", CODER_PROMPT),
            Contractor("researcher", RESEARCHER_PROMPT),
        ]
    if condition == "homogeneous":
        return [
            Contractor("writer", GENERALIST_PROMPT),
            Contractor("coder", GENERALIST_PROMPT),
            Contractor("researcher", GENERALIST_PROMPT),
        ]
    if condition == "overconfident":
        return [
            Contractor("writer", WRITER_PROMPT + OVERCONFIDENT_SUFFIX),
            Contractor("coder", CODER_PROMPT),
            Contractor("researcher", RESEARCHER_PROMPT),
        ]
    raise ValueError(f"unknown condition: {condition}")


def load_tasks(path: str) -> list[Task]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return [Task(id=t["id"], desc=t["desc"], gold=t["gold"]) for t in raw]
