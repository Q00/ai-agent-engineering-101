"""Challenge 1: set the shop's asking prices from its own trades and its reviews. The reserves stay.

    python tune_prices.py            # reads logs/market-kit.log and .market/shops.json, writes prices.json

The seller reads prices.json at the start of every negotiation, so a new file takes effect
without restarting the kit. Each run appends what it saw and what it changed to
logs/challenge-1/tuning.log.

Shop policy (Moss & Oak): cheap, practical, safe. We accept a thinner margin for more deals
and faster, happier customers, but never sell below the reserve.

Rules, each tied to a signal:
- deal rate per item < 85%      -> lower that item's opening ask 5% (buyers walk away)
- deals land near the reserve    -> the opening ask is mostly theatre: pull it toward the
  (avg within 12% of the reserve)   reserve, halfway to reserve * 1.15
- reviews complain about slow    -> give up more per counter (fewer rounds)
  haggling ("rounds", "drawn out", "gradual", "slow", "took")
- ask never below reserve * 1.08 -> keep a little room so a counter is still possible
"""
import json
import re
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
DEFAULT = {"ask": {"lamp": 130, "chair": 234, "bookshelf": 338}, "step": [0.25, 0.35, 0.5, 0.7, 1.0]}
FAST_STEP = [0.4, 0.6, 0.8, 1.0]
SLOW = re.compile(r"(rounds|drawn out|gradual|slow|took (a while|long|several))", re.I)
SHOP_NAMES = {"moss-and-oak", "move_76"}
# Do not chase one or two reviews: act only on enough evidence, and move a little at a time.
# (The first run, before these limits, moved asks 6-11% at once.)
MIN_DEALS = 8              # deals per item since the last change
MIN_THEME = 3              # reviews sharing a complaint
MAX_MOVE = 0.05            # largest change to an ask per run


def item_of(line: str, price: int) -> str:
    m = re.search(r"deal at \d+ \((lamp|chair|bookshelf)\)", line)
    if m:
        return m.group(1)
    # Older log lines carry no item; the price ranges do not overlap (reserves 100/180/260, asks below 150/255).
    return "lamp" if price < 150 else ("chair" if price < 255 else "bookshelf")


def main():
    cur = json.loads((HERE / "prices.json").read_text(encoding="utf-8")) if (HERE / "prices.json").exists() else DEFAULT
    lines = (HERE / "logs" / "market-kit.log").read_text(encoding="utf-8", errors="ignore").splitlines()
    since = cur.get("since", "")
    deals = {k: [] for k in RESERVE}
    opened = {k: 0 for k in RESERVE}
    for ln in lines:
        if ln[:8] < since[-8:] and since:
            continue
        if m := re.search(r"\[seller\] -> deal at (\d+)", ln):
            p = int(m.group(1))
            deals[item_of(ln, p)].append(p)
        if m := re.search(r"\[seller\] task \w+ <- '.*?(lamp|chair|bookshelf)", ln, re.I):
            opened[m.group(1).lower()] += 1

    reviews = []
    shops = HERE / ".market" / "shops.json"
    if shops.exists():
        for s in json.loads(shops.read_text(encoding="utf-8")):
            if s["name"] in SHOP_NAMES:
                reviews += s["reviews"]
    slow = sum(1 for r in reviews if SLOW.search(str(r.get("review", ""))))
    stars = [r["stars"] for r in reviews if r.get("stars")]

    new = {"ask": dict(cur["ask"]), "step": list(cur["step"]), "since": datetime.now().strftime("%H:%M:%S")}
    notes = []
    for item, reserve in RESERVE.items():
        ps, n = deals[item], max(opened[item], len(deals[item]))
        ask = cur["ask"][item]
        if len(ps) < MIN_DEALS:                    # not enough evidence: one or two trades do not move prices
            notes.append(f"{item}: {len(ps)} deals since {since or 'start'}, need {MIN_DEALS}; ask stays {ask}")
        else:
            avg, rate = sum(ps) / len(ps), len(ps) / n
            notes.append(f"{item}: {len(ps)}/{n} deals ({rate:.0%}), avg {avg:.1f}, reserve {reserve}, ask {ask}")
            if avg <= reserve * 1.12:
                ask = round((ask + reserve * 1.15) / 2)
                notes.append(f"  deals land near the reserve -> ask {ask}")
            if rate < 0.85:
                ask = round(ask * 0.95)
                notes.append(f"  deal rate below 85% -> ask {ask}")
        old = cur["ask"][item]
        ask = min(max(ask, round(old * (1 - MAX_MOVE))), round(old * (1 + MAX_MOVE)))   # at most 5% per run
        new["ask"][item] = max(ask, round(reserve * 1.08))
    if slow >= MIN_THEME and new["step"] != FAST_STEP:
        new["step"] = FAST_STEP
        notes.append(f"{slow} reviews call the haggling slow -> faster concessions {FAST_STEP}")
    notes.append(f"reviews: {len(reviews)}, avg stars {sum(stars) / len(stars):.2f}" if stars else "reviews: none")

    (HERE / "prices.json").write_text(json.dumps(new, indent=1), encoding="utf-8")
    out = HERE / "logs" / "challenge-1"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "tuning.log", "a", encoding="utf-8") as f:
        f.write(f"\n== {new['since']} tune_prices\nbefore: {json.dumps({k: cur[k] for k in ('ask', 'step')})}\n")
        f.write("\n".join(notes) + f"\nafter:  {json.dumps({k: new[k] for k in ('ask', 'step')})}\n")
    print("\n".join(notes))
    print("prices.json:", json.dumps(new))


if __name__ == "__main__":
    main()
