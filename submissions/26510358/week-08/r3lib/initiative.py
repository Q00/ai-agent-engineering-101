"""When to speak first (week-09: pro-activeness). Horvitz (1999): act unprompted only when the probability that the
audience wants the action, p, exceeds p* = C / (B + C), where B is the benefit of a wanted action and C the cost
of an unwanted one. Deterministic: the ReAct agent estimates p, this module decides.

For a buyer post on the forum:
  B = 1     a liked post counts toward buyer ALL (votes 0.3, posts 0.1)
  C grows   with how badly our recent posts were received (dislikes, hidden posts do not count) and with how
            often we already posted (noise in other agents' context; the lecture's third discussion question)
"""
from . import config

BENEFIT = 1.0
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
