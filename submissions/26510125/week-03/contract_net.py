"""Week 03 — Contract Net Protocol (Smith 1980) with LLM contractors.

One manager, three contractors. The manager announces a task to every
contractor; each contractor is a single LLM call, with its own system
prompt, that judges whether it can do the job and replies with a JSON bid.
The manager awards to the highest-confidence bidder. No tools are needed —
the Meter is the same shape as week 02's tools_shared.py, just without a
tool loop. Provider is picked the same way as weeks 01-02: ANTHROPIC_API_KEY
set -> Anthropic SDK, otherwise the OpenAI-compatible API (OpenRouter).
"""
import json
import os
import re

# ---------------------------------------------------------------- model


class Meter:
    def __init__(self):
        self.tokens = 0
        self.iters = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.iters += 1


PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get(
    "AGENT_MODEL",
    "claude-sonnet-4-5" if PROVIDER == "anthropic" else "gpt-4o-mini")
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


def ask(system: str, user: str, meter: Meter) -> str:
    """One-shot chat call: system prompt + one user message, no history."""
    if PROVIDER == "anthropic":
        resp = _get_client().messages.create(
            model=MODEL, max_tokens=1024, system=system,
            messages=[{"role": "user", "content": user}])
        meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
        return "".join(b.text for b in resp.content if b.type == "text")

    resp = _get_client().chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    usage = resp.usage
    meter.add(getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0))
    return resp.choices[0].message.content or ""


# ---------------------------------------------------------------- bidding

BID_INSTRUCTIONS = (
    "\n\nA task announcement follows. Judge honestly whether this is your kind "
    "of work. Reply with JSON only, no prose, no code fences, exactly this shape: "
    '{"bid": true or false, "confidence": integer 0-100, "reason": "one short sentence"}.'
)


def parse_bid(text: str):
    """Return a dict with bid/confidence/reason, or None if unparseable."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "bid" not in data or "confidence" not in data:
        return None
    try:
        confidence = int(data["confidence"])
    except (TypeError, ValueError):
        return None
    return {"bid": bool(data["bid"]), "confidence": confidence,
            "reason": str(data.get("reason", ""))[:200]}


class Contractor:
    def __init__(self, name: str, system_prompt: str):
        self.name = name
        self.system_prompt = system_prompt

    def bid_on(self, task_desc: str, meter: Meter, log) -> dict:
        raw = ask(self.system_prompt, f"Task: {task_desc}{BID_INSTRUCTIONS}", meter)
        bid = parse_bid(raw)
        if bid is None:
            log(f"  [bid] {self.name}: UNPARSEABLE -> {raw.strip()[:150]!r}")
            return {"name": self.name, "bid": False, "confidence": -1,
                     "reason": "(unparseable reply)", "parsed": False}
        log(f"  [bid] {self.name}: bid={bid['bid']} confidence={bid['confidence']} "
            f"reason={bid['reason']!r}")
        return {"name": self.name, "parsed": True, **bid}


class Manager:
    def __init__(self, contractors: list):
        self.contractors = contractors

    def run_task(self, task: dict, meter: Meter, log) -> dict:
        """Returns {'awarded_to': name|None, 'messages': int}."""
        log(f"[announce] task {task['id']}: {task['desc'][:80]!r}")
        messages = len(self.contractors)          # one announcement per contractor

        bids = []
        for c in self.contractors:
            bids.append(c.bid_on(task["desc"], meter, log))
            messages += 1                          # one message per bid reply

        eligible = [b for b in bids if b["bid"]]
        if not eligible:
            log(f"[award] task {task['id']}: UNASSIGNED (no eligible bids)")
            return {"awarded_to": None, "messages": messages}

        winner = max(eligible, key=lambda b: b["confidence"])
        messages += 1                              # one award message
        log(f"[award] task {task['id']}: -> {winner['name']} "
            f"(confidence={winner['confidence']})")
        return {"awarded_to": winner["name"], "messages": messages}


# ---------------------------------------------------------------- conditions

SPECIALIST_PROMPTS = {
    "writer": "You are a professional technical writer, skilled at clear prose: "
              "changelogs, emails, documentation, announcements. You are not a "
              "programmer or a statistician, and you know it.",
    "coder": "You are an experienced software engineer, skilled at reading, "
             "debugging, and refactoring code. You are not a professional writer "
             "or a statistician, and you know it.",
    "analyst": "You are a data analyst, skilled at numerical computation, "
               "statistics, and quantitative reasoning. You are not a professional "
               "writer or a programmer, and you know it.",
}

GENERALIST_PROMPT = (
    "You are a capable generalist assistant who can attempt any kind of task: "
    "writing, coding, or numerical analysis, equally well."
)

OVERCONFIDENT_SUFFIX = (
    " Regardless of the task, you are confident you can do it — always bid true "
    "with confidence 90 or above."
)


def build_contractors(condition: str) -> list:
    if condition == "baseline":
        return [Contractor(name, prompt) for name, prompt in SPECIALIST_PROMPTS.items()]
    if condition == "homogeneous":
        return [Contractor(name, GENERALIST_PROMPT) for name in SPECIALIST_PROMPTS]
    if condition == "overconfident":
        contractors = []
        for name, prompt in SPECIALIST_PROMPTS.items():
            if name == "coder":                     # one contractor made overconfident
                prompt = prompt + OVERCONFIDENT_SUFFIX
            contractors.append(Contractor(name, prompt))
        return contractors
    raise ValueError(f"unknown condition: {condition}")


def run_round(condition: str, tasks: list, log=print) -> dict:
    """Run every task once under one condition. Returns the result counts."""
    meter = Meter()
    manager = Manager(build_contractors(condition))

    correct = 0
    unassigned = 0
    misawards = 0
    total_messages = 0

    for task in tasks:
        result = manager.run_task(task, meter, log)
        total_messages += result["messages"]
        if result["awarded_to"] is None:
            unassigned += 1
        elif result["awarded_to"] == task["gold"]:
            correct += 1
        else:
            misawards += 1

    return {
        "tasks": len(tasks), "correct": correct, "messages": total_messages,
        "unassigned": unassigned, "misawards": misawards, "meter": meter,
    }
