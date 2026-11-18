"""What if the shop had priced differently? Replays our own negotiations under other strategies (no model calls).

    python simulate_r3.py               # the last 24 hours of logs/talks_r3.jsonl under the current strategy and
                                        # each playbook (playbooks_r3.py)

Each line of logs/talks_r3.jsonl is one negotiation as seller_r3.py saw it: the unit's list price and cost, our first
ask and floor, every offer the buyer sent, and how it ended. The replay runs the seller's own rules (ask, floor,
counter by `step`, accept at or above the floor after two offers or at the midpoint, walk after max_turns) against
those same offers under another strategy, and adds up deals, revenue and margin over cost.

What it cannot know, so read it as a first guess for the A/B test, not a verdict:
  - a buyer answers our counteroffers; here its offers are fixed. Where it closed early with us, its later offers
    are unknown, so a stricter strategy that would not have closed by then counts the talk as lost (this is
    pessimistic about holding firm), and a buyer that would have paid more for a firmer shop is never seen;
  - warranties (the break rate is hidden) and freshness pricing are left out; clearance floors are kept as logged.
`fidelity` is the share of talks where replaying what we actually did gives the outcome that really happened: the
lower it is, the less the other rows mean. Talks where the seller's model made the calls (model_on) cannot be
replayed as what we did, so fidelity leaves them out; their offers still count for the other strategies.
`model_vs_rule` compares the two halves of the seller's own split (model_share): talks, deals, and margin over cost
per talk, a lost sale counting 0.
"""
import json
import os
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TALKS = Path(os.environ.get("TALKS_LOG") or HERE / "logs" / "talks_r3.jsonl")


def talks(hours=24, now=None):
    now = now or time.time()
    out = []
    try:
        lines = TALKS.read_text(encoding="utf-8").splitlines()[-20000:]
    except OSError:
        return out
    for line in lines:
        try:
            t = json.loads(line)
        except ValueError:
            continue
        if now - t.get("t", 0) < hours * 3600 and t.get("offers") is not None and t.get("list_price"):
            out.append(t)
    return out


def replay(t, s=None):
    """(price or None) for one talk: under strategy s, or under what we actually used (logged ask and floor)."""
    lp, cost, food = t["list_price"], t["cost"], t.get("kind") == "food"
    if s is not None and not food and t.get("grade") in ("B", "C"):   # goods priced by grade (seller_r3.grade_frac)
        lp = lp * s.get(f"grade_frac_{t['grade'].lower()}", 1.0)
    if s is None or t.get("clear"):
        floor = t["floor"]
        ask = t["ask0"] if s is None else max(lp * min(s["ask_frac_food" if food else "ask_frac"], 1.0), floor)
    else:
        ask_frac = s["ask_frac_food"] if food else s["ask_frac"]
        floor = max(cost * (1 + s["min_margin"]), lp * min(s["floor_frac_food"] if food else s["floor_frac"], ask_frac))
        ask = max(lp * min(ask_frac, 1.0), floor)
    ask = ask if ask is not None else lp
    step = (s or {}).get("step", 0.4)
    max_turns = (s or {}).get("max_turns", len(t["offers"]) or 1)
    for i, o in enumerate(t["offers"][:max_turns]):
        if o is None:
            continue
        mid = (ask + floor) / 2
        if o >= floor and (o >= ask or i + 1 >= 2 or o >= mid):
            return min(o, ask)
        if o < ask:
            ask = max(floor, ask - step * (ask - o))
    return None


def score(rows, s=None):
    prices = [(replay(t, s), t) for t in rows]
    sold = [(p, t) for p, t in prices if p is not None]
    return {"deals": len(sold), "revenue": int(sum(p for p, _ in sold)), "margin": int(sum(p - t["cost"] for p, t in sold))}


def model_vs_rule(hours=24):
    """The seller's own split: negotiations the model haggled against those the rules haggled, same hours."""
    out = {}
    for t in talks(hours):
        k = "model" if t.get("model_on") else "rule"
        o = out.setdefault(k, {"talks": 0, "deals": 0, "margin": 0, "list": 0})
        o["talks"] += 1
        o["list"] += t["list_price"]
        if t.get("event") == "deal" and t.get("price") is not None:
            o["deals"] += 1
            o["margin"] += t["price"] - t["cost"]
    for o in out.values():
        o["margin_per_talk"] = round(o["margin"] / o["talks"])
        o["margin_of_list"] = round(o.pop("margin") / o.pop("list"), 3) if o["list"] else 0
    return out


def run(strategy, bounds, hours=24):
    """Our talks of the last `hours` under what we did, the current strategy and each playbook."""
    import playbooks_r3
    rows = talks(hours)
    if not rows:
        return {}
    real = [t.get("price") if t.get("event") == "deal" else None for t in rows]
    ruled = [(a, replay(t)) for a, t in zip(real, rows) if not t.get("model_on")]
    same = sum((a is None) == (b is None) and (a is None or abs(a - b) <= 1) for a, b in ruled)
    sold = [(p, t) for p, t in zip(real, rows) if p is not None]
    out = {"talks": len(rows), "hours": hours, "fidelity": round(same / len(ruled), 2) if ruled else None,
           "actual": {"deals": len(sold), "revenue": int(sum(p for p, _ in sold)), "margin": int(sum(p - t["cost"] for p, t in sold))},
           "current": score(rows, strategy)}
    for name in playbooks_r3.PLAYBOOKS:
        out[name] = score(rows, playbooks_r3.apply(name, strategy, bounds))
    return out


if __name__ == "__main__":
    import meta_r3
    print(json.dumps(run(meta_r3.current(), meta_r3.BOUNDS), ensure_ascii=False, indent=1))
