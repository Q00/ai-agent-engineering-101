"""When to speak first (week-09: pro-activeness). Horvitz (1999): act unprompted only when the probability that the
audience wants the action, p, exceeds p* = C / (B + C), where B is the benefit of a wanted action and C the cost
of an unwanted one. Deterministic: the ReAct agent estimates p, this module decides.

For a buyer post on the forum:
  B = 1 + COUNT_BENEFIT
            a liked post counts toward votes (0.3); any post that is not hidden also counts toward the number of
            buyer posts (0.1), so even an unliked but useful post has value. On 2026-11-03 21:10 the median buyer
            with a deal had 1 post and we had 0, which put that 0.1 at the 0th percentile.
  C grows   with how badly our recent posts were received (dislikes, hidden posts do not count) and with how
            often we already posted (noise in other agents' context; the lecture's third discussion question)

Since 2026-11-10 the same rule covers trade actions (RL_PROACTIVE.md 3): judge() takes B and C in board score
(score_model.delta), logs {goal, p, B, C, metric} before the action runs and answers act / wait / skip.
"""
import json
import time

from . import config

WAIT_BAND = 0.1                       # RL_PROACTIVE.md 3: within this of p* the action waits for the next observation
LOG = "initiative.jsonl"

COUNT_BENEFIT = 0.5
BENEFIT = 1.0 + COUNT_BENEFIT
BASE_COST = 0.5
DISLIKE_WEIGHT = 2.0
RECENT_POST_WEIGHT = 0.25
MAX_THRESHOLD = 0.9


def speak_threshold(reception, posts_last_6h):
    """reception: Memory.my_post_reception(); returns (p_star, cost) for posting now."""
    cost = BASE_COST + DISLIKE_WEIGHT * reception.get("dislike_share", 0.25) + RECENT_POST_WEIGHT * posts_last_6h
    return min(MAX_THRESHOLD, cost / (BENEFIT + cost)), round(cost, 3)


def should_post(p_wanted, reception, posts_last_6h):
    p_star, cost = speak_threshold(reception, posts_last_6h)
    try:
        p = float(p_wanted)
    except (TypeError, ValueError):
        return False, {"p_star": round(p_star, 3), "cost": cost, "p_wanted": None}
    return p >= p_star, {"p_star": round(p_star, 3), "cost": cost, "p_wanted": round(p, 3)}


def valid_post(final, known_seqs, known_post_ids):
    """Checks a ReAct proposal before it reaches the outbox. Returns (text, refs, thread) or a reason string."""
    text = final.get("text")
    if not isinstance(text, str) or not 20 <= len(text.strip()) <= config.FORUM_MAX_CHARS:
        return "text missing or wrong length"
    refs = final.get("refs") or []
    if not isinstance(refs, list) or not refs or not all(isinstance(r, int) and r in known_seqs for r in refs):
        return "refs must be ledger seqs we hold"
    thread = final.get("thread")
    if thread is not None and str(thread) not in known_post_ids:
        return "thread is not a post we have seen"
    return text.strip(), refs[:8], (str(thread) if thread is not None else None)


# --------------------------------------------------------------------------------- any action, in board score
def decide(p, B, C, reversible=True):
    """("act" | "wait" | "skip", p*) for an unprompted action wanted with probability p, worth B board score when
    wanted and costing C when not. p* = C / (B + C). Above it the action runs; within WAIT_BAND under it the action
    waits for the next observation; lower it is dropped. An action that cannot be undone also waits within
    WAIT_BAND above p*, so it needs the margin a reversible one (a price) does not."""
    if B <= 0:
        return "skip", 1.0
    p_star = max(0.0, C) / (B + max(0.0, C))
    if p > p_star + (0.0 if reversible else WAIT_BAND):
        return "act", p_star
    return ("wait" if p >= p_star - WAIT_BAND else "skip"), p_star


def judge(goal, p, B, C, metric, reversible=True, log=True, now=None, **context):
    """decide() plus the row RL_PROACTIVE.md 3 asks for, written before the action runs. Returns the row; the
    caller acts only when row["decision"] == "act"."""
    decision, p_star = decide(p, B, C, reversible)
    row = {"ts": round(time.time() if now is None else now), "goal": goal, "p": round(float(p), 4), "B": B, "C": C,
           "p_star": round(p_star, 4), "decision": decision, "reversible": reversible, "metric": metric, **context}
    if log:
        record(row)
    return row


def refuse(goal, reason, metric, B=0.0, C=0.0, log=True, now=None, **context):
    """An action we do not take by default (paid dislike, loan, bond): only the reasoning is kept."""
    row = {"ts": round(time.time() if now is None else now), "goal": goal, "p": 0.0, "B": B, "C": C, "p_star": 1.0,
           "decision": "skip", "reversible": False, "metric": metric, "reason": reason, **context}
    if log:
        record(row)
    return row


def record(row):
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(config.LOG_DIR / LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass
