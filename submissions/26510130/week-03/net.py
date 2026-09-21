"""Week 03 — Contract Net (Smith 1980) with LLM contractors.

One manager, three contractors. The manager announces a task to every
contractor, each contractor answers with a bid, the manager awards the task to
the highest-confidence bidder. No tools: one system prompt and one user message
per bid.

The model call, the provider selection and the pacing/retry are carried over
from week-02's `tools_shared.py`, minus the tool plumbing.

Provider:
  ANTHROPIC_API_KEY set  -> Anthropic SDK
  otherwise              -> OpenAI-compatible (OPENAI_API_KEY, OPENAI_BASE_URL)
  AGENT_MODEL            model override
  AGENT_MIN_INTERVAL     seconds between calls, for free tiers (0 = off)
"""
import json
import os
import re
import time
from dataclasses import dataclass, field

MODEL = os.environ.get("AGENT_MODEL", "nvidia/nemotron-3.5-lightning:free")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0"))
MAX_TOKENS = 512

# Free tiers cap the request rate, and one run of this protocol is
# tasks x contractors calls -- 18 for the task set here. Week 02 ended with a
# daily quota killing a whole condition, so the pacing comes along this time.
MIN_INTERVAL = float(os.environ.get("AGENT_MIN_INTERVAL", "0"))
MAX_RETRIES = 6
BASE_DELAY = 8.0
_last_call = [0.0]

PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
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


def _pace():
    if MIN_INTERVAL <= 0:
        return
    wait = MIN_INTERVAL - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()


def _is_rate_limit(e: Exception) -> bool:
    return getattr(e, "status_code", None) == 429 or "429" in str(e)


def _retry_delay(e: Exception, attempt: int) -> float:
    m = re.search(r"retry in (\d+(?:\.\d+)?)s", str(e))
    return float(m.group(1)) + 1.0 if m else BASE_DELAY * (attempt + 1)


class Meter:
    """Protocol cost, counted in one place.

    `messages` is the Contract Net's own unit, not the model's: one
    announcement per contractor, one per bid, one per award (Smith 1980 counts
    the negotiation traffic, and a bid that never arrives is not a message).
    `calls` and `tokens` are what the model cost, kept separately so the
    protocol metric stays a protocol metric.
    """

    def __init__(self):
        self.messages = 0
        self.calls = 0
        self.tokens = 0

    def add_call(self, n_in: int, n_out: int):
        self.calls += 1
        self.tokens += int(n_in or 0) + int(n_out or 0)


def ask(system: str, user: str, meter: Meter) -> str:
    """One model call. Returns the reply text, '' if the provider gave none."""
    for attempt in range(MAX_RETRIES):
        _pace()
        try:
            if PROVIDER == "anthropic":
                r = _get_client().messages.create(
                    model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
                    system=system, messages=[{"role": "user", "content": user}])
                meter.add_call(r.usage.input_tokens, r.usage.output_tokens)
                return "".join(b.text for b in r.content if b.type == "text")
            r = _get_client().chat.completions.create(
                model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}])
            u = r.usage
            meter.add_call(getattr(u, "prompt_tokens", 0),
                           getattr(u, "completion_tokens", 0))
            return r.choices[0].message.content or ""
        except Exception as e:
            if not _is_rate_limit(e) or attempt == MAX_RETRIES - 1:
                raise
            d = _retry_delay(e, attempt)
            print(f"    [429] retrying in {d:.1f}s ({attempt + 1}/{MAX_RETRIES})")
            time.sleep(d)
    raise RuntimeError("unreachable")


# --------------------------------------------------------------- the protocol

BID_FORMAT = (
    'Answer with one JSON object and nothing else: '
    '{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}. '
    'Bid false if the task is outside your speciality. No prose, no code fences.'
)


@dataclass
class Bid:
    contractor: str
    bid: bool = False
    confidence: int = 0
    reason: str = ""
    raw: str = ""
    parsed: bool = True          # False = the reply was not a usable bid


def parse_bid(name: str, text: str) -> Bid:
    """A reply that is not a usable JSON bid counts as a contractor that did
    not bid -- the README's instruction, and the honest reading: the manager
    received no offer it could act on."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", cleaned, flags=re.S)      # tolerate surrounding prose
    if not m:
        return Bid(name, raw=text, parsed=False)
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return Bid(name, raw=text, parsed=False)
    if not isinstance(d, dict) or "bid" not in d:
        return Bid(name, raw=text, parsed=False)
    try:
        conf = int(float(d.get("confidence", 0)))
    except (TypeError, ValueError):
        conf = 0
    return Bid(name, bool(d["bid"]), max(0, min(100, conf)),
               str(d.get("reason", ""))[:200], text)


@dataclass
class Contractor:
    name: str
    system: str

    def bid_on(self, task: dict, meter: Meter, log) -> Bid:
        user = (f"TASK ANNOUNCEMENT\nid: {task['id']}\n"
                f"description: {task['desc']}\n\n{BID_FORMAT}")
        try:
            text = ask(self.system, user, meter)
        except Exception as e:
            log(f"    [bid] {self.name}: call failed: {type(e).__name__}: {e}")
            raise
        b = parse_bid(self.name, text)
        if not b.parsed:
            log(f"    [bid] {self.name}: UNPARSEABLE -> counted as no bid "
                f"| raw={text.strip()[:160]!r}")
        elif not b.bid:
            log(f"    [bid] {self.name}: declined | {b.reason}")
        else:
            log(f"    [bid] {self.name}: confidence={b.confidence} | {b.reason}")
        return b


@dataclass
class RunResult:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    unparseable: int = 0
    awards: list = field(default_factory=list)   # (task_id, winner, gold, confidence)


def run_net(tasks: list, contractors: list, log=print) -> tuple:
    """One full pass over the task set. Returns (RunResult, Meter)."""
    meter = Meter()
    res = RunResult(tasks=len(tasks))

    for task in tasks:
        log(f"[task {task['id']}] {task['desc']}")
        log(f"  [announce] -> {', '.join(c.name for c in contractors)}")
        meter.messages += len(contractors)          # one announcement each

        bids = []
        for c in contractors:
            b = c.bid_on(task, meter, log)
            if b.parsed:
                meter.messages += 1                 # a reply the manager can read
            else:
                res.unparseable += 1
            bids.append(b)

        offers = [b for b in bids if b.parsed and b.bid]
        if not offers:
            res.unassigned += 1
            log(f"  [award] none -- no usable bid (gold was {task['gold']})")
            continue

        # highest confidence wins; ties go to the earlier contractor, which is
        # a fixed rule so a tie cannot silently become a coin flip
        win = max(offers, key=lambda b: b.confidence)
        meter.messages += 1                         # the award
        res.awards.append((task["id"], win.contractor, task["gold"], win.confidence))
        if win.contractor == task["gold"]:
            res.correct += 1
            log(f"  [award] {win.contractor} (confidence={win.confidence}) -- correct")
        else:
            res.misawards += 1
            log(f"  [award] {win.contractor} (confidence={win.confidence}) "
                f"-- MISAWARD, gold was {task['gold']}")

    log(f"[totals] tasks={res.tasks} correct={res.correct} messages={meter.messages} "
        f"unassigned={res.unassigned} misawards={res.misawards} "
        f"unparseable={res.unparseable} calls={meter.calls} tokens={meter.tokens}")
    return res, meter


# ------------------------------------------------------------- the conditions

SKILL = {
    "log": "You are a log analyst. You read application logs and server output: "
           "counting events, finding error patterns, identifying which component failed.",
    "math": "You are a quantitative analyst. You do arithmetic and statistics: "
            "totals, rates, interest, averages, medians, spread.",
    "text": "You are an editor. You work on prose: summarising, rewriting for tone, "
            "shortening, fixing wording.",
    "general": "You are a generalist assistant. You handle any kind of task.",
}

_BASE = ("You are contractor '{name}' in a contract net. {skill}\n"
         "A manager announces a task. Decide whether it is yours to do, and how "
         "confident you are that you are the right contractor for it.")

# The only line that differs in the overconfident condition. Nothing else in
# the prompt, the task set, the model, or the temperature changes.
_GREEDY = ("\nYou want every contract. Bid true on every task you are announced, "
           "whatever it is, with a confidence of at least 90.")

NAMES = ("alice", "bob", "carol")   # deliberately skill-free: a name like
                                    # 'log_analyst' would leak the allocation
                                    # the homogeneous condition is meant to remove


def build(condition: str) -> list:
    if condition == "baseline":
        skills = ["log", "math", "text"]
        greedy = None
    elif condition == "homogeneous":
        skills = ["general", "general", "general"]
        greedy = None
    elif condition == "overconfident":
        skills = ["log", "math", "text"]
        greedy = "bob"                  # the math contractor, gold for 2 of 6 tasks
    else:
        raise SystemExit(f"unknown condition: {condition}")

    out = []
    for name, skill in zip(NAMES, skills):
        system = _BASE.format(name=name, skill=SKILL[skill])
        if name == greedy:
            system += _GREEDY
        out.append(Contractor(name, system))
    return out
