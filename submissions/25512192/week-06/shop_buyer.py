"""Challenge 2: one buyer, a directory of ~20 shops, a shopping list, one total budget.

    python shop_buyer.py --directory directory.json --list shopping_list.json --budget 1200

1. Read every Agent Card in the directory (the DF / yellow pages) and squeeze each into one line.
2. PLAN (harness): for each list entry, which shops to try in which order, and a cap per entry.
   The code scales the caps down if they add up to more than the budget.
3. Negotiate all entries at once, one thread per entry, one shop at a time per entry
   (buyer.negotiate, the same loop the trade test runs). An entry stops at its first deal,
   so nothing is bought twice; every deal is at or below its cap, so the total stays in budget.
4. REPLAN (harness) for entries still open: it sees the money left and one summary line per
   finished negotiation, never the transcripts. Up to MAX_PASSES passes.

State the buyer carries per entry: status, shop, price, and the notes lines (one per round).
"""
import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import harness
from a2a_client import Shop
from buyer import log, negotiate

MAX_PASSES = 3
SYSTEM = ("You are the planning brain of a buyer agent that shops at many shops at once. "
          "Answer with exactly one JSON object and nothing else.")
PLAN = """Shopping list (entry: what to buy):
{entries}

Money left: {money}. Every entry must be bought, and the total must stay within the money left.

Shop directory (id | name | what the card says):
{shops}

{past}For each open entry, choose up to 3 shops that sell that kind of item, best first (cheaper shops
first), and a cap: the most you are willing to pay for that entry. The caps of the open entries must add
up to at most {money}; give expensive kinds of furniture a larger share.
Answer: {{"plan": [{{"entry": <number>, "shops": ["<id>", ...], "cap": N}}, ...]}}"""


def card_line(sid, shop):
    skills = " ".join(s.get("description", "") for s in shop.card.get("skills", []))
    return f"{sid} | {shop.name} | {shop.card.get('description', '')} {skills.split('. Name or')[0]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--directory", required=True)
    ap.add_argument("--list", required=True)
    ap.add_argument("--budget", type=int, required=True)
    args = ap.parse_args()
    urls = json.load(open(args.directory, encoding="utf-8"))
    wanted = json.load(open(args.list, encoding="utf-8"))
    t0 = time.time()

    shops = {}
    for k, url in enumerate(urls, 1):
        try:
            shops[f"S{k:02d}"] = Shop(url)
        except Exception as e:                     # a shop that is down is skipped, not fatal
            log(f"[dir] {url}: card unreadable ({e})")
    directory = "\n".join(card_line(sid, s) for sid, s in shops.items())
    log(f"[dir] {len(shops)} cards read; directory for the planner is {len(directory)} chars:\n{directory}")

    ledger = {n: {"want": w, "status": "open", "notes": []} for n, w in enumerate(wanted, 1)}
    lock = threading.Lock()

    def spent():
        return sum(e["price"] for e in ledger.values() if e["status"] == "bought")

    def run_entry(n, shop_ids, cap):
        e = ledger[n]
        for sid in shop_ids:
            if sid not in shops:
                e["notes"].append(f"{sid}: not in directory")
                continue
            res = negotiate(shops[sid], e["want"], e["want"], cap, log, tag=f"entry{n}@{sid}",
                            notes=lambda line: e["notes"].append(line))
            if res["deal"]:
                with lock:
                    e.update(status="bought", shop=sid, price=res["price"], receipt=res["receipt"])
                log(f"[entry{n}] BOUGHT at {sid} for {res['price']}: {res['receipt']}")
                return
        log(f"[entry{n}] not bought this pass (cap {cap})")

    for p in range(1, MAX_PASSES + 1):
        open_ = {n: e for n, e in ledger.items() if e["status"] == "open"}
        if not open_:
            break
        money = args.budget - spent()
        past = ""
        if p > 1:
            past = "What happened so far (one line per negotiation round):\n" + "\n".join(
                f"entry {n}: {e['status']}" + (f" at {e['shop']} for {e['price']}" if e["status"] == "bought" else "")
                + "".join(f"\n  - {line}" for line in e["notes"][-8:]) for n, e in ledger.items()) + "\n\n"
        prompt = PLAN.format(entries="\n".join(f"{n}: {e['want']}" for n, e in open_.items()), money=money,
                             shops=directory, past=past)
        log(f"[plan {p}] prompt {len(prompt)} chars")
        plan = (harness.ask(SYSTEM, prompt, log, f"plan {p}") or {}).get("plan", [])
        plan = {int(x["entry"]): x for x in plan if isinstance(x, dict) and int(x.get("entry", 0)) in open_}
        caps = {n: max(0, int(plan.get(n, {}).get("cap") or 0)) for n in open_}
        if sum(caps.values()) > money:             # GUARD: the caps of open entries never exceed the money left
            k = money / sum(caps.values())
            log(f"[plan {p}] GUARD caps {caps} add up to {sum(caps.values())} > {money}; scaling by {k:.2f}")
            caps = {n: int(c * k) for n, c in caps.items()}
        log(f"[plan {p}] money left {money}; plan " + json.dumps(
            {n: {"shops": plan.get(n, {}).get("shops", []), "cap": caps[n]} for n in open_}))
        with ThreadPoolExecutor(len(open_)) as pool:
            for n in open_:
                pool.submit(run_entry, n, list(plan.get(n, {}).get("shops", []))[:3], caps[n])

    bought = {n: e for n, e in ledger.items() if e["status"] == "bought"}
    log("\nLEDGER")
    for n, e in ledger.items():
        log(f"  {n}. {e['want']!r}: {e['status']}" + (f" at {shops[e['shop']].name} ({e['shop']}) for {e['price']}, "
                                                     f"item {e['receipt'].get('item')} {e['receipt'].get('name')}"
                                                     if e["status"] == "bought" else ""))
    total = spent()
    ok = len(bought) == len(wanted) and total <= args.budget
    log(f"RESULT bought {len(bought)}/{len(wanted)}, total {total} of budget {args.budget}, "
        f"{'PASS' if ok else 'FAIL'}; harness calls {harness.CALLS}, {time.time() - t0:.0f} s")
    print(json.dumps({"ledger": ledger, "total": total, "pass": ok}, default=str), flush=True)


if __name__ == "__main__":
    main()
