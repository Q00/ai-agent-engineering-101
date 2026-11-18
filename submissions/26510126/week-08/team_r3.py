"""Sales and buying teams with promotion (11/4: the user, after other players' team-run agents and a team with ranks).

    python team_r3.py            # prints both rosters with each member's record

Each side has a few members, each a style laid over the strategy the meta-harness sets: the buyer's members haggle
differently (how far one offer moves, when to take a held ask, where to open), the seller's members price
differently (ask and floor a few points up or down, how fast to concede). A new negotiation goes to a member drawn by
rank: the lead gets most of them, the senior some, the junior a few, so a style is tried on live talks without
betting the shop on it. Every meta-harness cycle `review` ranks the members with enough talks by their reward per
talk (buyer: surplus, list minus price, 0 when no deal; seller: margin over cost as a share of list, 0 when no deal,
the A/B test's reward), promotes the best to lead and, when the last has had a fair trial and earns well under the
lead, replaces it with a recruit: the meta-harness's proposal when it made one, else a variation of the lead's style.
The roster and every change are in team_r3.json, which the buyer and the seller read at each negotiation.
"""
import json
import os
import random
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROSTER = Path(os.environ.get("TEAM_ROSTER") or HERE / "team_r3.json")
RANK_WEIGHT = {"lead": 0.6, "senior": 0.25, "junior": 0.15}
MIN_TALKS = 20       # talks a member needs since it joined before its record counts
TRIAL = 40           # talks before the last member can be replaced
REPLACE_BELOW = 0.7  # ... when its reward per talk is under this share of the lead's
# style knobs: buyer values are absolute overrides of its plan numbers, seller values are deltas on the strategy
BOUNDS = {
    "buyer": {"MAX_RAISE": (0.2, 0.7, 0.1), "FIRM_GAP": (0.0, 0.1, 0.02)},
    "seller": {"ask_delta": (-0.1, 0.1, 0.03), "floor_delta": (-0.08, 0.08, 0.02), "step": (0.2, 0.7, 0.1)},
}
OPENINGS = ("low", "p25", "median")   # buyer: where the first offer goes (below half of list, the market's lower
                                      # quartile, just under the market median)
DEFAULT = {
    "buyer": [
        {"id": "b-balanced", "rank": "lead", "style": {"OPEN": "p25"}},
        {"id": "b-patient", "rank": "senior", "style": {"MAX_RAISE": 0.25, "FIRM_GAP": 0.02, "OPEN": "low"}},
        {"id": "b-closer", "rank": "junior", "style": {"MAX_RAISE": 0.6, "FIRM_GAP": 0.08, "OPEN": "median"}},
        {"id": "b-fast", "rank": "junior", "style": {"PLAN": 1, "MAX_RAISE": 0.4, "OPEN": "p25"}},
    ],
    "seller": [
        {"id": "s-steady", "rank": "lead", "style": {}},
        {"id": "s-premium", "rank": "senior", "style": {"ask_delta": 0.06, "floor_delta": 0.02, "step": 0.2}},
        {"id": "s-volume", "rank": "junior", "style": {"ask_delta": -0.05, "floor_delta": -0.03, "step": 0.6}},
    ],
}


def load(path=None):
    try:
        r = json.loads((path or ROSTER).read_text(encoding="utf-8"))
        if isinstance(r, dict) and r.get("buyer") and r.get("seller"):
            return r
    except (OSError, ValueError):
        pass
    now = time.time()
    return {side: [dict(m, joined=now) for m in ms] for side, ms in DEFAULT.items()} | {"changes": []}


def save(roster, path=None):
    p = path or ROSTER
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(roster, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def choose(side, path=None):
    """(member id, style) for a new negotiation, drawn by rank weight; ("", {}) when there is no roster."""
    ms = load(path).get(side) or []
    if not ms:
        return "", {}
    m = random.choices(ms, weights=[RANK_WEIGHT.get(x.get("rank"), 0.1) for x in ms])[0]
    return m["id"], dict(m.get("style") or {})


def apply_seller(s, style):
    """The strategy with a seller member's style laid over it (ask and floor moved, step replaced), kept sane."""
    s = dict(s)
    for k in ("ask_frac", "ask_frac_food"):
        s[k] = round(min(1.0, max(0.5, s[k] + style.get("ask_delta", 0))), 3)
    for k, a in (("floor_frac", "ask_frac"), ("floor_frac_food", "ask_frac_food")):
        s[k] = round(min(s[a], max(0.4, s[k] + style.get("floor_delta", 0))), 3)
    if isinstance(style.get("step"), (int, float)):
        s["step"] = style["step"]
    return s


def clamp(side, style):
    out = {}
    for k, v in (style or {}).items():
        if k == "OPEN" and side == "buyer" and v in OPENINGS:
            out[k] = v
        elif k == "PLAN" and side == "buyer":
            out[k] = 1 if v else 0
        elif k in BOUNDS[side] and isinstance(v, (int, float)) and not isinstance(v, bool):
            lo, hi, _ = BOUNDS[side][k]
            out[k] = round(min(max(v, lo), hi), 3)
    return out


def vary(side, style):
    """A recruit's style: the lead's, each knob moved one step up or down at random."""
    out = dict(style)
    for k, (lo, hi, step) in BOUNDS[side].items():
        base = out.get(k, (lo + hi) / 2)
        out[k] = round(min(max(base + random.choice((-step, step)), lo), hi), 3)
    if side == "buyer":
        out["OPEN"] = random.choice(OPENINGS)
    return out


def review(side, records, recruit=None, path=None, now=None):
    """Rank `side`'s members by reward per talk since they joined (records: id -> [(t, reward)]), promote and replace.
    Returns the list of changes made (also appended to the roster's `changes`)."""
    now = now or time.time()
    roster = load(path)
    ms = roster[side]
    rec = {m["id"]: [r for t, r in records.get(m["id"], []) if t >= m.get("joined", 0)] for m in ms}
    for m in ms:
        r = rec[m["id"]]
        m["talks"], m["reward"] = len(r), round(sum(r) / len(r), 4) if r else None
    rated = sorted([m for m in ms if m["talks"] >= MIN_TALKS], key=lambda m: -m["reward"])
    changes = []
    if len(rated) >= 2:
        order = rated + [m for m in ms if m not in rated]
        for m, rank in zip(order, ("lead", "senior", "junior", "junior", "junior")):
            if m["rank"] != rank:
                changes.append(f"{side} {m['id']}: {m['rank']} -> {rank} ({m['reward']} per talk over {m['talks']})"
                               if m in rated else f"{side} {m['id']}: {m['rank']} -> {rank} (not yet rated)")
                m["rank"] = rank
        lead, last = rated[0], rated[-1]
        if last["talks"] >= TRIAL and lead["reward"] > 0 and last["reward"] < REPLACE_BELOW * lead["reward"]:
            style = clamp(side, recruit) if recruit else vary(side, lead.get("style") or {})
            new = {"id": f"{side[0]}-r{time.strftime('%m%d%H%M', time.localtime(now))}", "rank": "junior",
                   "style": style, "joined": now}
            changes.append(f"{side} {last['id']} replaced ({last['reward']} per talk vs the lead's {lead['reward']}) "
                           f"by {new['id']} {json.dumps(style)}")
            ms[ms.index(last)] = new
    if changes:
        roster["changes"] = (roster.get("changes") or [])[-50:] + [{"t": round(now), "change": c} for c in changes]
    save(roster, path)
    return changes


def summary(path=None):
    r = load(path)
    return {side: [{k: m.get(k) for k in ("id", "rank", "style", "talks", "reward")} for m in r[side]]
            for side in ("buyer", "seller")}


if __name__ == "__main__":
    print(json.dumps(summary(), ensure_ascii=False, indent=1))
