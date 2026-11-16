"""Week 08 policy: the numbers the shop runs on, their allowed ranges, and the file they live in.

The restock hook, the seller and the forum posts read data/policy.json; only the manager writes it. Every value
has a range: the manager (opus, hourly) may move a value inside its range and at most MAX_STEP of the range per
decision, and whatever it writes is clipped here, so a bad answer cannot push the shop outside the ranges agreed
on 11-02. Posterior updates (demand, share, break rates, the price bandit) are not here; code computes them.
"""
import json
import math
import os
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("DATA_DIR", HERE / "data"))   # tests point this at a scratch folder
PATH = DATA / "policy.json"

ROUND_END = 1794927540          # 2026-11-17 23:59 KST (14:59 UTC), when Round 3 closes
MAX_STEP = 0.25                 # share of a value's range one manager decision may move it

# name: (initial, low, high). Booleans have no range and are flipped freely.
NUMBERS = {
    "refund_sd":         (2.0, 1.0, 3.0),      # cash kept out of restock: bankruptcy line + expected refunds + this many sd
    "unit_cap_frac":     (0.10, 0.05, 0.20),   # one unit's cost at most this share of the balance
    "min_margin":        (0.10, 0.05, 0.20),   # floor = cost x (1 + min_margin) + warranty premium
    "max_concessions":   (4, 2, 6),            # counters before the final price
    "c_discount":        (0.10, 0.00, 0.25),   # extra cut off the opening ask for grade C (food near its date, refurbished goods)
    "liquidate_hours":   (24, 12, 48),         # from this many hours before the end the floor falls to cost
    "half_life_h":       (24, 6, 72),          # demand memory: weight of a sale halves every this many hours
    "prior_hours":       (2.0, 0.5, 8),          # cap on the demand prior's weight in hours; the market's spread sets it below
    "mkt_floor":         (0.95, 0.93, 1.02),   # a goods A's floor as a share of the market's price for its SKU and grade
                                               # 0.95 -> 1.00 (owner, 11-16 12:16): Book 1st is margin, not revenue (we
                                               # sell A at 0.676 of list, the market 0.693); 11-15 11 h - 11-16 11 h
                                               # replay (~/wk8-test/mktbt.py): 0.97 +0.76M/h, 1.00 +1.53M/h at 9% fewer
                                               # sales. Back to 0.95 at 13:50: in 1.3 h our goods settles fell 62.9 ->
                                               # 35.7M/h (-43%) while the other shops' rose 341 -> 382M/h, A margin
                                               # +1.02 -> +1.10M/h (14.0k a deal against 9.2k), book +0.88M/h
    "mkt_ask":           (1.04, 1.00, 1.10),   # its opening ask, same base (seller.MKT_PRICING, 11-10); OWNER_FIXED for now
    "warranty_hours":    (72, 72, 72),         # goods break within 6 h of the sale (agent.md), so 6 covered every
                                               # break; a longer one costs the same and is fixed (owner, 11-06 01 h):
                                               # buyers wrote "6 h is too short for a sofa" (11-05 56 reviews), and
                                               # 25620027 sells every unit with 72 h. 168 h (01:35) was refused at
                                               # settle (claim_and_warranty_well_formed); 72 h settles for 25620027
}
# 11-03 23 h: reserve_frac, goods_cap_frac, review_minutes, scout_frac and hysteresis are gone. restock prices each
# unit by the demand posterior over the refill cycle it measures, and spends unit by unit, best value per won first,
# so a separate goods cap, a fixed coverage time, scouting and a hold band only overrode that ranking. The reserve
# is the refunds we may still owe, not a share of the balance. Old keys left in policy.json are ignored.
FLAGS = {
    "warranty_A": True, "warranty_B": True, "warranty_C": True,   # offer the warranty on goods of that grade
}
# 11-10 (owner): no warranty on goods B and C, whoever asks; A keeps it. 48 h to 11-10 14 h, our goods with a warranty:
# A 4,121 sold, margin 241M, 3.3% broke, 108M refunded (+134M); B 1,219, 63M, 11.7%, 107M (-43M); C 626, 48M, 35.5%,
# 161M (-113M): the shop's 48 h came to -36M on 4,621M revenue while yms979 made +207M on 2,462M. The market pays
# nothing for a warranty (same SKU and grade, with against without, as a share of list: A +1.0%, B 0.0%, C +1.7%).
# The 11-07 11:00 fix (C none, B on ask) was lost in the 15:47 rollback of seller.py. These flags stay off whatever
# policy.json or the manager says (load, clip), so seller, restock, forum and manager all read the same. Undo: ().
# 11-10 17:35 (owner): undone. In 1.7 h after the deploy the ledger settled 0 of the 57 C deals in our log (74 of 94
# in the 2 h before) and 21 of 29 B: a buyer does not take the receipt of a C without a warranty (the order's accept
# terms). Goods share 18-21% -> 11.5%, Book 808.3M -> 793.7M. A "deal" in negotiations.jsonl is not a settlement.
# 11-10 19:15 (owner): B only. 56.5 h to 11-10 08:30 from the ledger: B with a warranty 1,504 sold, margin 88.1M,
# refunds 122.3M (-34.2M); other shops sold 2,717 B without one at 0.650 of list against 0.647 with; in the 14:40-17:35
# test 37 of our 41 B deals without a warranty settled. C keeps its warranty (0 of 57 settled without).
OWNER_OFF = ("warranty_B",)
# Numbers that stay at their initial value whatever policy.json or the manager says (load, clip), as OWNER_OFF does for
# flags. 11-10 (owner): the market-based prices went live at 19:50; they stay at 0.98 / 1.04 until the owner has read
# their effect, then the manager takes them over. Hand over: ().
OWNER_FIXED = ("mkt_floor", "mkt_ask")


def _owner(p):
    """p with the owner's fixed flags and numbers applied."""
    return {**p, **{k: False for k in OWNER_OFF}, **{k: NUMBERS[k][0] for k in OWNER_FIXED}}
BANKRUPT_LINE = 500_000        # agent.md; inventory.json's bankrupt_line wins when present
ARMS = (0.70, 0.80, 0.90, 1.00) # opening ask as a share of the list price; the bandit picks one per negotiation
# Goods open at the floor x (1 + arm), at most the list price (owner, 11-05 00:30). With list x ARMS, the floor (cost
# x 1.08 + break premium) overrode the arm in half the goods talks (arm 0.7: 76%), so the bandit learned the floor's
# results. 11-04 07-24 h goods talks by opening premium over the floor, within floor/list bands: floor 0.70-0.78 of
# list, at the floor 41% paid deals and profit 0.042 of list a talk, 6-10% over 47% 0.083, 10-15% 47% 0.098, 15-25%
# 40% 0.122, 25%+ 20% 0.080; floor 0.78+: at it 40% 0.046, 10-15% 36% 0.082 (observational: units with room over
# their floor are cheaper ones too). A replay of the last 6 h (859 talks, the buyers' numbers as sent, which answered
# our old asks) favours the old arms (paid-deal rate 49%; +0% 39%, +4% 46%, +8% 35%, +15% 30%) and cannot judge
# demand; the two disagree, so the bandit decides, with the floor itself (0.0) among the arms. Revert if the goods
# deal rate falls over 5%p or profit per talk falls in the first hour (owner, 11-05 00:45).
# 11-05 08h the bandit had settled on 0.0 (owner: fix it). 00:22-07:08 goods talks by arm: 0.0 1,507 talks, 24.2%
# closed, profit 39.5k a talk, verified stars 4.73; 0.04 408, 19.6%, 29.8k, 4.63; 0.08 597, 19.4%, 33.7k, 4.56; 0.15
# 510, 19.2%, 40.5k, 4.45 (posterior means 0.21 / 0.185 / 0.188 / 0.178). The board's ALL weighs book 0.5, revenue
# 0.3, stars 0.2 and we lead book and revenue, so stars are where it can still rise; the other arms cost about 1M of
# margin an hour in exploration. 0.0 still follows the market: its base is max(floor, MKT_BASE x the market's price).
GOODS_ARMS = (0.0,)                     # was (0.0, 0.04, 0.08, 0.15) from 11-05 00:22


def arms(kind):
    """The bandit's arms for a kind: food a share of list (ARMS), goods a premium over the floor (GOODS_ARMS)."""
    return GOODS_ARMS if kind == "goods" else ARMS
LAST_HOURS_BELOW_COST = 6       # in the last hours the floor is the cost and restock buys nothing (no margin left to earn).
# The name is from before 11-05 03:45 KST, when the floor fell to half the cost here: the board's ALL then used cash
# and unsold stock was a total loss. Since then ALL uses book (cash + cost of unsold units), so a sale under cost
# lowers the score by the gap and an unsold unit costs only its storage (food: its cost once past its date).
# 11-13 (owner): under book a sale at cost adds nothing, a unit left over stays in the book at its cost, and a unit
# bought late pays 0.04% of its cost an hour in storage, so the slide of the last day only gave the margin away
# (and revenue weighs 0.3). Off: the floor keeps its margin to the end and restock buys until the last hour.
END_FLOOR_TO_COST = False       # True: the floor falls to the cost over the last liquidate_hours, no restock in the last 6 h
LAST_HOURS_NO_RESTOCK = 1       # with END_FLOOR_TO_COST off: restock stops this close to the end (nothing left to sell it in)


def restock_stops(left):
    """No more buying this close to the round's end."""
    return left < (LAST_HOURS_BELOW_COST if END_FLOOR_TO_COST else LAST_HOURS_NO_RESTOCK)


def defaults():
    p = {k: v[0] for k, v in NUMBERS.items()}
    p.update(FLAGS)
    p.update(updated=0, reason="initial values, 11-02", version=0)
    return p


def clip(new, old=None):
    """Keep known keys, inside their ranges and within MAX_STEP of the old value."""
    old = old or defaults()
    out = dict(old)
    for k, (_, lo, hi) in NUMBERS.items():
        if k not in new:
            continue
        try:
            v = float(new[k])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(v):     # "nan" parses as a float and would pass every comparison below
            continue
        whole = isinstance(NUMBERS[k][0], int)
        step = MAX_STEP * (hi - lo)
        if whole:                    # at least one unit, rounded half up: round() halves to even and can get stuck
            step = max(step, 1)
        v = min(max(v, old[k] - step, lo), old[k] + step, hi)
        out[k] = math.floor(v + 0.5) if whole else round(v, 4)
    for k in FLAGS:
        if isinstance(new.get(k), bool):
            out[k] = new[k]
    return _owner(out)


def load():
    try:
        p = json.loads(PATH.read_text(encoding="utf-8"))
        return _owner({**defaults(), **p})
    except (OSError, ValueError):
        return _owner(defaults())


def save(p, reason):
    p = dict(p, updated=time.time(), reason=reason, version=int(p.get("version", 0)) + 1)
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, PATH)
    return p


def hours_left(now=None):
    return max(0.0, (ROUND_END - (now or time.time())) / 3600)


def reserve(p, line, refunds_mean, refunds_sd):
    """Cash restock never spends: below the line the shop is bankrupt, and a warranty refund is paid from the
    balance whether or not the cash is there."""
    return (line or BANKRUPT_LINE) + refunds_mean + p["refund_sd"] * refunds_sd


# The playbook: what the manager may change that is not a number. Text guidance read by the seller's haiku
# (negotiation) and the forum's opus (forum), and categories restock leaves alone for a while. The code still
# decides every price, unit and receipt; guidance only steers how the models speak and choose among the code's
# options. No digits in guidance (it reaches haiku, which must not learn internal numbers).
PLAYBOOK = DATA / "playbook.json"
GUIDE_KEYS = ("negotiation", "forum")
GUIDE_CHARS = 600
# Words that would carry internal numbers without digits ("floor is cost plus a tenth"): guidance with them is refused.
INTERNAL = re.compile(r"\b(?:costs?|floors?|margins?|percent(?:age)?s?|per cent|half|halves|thirds?|quarters?|"
                      r"tenths?|fifths?|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundreds?|"
                      r"thousands?|millions?|dozens?)\b|%|원가|최저가|마진|퍼센트|할인율|만원|천원|반값|절반", re.I)
SKIP_MAX, SKIP_HOURS = 10, 24


def load_playbook():
    try:
        b = json.loads(PLAYBOOK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        b = {}
    return {"negotiation": "", "forum": "", "restock_skip": {}, "version": 0, "updated": 0, "reason": "", **b}


def skipped(b, now=None):
    """Categories restock leaves alone now (each skip lasts at most SKIP_HOURS)."""
    now = now or time.time()
    return {c for c, until in (b.get("restock_skip") or {}).items() if until > now}


def clip_playbook(new, old, categories, now=None):
    """Apply the manager's playbook edits that pass: guidance without digits and within GUIDE_CHARS, skips of known
    categories, at most SKIP_MAX, each for at most SKIP_HOURS. Returns (playbook, what was refused)."""
    now = now or time.time()
    out, refused = dict(old), []
    for k in GUIDE_KEYS:
        v = new.get(k)
        if v is None:
            continue
        v = " ".join(str(v).split())
        if any(ch.isdigit() for ch in v):
            refused.append(f"{k}: has digits")
        elif INTERNAL.search(v):
            refused.append(f"{k}: names an internal number ({INTERNAL.search(v).group()})")
        elif len(v) > GUIDE_CHARS:
            refused.append(f"{k}: longer than {GUIDE_CHARS} characters")
        else:
            out[k] = v
    skip = new.get("restock_skip")
    if isinstance(skip, dict):
        keep = {c: t for c, t in (old.get("restock_skip") or {}).items() if t > now}
        for c, hours in skip.items():
            if c not in categories:
                refused.append(f"restock_skip: unknown category {c!r}")
                continue
            try:
                h = float(hours)
            except (TypeError, ValueError):
                refused.append(f"restock_skip: {c!r} hours not a number")
                continue
            if h <= 0:
                keep.pop(c, None)
            else:
                keep[c] = now + min(h, SKIP_HOURS) * 3600
        if len(keep) > SKIP_MAX:
            refused.append(f"restock_skip: more than {SKIP_MAX}, kept the {SKIP_MAX} ending last")
            keep = dict(sorted(keep.items(), key=lambda x: -x[1])[:SKIP_MAX])
        out["restock_skip"] = {c: round(t) for c, t in keep.items()}
    return out, refused


def save_playbook(b, reason):
    b = dict(b, updated=time.time(), reason=reason, version=int(b.get("version", 0)) + 1)
    PLAYBOOK.parent.mkdir(parents=True, exist_ok=True)
    tmp = PLAYBOOK.with_suffix(".tmp")
    tmp.write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, PLAYBOOK)
    return b
