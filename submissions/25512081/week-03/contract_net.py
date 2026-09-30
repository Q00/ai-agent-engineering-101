"""Contract net for week-03 — one manager, three LLM contractors.

Domain: a voice-assistant skill router. Three contractors (weather / music /
home) bid on each user utterance; the manager awards the utterance to one of
them. gold in tasks.json is the skill that *should* win — the manager never
sees it, only the scorer does.

Protocol (FIPA Contract Net, fipa00029, mapped to this lab):
  cfp / announcement        manager sends {id, desc} to every contractor
  propose / refuse          contractor replies with a bid (participate y/n)
  not-understood            unparseable reply -> counted as "no bid"
  accept / reject           manager awards to the best participant

The three conditions change ONLY the contractor system prompts. The task set,
the model, the temperature, the announcement text and the award rule are all
held constant, so any change in the metrics is attributable to the prompts.
"""
import json
import re
from dataclasses import dataclass

import llm

# ---- the three contractors, by name. gold values in tasks.json use these. ----
CONTRACTOR_NAMES = ["weather", "music", "home"]

# In the overconfident condition, this one contractor is told to bid on
# everything. Fixed and named here so the run is reproducible.
OVERCONFIDENT = "music"

# Specialist personas (baseline). Kept parallel so only the skill differs.
_SPECIALIST = {
    "weather": "You are the WEATHER skill of a voice assistant. Your area is "
               "forecasts, current temperature, rain and other weather. You do "
               "NOT play music and you do NOT control home devices.",
    "music": "You are the MUSIC skill of a voice assistant. Your area is "
             "playing songs and playlists, skipping tracks and playback volume. "
             "You do NOT answer weather questions and you do NOT control home devices.",
    "home": "You are the HOME-CONTROL skill of a voice assistant. Your area is "
            "lights, thermostat, door locks and appliances. You do NOT answer "
            "weather questions and you do NOT play music.",
}

# The generalist persona used by ALL three in the homogeneous condition.
_GENERALIST = ("You are a general-purpose voice assistant. You can handle any "
               "everyday request — weather, music, home devices and more.")

# Appended to a specialist to make it overconfident.
_OVERCONFIDENT_SUFFIX = (" You are extremely eager: bid to participate on EVERY "
                         "task with high confidence (0.9 or above), even when the "
                         "task is clearly outside your area.")

# Output-format block — IDENTICAL in every condition, so it is never the thing
# that varies. Only the persona above changes.
_BID_FORMAT = (
    "\n\nYou are one bidder in a contract net. Decide whether this task belongs "
    "to you. Answer with ONE JSON object and nothing else:\n"
    '{"participate": true or false, "confidence": <number 0..1>, "reason": "<one short sentence>"}\n'
    "confidence is how sure you are that YOU are the right handler for this task.")


def build_contractors(condition):
    """Return [(name, system_prompt, kind)] for a condition. `kind` is only used
    by the --dry stub; the model never sees it."""
    out = []
    for name in CONTRACTOR_NAMES:
        if condition == "baseline":
            persona, kind = _SPECIALIST[name], name
        elif condition == "homogeneous":
            persona, kind = _GENERALIST, "general"
        elif condition == "overconfident":
            persona, kind = _SPECIALIST[name], name
            if name == OVERCONFIDENT:
                persona, kind = persona + _OVERCONFIDENT_SUFFIX, "overconfident"
        else:
            raise ValueError(f"unknown condition: {condition}")
        out.append((name, persona + _BID_FORMAT, kind))
    return out


# ---------------------------------------------------------------- the bid ----
@dataclass
class Bid:
    contractor: str
    participate: bool
    confidence: float
    reason: str
    parse_ok: bool          # False = unparseable reply = "not-understood" = no bid


_JSON = re.compile(r"\{.*\}", re.DOTALL)


def parse_bid(name, text):
    """Extract the JSON bid from a reply. Any failure -> parse_ok=False (no bid)."""
    m = _JSON.search(text or "")
    if not m:
        return Bid(name, False, 0.0, "", parse_ok=False)
    try:
        obj = json.loads(m.group(0))
        participate = bool(obj["participate"])
        confidence = max(0.0, min(1.0, float(obj.get("confidence", 0.0))))
        reason = str(obj.get("reason", ""))[:200]
        return Bid(name, participate, confidence, reason, parse_ok=True)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return Bid(name, False, 0.0, "", parse_ok=False)


def _dry_reply(kind, task):
    """Offline stub so the manager/metrics plumbing can be tested with no API
    key. It returns what each condition is *supposed* to produce."""
    gold = task["gold"]
    if kind == "overconfident":
        p, c = True, 0.99
    elif kind == "general":                 # all three identical -> tie
        p, c = True, 0.50
    elif kind == gold:                       # the matching specialist
        p, c = True, 0.95
    else:                                    # a specialist, wrong task
        p, c = False, 0.10
    return json.dumps({"participate": p, "confidence": c, "reason": f"dry:{kind}"})


def collect_bid(name, system, kind, task, meter, dry, log):
    announcement = f'Task {task["id"]}: {task["desc"]}'   # gold is NOT sent
    if dry:
        text = _dry_reply(kind, task)
        meter.add(0, 0)
    else:
        try:
            text = llm.complete(system, announcement, meter)
        except Exception as e:                 # API/rate-limit failure = no bid
            log(f"    bid[{name}] no-bid(api-error): {type(e).__name__}: {e}")
            return Bid(name, False, 0.0, "", parse_ok=False)
    bid = parse_bid(name, text)
    tag = "no-bid(unparseable)" if not bid.parse_ok else (
        f"participate={bid.participate} conf={bid.confidence:.2f} :: {bid.reason}")
    log(f"    bid[{name}] {tag}")
    return bid


# ---------------------------------------------------------------- the manager ----
def award(bids):
    """Best participant wins. Tie on confidence -> lower name (ascending).
    No participant -> None (unassigned)."""
    participants = [b for b in bids if b.parse_ok and b.participate]
    if not participants:
        return None
    participants.sort(key=lambda b: (-b.confidence, b.contractor))
    return participants[0].contractor


def run_condition(condition, tasks, dry=False, log=print):
    """One run = one pass over every task under one condition.
    Returns the results.csv counts for the run."""
    contractors = build_contractors(condition)
    meter = llm.Meter()
    correct = misawards = unassigned = messages = parse_fail = 0

    log(f"=== condition={condition}  contractors={[c[0] for c in contractors]} ===")
    for task in tasks:
        log(f'  announce {task["id"]}: {task["desc"]}')
        messages += len(contractors)                       # one cfp per contractor
        bids = [collect_bid(n, s, k, task, meter, dry, log) for (n, s, k) in contractors]
        messages += sum(1 for b in bids if b.parse_ok)     # one message per real bid
        parse_fail += sum(1 for b in bids if not b.parse_ok)

        winner = award(bids)
        if winner is None:
            unassigned += 1
            log(f'  award {task["id"]}: UNASSIGNED (gold={task["gold"]})')
        else:
            messages += 1                                  # accept-proposal
            if winner == task["gold"]:
                correct += 1
            else:
                misawards += 1
            log(f'  award {task["id"]}: {winner} (gold={task["gold"]}) '
                f'{"OK" if winner == task["gold"] else "MISAWARD"}')

    note = "ok" if parse_fail == 0 else f"{parse_fail} unparseable reply(ies)"
    counts = dict(tasks=len(tasks), correct=correct, messages=messages,
                  unassigned=unassigned, misawards=misawards, note=note)
    log(f"  -> {counts}  (tokens={meter.tokens}, calls={meter.calls})")
    return counts


if __name__ == "__main__":
    # Dry smoke test of the plumbing (no API key needed).
    with open("tasks.json", encoding="utf-8") as f:
        tasks = json.load(f)
    for cond in ("baseline", "homogeneous", "overconfident"):
        run_condition(cond, tasks, dry=True)
        print()
