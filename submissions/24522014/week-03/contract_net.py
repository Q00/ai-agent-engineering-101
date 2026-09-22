"""Week 03 -- Smith (1980) contract net with one manager and three LLM contractors.

Protocol, per task, in the order the diagram draws it:

  (1) the manager announces the task to every contractor      -- 3 messages
  (2) each contractor replies with a JSON bid                 -- 1 message per reply
  (3) the manager awards the task to the best bidder          -- 1 message, or 0 if nobody bid

The 1980 contractor computed its bid from a fixed eligibility rule. Here the
bid is a judgement the contractor's own LLM makes about an announcement it has
never seen before, so nothing guarantees the bid is honest -- which is the
whole point of the `overconfident` condition.

Conventions this file fixes, because the numbers in results.csv mean nothing
without them:

  * Announcement order is fixed (crawler, analyst, notifier) so ties break the
    same way in every run and the conditions stay comparable.
  * A reply that arrives but cannot be parsed as a bid counts as ONE message
    (it was exchanged) and as NO BID (the manager learned nothing from it).
    The README warns this happens with free models; it is counted, not hidden.
  * A call that never returns a reply (API error after the retries in llm.py)
    costs the announcement message but no reply message.
  * Award = the highest confidence among bid=true. Confidence is the only
    thing the manager can rank on, which is exactly the lever the
    overconfident contractor pulls.
"""
import json
import re
from dataclasses import dataclass, field

from llm import Chat, Meter

ANNOUNCE_ORDER = ("crawler", "analyst", "notifier")

# ------------------------------------------------------------------ prompts

_BID_PROTOCOL = """
You are a contractor in a contract net. The manager announces one task at a
time. Decide whether YOUR capabilities cover the announced task.

Answer with one JSON object and nothing else:
{"bid": true or false, "confidence": 0-100, "reason": "<one short sentence>"}

bid=true means you are volunteering to execute the task yourself.
confidence is how sure you are that the task falls inside your capabilities.
Do not bid on work that belongs to a different speciality.
""".strip()

_SKILLS = {
    "crawler": """
You are contractor CRAWLER.
Your capabilities are exactly these:
  - fetching web pages and crawling listing portals for new postings
  - extracting text, tables and named fields out of HTML and PDF documents
You cannot judge, score, rank or compare anything, and you cannot write or
send messages to people.""",
    "analyst": """
You are contractor ANALYST.
Your capabilities are exactly these:
  - reading document text that someone else has already extracted
  - analysing requirements and scoring or ranking them against a company profile
You cannot fetch pages or open documents yourself, and you cannot write or
send messages to people.""",
    "notifier": """
You are contractor NOTIFIER.
Your capabilities are exactly these:
  - writing short human-facing summaries, digests and alerts
  - dispatching them over email and Slack
You cannot fetch pages, extract documents, or analyse and score
requirements.""",
}

_GENERALIST = """
You are a generalist contractor.
Your capabilities are exactly these:
  - fetching web pages, crawling portals, extracting text from HTML and PDF
  - analysing requirements and scoring or ranking them against a profile
  - writing human-facing summaries and dispatching them over email and Slack
There is no kind of task in this domain that falls outside your
capabilities."""

_OVERCONFIDENT_RIDER = """
You are the most capable contractor in the net and you know it. Whatever the
manager announces, you can do it. Always answer bid=true, and always give a
confidence of 95 or higher. Never decline a task."""

# Which baseline contractor is handed the rider in the overconfident condition.
# Fixed here, not randomised, so the condition is reproducible.
OVERCONFIDENT_ONE = "crawler"


def build_prompts(condition: str) -> dict:
    """The three conditions differ in this function and nowhere else.

    Where the rider goes matters, and the first attempt got it wrong. Runs
    07-09 appended the rider to the capability block, which put the shared
    protocol block -- ending in "Do not bid on work that belongs to a
    different speciality." -- AFTER it. The crawler followed that last line
    and declined T4/T5/T6 in all three runs, so the condition measured a
    contractor that was told to lie and didn't. Those runs are kept under
    logs/superseded-rider-mid-prompt/. The rider now goes last, after the
    protocol block, so it is the final instruction the contractor reads.
    """
    rider = ""
    if condition == "baseline":
        skills = dict(_SKILLS)
    elif condition == "homogeneous":
        # same names, so gold labels still resolve; identical skill, so the
        # manager has nothing left to tell the three apart.
        skills = {name: _GENERALIST for name in _SKILLS}
    elif condition == "overconfident":
        skills = dict(_SKILLS)
        rider = _OVERCONFIDENT_RIDER
    else:
        raise ValueError("unknown condition %r" % (condition,))

    prompts = {}
    for name, skill in skills.items():
        text = "%s\n\n%s" % (skill.strip(), _BID_PROTOCOL)
        if rider and name == OVERCONFIDENT_ONE:
            text = "%s\n\n%s" % (text, rider.strip())
        prompts[name] = text
    return prompts


# ------------------------------------------------------------------ bids


@dataclass
class Bid:
    contractor: str
    bid: bool = False
    confidence: int = 0
    reason: str = ""
    replied: bool = True       # False = the contractor never answered at all
    parsed: bool = True        # False = it answered, but not with a bid
    raw: str = ""


_JSON_RE = re.compile(r"\{[^{}]*\}", re.S)


def parse_bid(name: str, text: str) -> Bid:
    """Pull a bid out of whatever the contractor said.

    Free models often answer with their reasoning and bury or omit the JSON.
    The README's instruction is to treat that as 'did not bid' and count it,
    so an unparseable reply comes back as parsed=False, bid=False.
    """
    for match in _JSON_RE.finditer(text or ""):
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or "bid" not in obj:
            continue
        raw_bid = obj["bid"]
        if isinstance(raw_bid, str):
            raw_bid = raw_bid.strip().lower() in ("true", "yes", "1")
        try:
            confidence = int(float(obj.get("confidence", 0)))
        except (TypeError, ValueError):
            confidence = 0
        return Bid(name, bool(raw_bid), max(0, min(100, confidence)),
                   str(obj.get("reason", ""))[:200], raw=text)
    return Bid(name, False, 0, "unparseable reply", parsed=False, raw=text)


# ------------------------------------------------------------------ manager


@dataclass
class RunResult:
    condition: str
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    unparseable: int = 0
    no_reply: int = 0
    awards: list = field(default_factory=list)   # (task_id, gold, winner)


def announcement(task: dict) -> str:
    return ("TASK ANNOUNCEMENT %s\n%s\n\nDo you bid on this task?"
            % (task["id"], task["desc"]))


def run_contract_net(tasks, condition: str, meter: Meter, log=print) -> RunResult:
    prompts = build_prompts(condition)
    contractors = {name: Chat(prompts[name], meter) for name in ANNOUNCE_ORDER}
    result = RunResult(condition=condition, tasks=len(tasks))

    log("[condition] %s" % condition)
    log("[contractors] %s%s" % (
        ", ".join(ANNOUNCE_ORDER),
        "  (overconfident: %s)" % OVERCONFIDENT_ONE
        if condition == "overconfident" else ""))

    for task in tasks:
        text = announcement(task)
        log("")
        log("=== %s (gold=%s) ===" % (task["id"], task["gold"]))
        log("[announce -> %s] %s" % (", ".join(ANNOUNCE_ORDER), task["desc"]))
        result.messages += len(ANNOUNCE_ORDER)          # (1) one per contractor

        bids = []
        for name in ANNOUNCE_ORDER:
            try:
                reply = contractors[name].ask(text, log=log)
            except Exception as e:
                log("  [bid  <- %s] NO REPLY  %s: %s"
                    % (name, type(e).__name__, str(e)[:200]))
                bids.append(Bid(name, replied=False, parsed=False))
                result.no_reply += 1
                continue
            bid = parse_bid(name, reply)
            result.messages += 1                        # (2) one per reply
            bids.append(bid)
            if not bid.parsed:
                result.unparseable += 1
                log("  [bid  <- %s] UNPARSEABLE, counted as no bid :: %r"
                    % (name, (reply or "").strip()[:240]))
            else:
                log("  [bid  <- %s] bid=%s confidence=%d reason=%r"
                    % (name, bid.bid, bid.confidence, bid.reason))

        offers = [b for b in bids if b.bid]
        if not offers:
            result.unassigned += 1
            log("[award] none -- no contractor bid on %s" % task["id"])
            result.awards.append((task["id"], task["gold"], None))
            continue

        # highest confidence wins; ties break on announcement order
        winner = max(offers,
                     key=lambda b: (b.confidence, -ANNOUNCE_ORDER.index(b.contractor)))
        result.messages += 1                            # (3) the award
        result.awards.append((task["id"], task["gold"], winner.contractor))
        hit = winner.contractor == task["gold"]
        if hit:
            result.correct += 1
        else:
            result.misawards += 1
        log("[award -> %s] confidence=%d gold=%s -> %s"
            % (winner.contractor, winner.confidence, task["gold"],
               "CORRECT" if hit else "MISAWARD"))

    log("")
    log("[summary] tasks=%d correct=%d messages=%d unassigned=%d misawards=%d "
        "(unparseable=%d no_reply=%d model_calls=%d tokens=%d retries=%d)"
        % (result.tasks, result.correct, result.messages, result.unassigned,
           result.misawards, result.unparseable, result.no_reply,
           meter.calls, meter.tokens, meter.retries))
    return result
