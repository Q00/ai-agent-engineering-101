"""The manager: an LLM, and a different one every task.

Stage 1's manager was `max()` — incorruptible but too dumb to use evidence.
Here the manager reasons, and acts only through tool calls the orchestrator
executes, so the deterministic parts stay deterministic. Which tools it is
offered is what separates the arms; the manager cannot reach a tool it was
not offered, and a call to one is refused and logged.
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import backend                     # noqa: E402

MAX_TURNS = 5                      # the negotiation cap, all actions counted

_JSON_RX = re.compile(r"\{.*\}", re.S)

DECOMPOSE_SYSTEM = """You are contractor {name}, acting as manager for one task
in a contract net. Split the task into the fewest fragments that can each be
awarded to a single contractor. A task that needs only one kind of work is one
fragment — do not split it further.

Tag every fragment with the kinds of work it needs, from exactly this list:
  calc   evaluating a numeric expression and returning the number
  write  rewriting or summarising in plain English
  code   writing a small Python function

Reply with one JSON object and nothing else:
{{"fragments": [{{"text": "the work, self-contained", "needs": ["calc"]}}]}}"""

DECIDE_SYSTEM = """You are contractor {name}, acting as manager for one fragment
in a contract net. Award it to exactly one contractor. You are eligible yourself.

One action per reply, as one JSON object and nothing else:
{tools}

You have {turns} actions left for this fragment. If you run out without
awarding, the fragment goes to nobody and the work is not done."""

TOOL_LINES = {
    "get_trajectory": '  {"action": "get_trajectory"}'
                      '   -- verified past performance of every contractor',
    "ask": '  {"action": "ask", "to": "A", "question": "..."}'
           '   -- one follow-up question',
    "award": '  {"action": "award", "to": "A", "why": "one short sentence"}',
}


def _parse(text):
    match = _JSON_RX.search(text or "")
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def decompose(manager, task, meter, log):
    """Ask the manager to split the task. Fall back to one fragment on failure.

    The manager never sees the gold element list, only the task text, so the
    split is its own.
    """
    system = DECOMPOSE_SYSTEM.format(name=manager["name"])
    reply = backend.ask(system, f"TASK: {task['desc']}", meter)
    obj = _parse(reply) or {}
    frags = []
    for item in obj.get("fragments") or []:
        text = str(item.get("text", "")).strip()
        needs = [n for n in (item.get("needs") or []) if n in ("calc", "write", "code")]
        if text and needs:
            frags.append({"text": text, "needs": needs})
    if not frags:
        log("  [decompose] unparseable or empty — falling back to one fragment")
        frags = [{"text": task["desc"], "needs": ["calc", "write", "code"]}]
    for i, f in enumerate(frags):
        log(f"  [frag {i}] needs={'+'.join(f['needs'])} :: {f['text'][:110]}")
    return frags


def decide_rule(bids, needs, record):
    """The deterministic arm: highest shrunk score wins, ties by team order."""
    if not bids:
        return None, [], 0
    skill = needs[0]
    scored = [(name, record.score(name, skill, conf)) for name, conf, _ in bids]
    winner = max(scored, key=lambda s: s[1])[0]
    return winner, scored, 0


def decide_llm(manager, fragment, bids, record, team, tools, meter, log):
    """The reasoning arm: a tool-call loop, capped at MAX_TURNS actions.

    Returns (winner or None, turns used, refused tool calls).
    """
    names = [m["name"] for m in team]
    lines = "\n".join(TOOL_LINES[t] for t in tools)
    packet = [f"FRAGMENT: {fragment['text']}", "", "BIDS"]
    for name, conf, reason in bids:
        packet.append(f"  {name}  confidence {conf:g}  \"{reason}\"")
    if not bids:
        packet.append("  (none)")
    transcript = "\n".join(packet)

    refused = 0
    for turn in range(MAX_TURNS):
        system = DECIDE_SYSTEM.format(name=manager["name"], tools=lines,
                                      turns=MAX_TURNS - turn)
        obj = _parse(backend.ask(system, transcript, meter)) or {}
        action = obj.get("action")

        if action == "award":
            who = obj.get("to")
            if who in names:
                log(f"  [mgr {manager['name']}] award -> {who} "
                    f":: {str(obj.get('why', ''))[:100]}")
                return who, turn + 1, refused
            log(f"  [mgr {manager['name']}] award to unknown {who!r} — ignored")
            transcript += f"\n\n[refused] {who!r} is not a contractor."
            refused += 1
            continue

        if action not in tools:
            log(f"  [mgr {manager['name']}] refused tool {action!r}")
            transcript += f"\n\n[refused] {action!r} is not available to you."
            refused += 1
            continue

        if action == "get_trajectory":
            block = record.table(names)
            log(f"  [mgr {manager['name']}] get_trajectory")
            transcript += "\n\n" + block

        elif action == "ask":
            who, question = obj.get("to"), str(obj.get("question", ""))
            if who not in names:
                transcript += f"\n\n[refused] {who!r} is not a contractor."
                refused += 1
                continue
            target = next(m for m in team if m["name"] == who)
            answer = backend.ask(target["system"],
                                 f"The manager asks: {question}\n"
                                 f"Answer in one short sentence.", meter)
            log(f"  [mgr {manager['name']}] ask {who}: {question[:80]}")
            log(f"  [{who} reply] {answer.strip()[:120]}")
            transcript += f"\n\n[you asked {who}] {question}\n[{who}] {answer.strip()[:400]}"

    log(f"  [mgr {manager['name']}] ran out of actions — unassigned")
    return None, MAX_TURNS, refused
