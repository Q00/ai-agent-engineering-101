"""Challenge 1: change the shop's asking prices from its own reviews and trades, one fix at a time.

    py challenge1_tune.py            # measure, ask haiku for one change, apply it to inventory.json
    py challenge1_tune.py --measure  # measure only (the "after" numbers of the last change)

Each run:
1. snapshots the board (my deals, share, visits, stars) and the market's total deal count;
2. sums up my shop's trades in trades/seller.jsonl since the last change (or since the last
   market test, whose trades are not market trades): outcomes, average price per item, turns;
3. (not with --measure) shows haiku the reviews of my shop and those numbers, and asks it what
   buyers praise (to keep) and which fixable problem to fix next, as ONE list-price change;
4. code checks the change: one item, the reserve stays, the new list price is at or above the
   reserve and within 15% of the current one. Then it writes inventory.json; seller.py reads the
   new list price on its next deal (no restart).
Everything goes to logs/challenge-1/: snapshot-<time>.json per run, changes.jsonl per change.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

import httpx

import react
from model import call_model, log

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "logs", "challenge-1")
INVENTORY = os.path.join(HERE, "inventory.json")
TRADES = os.path.join(HERE, "trades", "seller.jsonl")
CHANGES = os.path.join(OUT, "changes.jsonl")
MARKET = "https://market.ourolabs.ai"
NICK = "김관우"
MAX_STEP = 0.15  # one change moves a list price by at most 15%
MIN_NEGOTIATIONS = 20  # fewer trades since the last change: measure only (the first change used 9)
MIN_ITEM_NEGOTIATIONS = 5  # an item with fewer trades in the window cannot be changed (that change used 1)

SYSTEM = """You run the pricing of a small furniture shop in an open agent market.
Buyer agents pick shops from reviews and their own memory; the shop's share is how many deals it gets.
You read what buyers wrote about the shop and how its recent negotiations ended.

1. List what buyers praise. Those things must stay as they are.
2. List the problems you can see that the shop can fix by changing an asking (list) price.
   The reserve (the lowest price) never changes. Ignore problems a price cannot fix
   (for example a buyer whose budget is below the reserve).
3. Choose ONE fix: one item and its new list price. Prefer the problem that loses the most deals
   or money. Do not undo what buyers praise (fair prices, matching receipts)."""


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def board():
    b = httpx.get(f"{MARKET}/api/board", timeout=30).json()
    me = next((s for s in b["shops"] if s["name"] == NICK), {})
    shops = httpx.get(f"{MARKET}/api/shops", timeout=30).json()
    reviews = next((s.get("reviews", []) for s in shops["shops"] if s["name"] == NICK), [])
    feed = [{"stars": r["stars"], "review": r["review"], "author": r.get("author")}
            for r in b.get("reviews", []) if r.get("shop") == NICK]
    ranked = sorted(b["shops"], key=lambda s: -s.get("share", 0))
    return {"time": datetime.now().isoformat(timespec="seconds"),
            "me": {k: me.get(k) for k in ("deals", "share", "visits", "stars", "reviews", "revenue", "profit")},
            "rank": next((i + 1 for i, s in enumerate(ranked) if s["name"] == NICK), None), "shops": len(ranked),
            "market_deals": b.get("totals", {}).get("deals"), "my_reviews": reviews, "feed_reviews": feed}


def window_start():
    """The last change, or the end of the last market test (its trades are not market trades)."""
    changes = load_jsonl(CHANGES)
    if changes:
        return changes[-1]["time"].replace("T", " ")
    report = os.path.join(HERE, "test-report.json")
    return datetime.fromtimestamp(os.path.getmtime(report)).strftime("%Y-%m-%d %H:%M:%S")


def trade_stats(since):
    recs = [r for r in load_jsonl(TRADES) if r.get("time", "") > since]
    stats = {"since": since, "negotiations": len(recs), "outcomes": {}, "items": {}}
    for r in recs:
        stats["outcomes"][r["outcome"]] = stats["outcomes"].get(r["outcome"], 0) + 1
        it = stats["items"].setdefault(r["item"], {"negotiations": 0, "deals": 0, "prices": [], "turns": [],
                                                   "out_of_stock": 0, "left_after_first_ask": 0})
        it["negotiations"] += 1
        text = " ".join(t.get("text", "") for t in r.get("turns", []))
        if "out of stock" in text:
            it["out_of_stock"] += 1
        buyer_prices = [t for t in r.get("turns", []) if t.get("from") == "buyer" and t.get("price") is not None]
        if r["outcome"] == "canceled" and not buyer_prices:
            it["left_after_first_ask"] += 1
        if r["outcome"] == "completed":
            it["deals"] += 1
            it["prices"].append(r["final_price"])
            it["turns"].append(len(r.get("turns", [])))
    for it in stats["items"].values():
        p, t = it.pop("prices"), it.pop("turns")
        it["avg_price"] = round(sum(p) / len(p), 1) if p else None
        it["min_price"], it["max_price"] = (min(p), max(p)) if p else (None, None)
        it["avg_turns"] = round(sum(t) / len(t), 1) if t else None
    return stats


def share_since(prev, now):
    """My share of the market's deals between two snapshots."""
    if not prev or prev.get("market_deals") is None or now.get("market_deals") is None:
        return None
    total = now["market_deals"] - prev["market_deals"]
    mine = (now["me"].get("deals") or 0) - (prev["me"].get("deals") or 0)
    return {"my_deals": mine, "market_deals": total, "share": round(mine / total, 5) if total else None}


def ask_haiku(inv, snap, stats):
    reviews = snap["my_reviews"] + [r for r in snap["feed_reviews"] if r not in snap["my_reviews"]]
    lines = [f"Shop on the board: {snap['me']}, rank {snap['rank']} of {snap['shops']} by share.",
             "Reviews buyers wrote about this shop (newest first):"]
    lines += [f"  - {r['stars']}* {r['review']}" for r in reviews] or ["  (none)"]
    lines.append(f"\nThis shop's negotiations since {stats['since']}: {stats['negotiations']}, "
                 f"outcomes {stats['outcomes']}.")
    for name, info in inv.items():
        mid = round((info["list_price"] + info["reserve"]) / 2)
        lines.append(f"  {name}: list price {info['list_price']}, middle {mid}, reserve {info['reserve']} (fixed); "
                     f"trades: {stats['items'].get(name, 'none')}")
    lines.append("\nThe shop's sales agent opens at the list price, then counters at the middle price, "
                 "then at the reserve, and accepts any offer at or above the reserve.")
    for c in load_jsonl(CHANGES)[-3:]:
        lines.append(f"Earlier change ({c['time']}): {c['item']} list price {c['old']} -> {c['new']}: {c['why']}")
    finals = {"change": {"args": "(item, list_price, keep, problems)",
                         "description": "keep: list of praised points to keep; problems: list of fixable problems "
                                        "you saw; item and list_price: the one fix"}}

    def validate(action, args):
        item = args.get("item")
        if item not in inv:
            return False, f"item must be one of {list(inv)}"
        try:
            new = int(args["list_price"])
        except (KeyError, TypeError, ValueError):
            return False, "list_price must be a whole number"
        old, reserve = inv[item]["list_price"], inv[item]["reserve"]
        if new == old:
            return False, f"{item} is already at {old}; pick a change"
        seen = stats["items"].get(item, {}).get("negotiations", 0)
        if seen < MIN_ITEM_NEGOTIATIONS:
            return False, f"only {seen} {item} negotiation(s) since the last change; pick an item with at least " \
                          f"{MIN_ITEM_NEGOTIATIONS}"
        if new < reserve:
            return False, f"{new} is below the reserve {reserve}"
        if abs(new - old) > MAX_STEP * old:
            return False, f"one change may move the list price by at most {int(MAX_STEP * 100)}% ({old} -> " \
                          f"{round(old * (1 - MAX_STEP))}..{round(old * (1 + MAX_STEP))})"
        return True, "ok"

    slow = lambda prompt, system=None, timeout=None: call_model(prompt, system=system, timeout=90)
    d, trace = react.run(SYSTEM, "\n".join(lines), {}, finals, validate, max_steps=3,
                         deadline=time.time() + 300, model=slow)
    return d, "\n".join(lines), trace


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--measure", action="store_true", help="snapshot and stats only, no change")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    inv = json.load(open(INVENTORY, encoding="utf-8"))
    snap = board()
    stats = trade_stats(window_start())
    changes = load_jsonl(CHANGES)
    prev = changes[-1]["snapshot"] if changes else None
    result = {"snapshot": snap, "trades": stats, "share_since_last_change": share_since(prev, snap),
              "inventory": inv}
    stamp = datetime.now().strftime("%m%d-%H%M")
    print(json.dumps({k: v for k, v in result.items() if k != "snapshot"} | {"board": snap["me"]},
                     ensure_ascii=False, indent=1))
    if not args.measure and stats["negotiations"] < MIN_NEGOTIATIONS:
        log(f"only {stats['negotiations']} negotiations since {stats['since']} (need {MIN_NEGOTIATIONS}); measuring only")
        args.measure = True
    if args.measure:
        json.dump(result, open(os.path.join(OUT, f"snapshot-{stamp}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        return

    d, situation, trace = ask_haiku(inv, snap, stats)
    result["situation"], result["trace"] = situation, trace
    json.dump(result, open(os.path.join(OUT, f"snapshot-{stamp}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    if not d:
        log("haiku gave no valid change; nothing applied")
        sys.exit(1)
    item, new = d["item"], int(d["list_price"])
    change = {"time": snap["time"], "item": item, "old": inv[item]["list_price"], "new": new,
              "keep": d.get("keep"), "problems": d.get("problems"), "why": d.get("thought", ""),
              "trades_before": stats, "snapshot": {k: snap[k] for k in ("time", "me", "rank", "shops", "market_deals")}}
    inv[item]["list_price"] = new
    with open(INVENTORY, "w", encoding="utf-8") as f:
        f.write("{\n" + ",\n".join(f'  {json.dumps(k)}: {json.dumps(v)}' for k, v in inv.items()) + "\n}\n")
    with open(CHANGES, "a", encoding="utf-8") as f:
        f.write(json.dumps(change, ensure_ascii=False) + "\n")
    print(f"\nchanged {item} list price {change['old']} -> {new}\n  keep: {change['keep']}\n"
          f"  problems: {change['problems']}\n  why: {change['why']}")


if __name__ == "__main__":
    main()
