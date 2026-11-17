"""Week 07 — backfill_memory.py: turn the Round 0 trades in logs/market-kit.log into memory.jsonl lines.

    python backfill_memory.py               # appends trades not in memory.jsonl yet
    python backfill_memory.py --dry-run     # prints them only

In Round 0 the kit ran the week-06 buyer and sent no reviews, but the log has every trade: the shop
(by name), each ask, each offer, the deal message, the price on the receipt and whether it was signed.
This replays each order through market_buyer's own checks (misbehaviour, receipt vs agreed price,
facts_and_stars), so the stars and the review text follow the same rules as a live trade. The text is
the facts themselves (no harness call). Orders our own buyer broke (a harness error) or that were cut
off by a restart are skipped: they say nothing about the shop.
"""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import market_buyer as mb

LOG = Path(__file__).parent / "logs" / "market-kit.log"
SOURCE = "backfill from logs/market-kit.log (Round 0, week-06 buyer)"


def blocks(lines):
    cur = None
    for line in lines:
        if "[order " in line:
            if cur:
                yield cur
            cur = [line]
        elif cur is not None:
            cur.append(line)
            if "[kit] leaving the market" in line:
                yield cur
                cur = None
    if cur:
        yield cur


def parse(block, day):
    head = re.search(r"^(\d\d:\d\d:\d\d) \[order (\S+)\] (\w+), budget (\d+), round (\d+)", block[0])
    shop = next((re.search(r"shop for this order: (\S+)", l).group(1) for l in block if "shop for this order" in l), None)
    result = next((l for l in block if "[buyer] result:" in l), None)
    if not head or not shop or not result or any("harness error" in l for l in block):
        return None
    t, order, item, budget, rnd = head.groups()
    asks, texts, offers = [], [], []
    for l in block:
        m = re.search(r"\[buyer\] <- (TASK_STATE_\w+) \| (.*)$", l)
        if m:
            texts.append(m.group(2))
            if m.group(1) == "TASK_STATE_INPUT_REQUIRED":
                nums = re.findall(r"\d+", m.group(2))
                if nums:
                    asks.append(int(nums[-1]))
        m = re.search(r"\[buyer\] -> (\d+)\b", l)
        if m:
            offers.append(int(m.group(1)))
    ledger = next((re.search(r"ledger #(\d+)", l).group(1) for l in block if "ledger #" in l), None)
    paid = re.search(r"paid ([\d.]+)", result)
    if "buyer error" in " ".join(block):
        outcome, rec, problem = "no answer", None, "no answer (buyer error)"
    elif "TASK_STATE_COMPLETED" in result and paid:
        outcome = "deal"
        signed = any("sig_seller ok" in l for l in block)
        rec = {"item": item, "price": float(paid.group(1)), **({"sig_seller": "(verified by the kit)"} if signed else {})}
        problem = mb.check_receipt(rec, {"id": None}, item, offers[-1] if offers else None)
    else:
        outcome, rec, problem = "no deal", None, None
    issues, worst = mb.misbehaviour(item, asks, offers, texts, outcome)
    stars, facts = mb.facts_and_stars(outcome, item, asks, offers, rec, problem, issues, worst)
    ts = datetime.combine(day, datetime.strptime(t, "%H:%M:%S").time()).timestamp()
    return {"ts": ts, "order": order, "ledger": ledger, "shop": shop, "item": item, "budget": int(budget),
            "first_ask": asks[0] if asks else None, "last_ask": asks[-1] if asks else None,
            "last_offer": offers[-1] if offers else None,
            "agreed": offers[-1] if outcome == "deal" and offers else None,
            "receipt_price": rec["price"] if rec else None, "outcome": outcome,
            "signed": bool(rec and rec.get("sig_seller")), "stars": stars,
            "problem": problem or (f"misbehaved ({worst}): " + "; ".join(issues) if issues else None),
            "review": facts, "round": int(rnd), "source": SOURCE}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--day", default=datetime.fromtimestamp(LOG.stat().st_mtime).date().isoformat())
    a = ap.parse_args()
    day = datetime.fromisoformat(a.day).date()
    memory = mb.load_memory()
    known_orders = {m.get("order") for m in memory}
    known_ledger = {"2508"} | {m.get("ledger") for m in memory}   # 2508: the maple-home line seeded by hand
    new = []
    for b in blocks(LOG.read_text(encoding="utf-8").splitlines()):
        e = parse(b, day)
        if e and e["order"] not in known_orders and e["ledger"] not in known_ledger:
            new.append(e)
            known_orders.add(e["order"])
    for e in new:
        print(f"{e['stars']}* {e['shop']:15} {e['item']:9} {e['outcome']:8} {e['review']}")
        if not a.dry_run:
            mb.remember({k: v for k, v in e.items()})
    print(f"{len(new)} trades {'found' if a.dry_run else 'added to ' + mb.MEMORY.name}")


if __name__ == "__main__":
    main()
