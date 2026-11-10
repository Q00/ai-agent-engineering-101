"""Week 06 challenge 2 -- a buyer that shops a list across many shops.

Given a directory of ~20 real Agent Cards, a shopping list, and one total
budget: for each item it reads the shops' skill descriptions and asks the
harness which 2-3 shops look likely to sell it, negotiates with ALL of those
candidates AT ONCE (asyncio.to_thread around buyer.py's existing synchronous
negotiate_one -- the simplest way to get real concurrency without rewriting
the A2A client as async), and keeps the cheapest deal that actually closed.

State carried forward between items is deliberately thin: not the raw
transcript of past negotiations, just a one-line summary per finished
purchase ("bought lamp from Shop 03 at 105") and the remaining budget number
-- that's enough for the next item's negotiation and keeps the harness
prompts from growing with every shop.

    python shopping_buyer.py --directory directory.json --list shopping_list.json --budget 600
"""
import argparse
import asyncio
import json
import re

from buyer import negotiate_one
from harness import call_harness

CANDIDATES_PER_ITEM = 3


def load_directory(path: str) -> list[dict]:
    cards = json.load(open(path, encoding="utf-8"))
    shops = []
    for c in cards:
        iface = (c.get("supportedInterfaces") or [{}])[0]
        skills = c.get("skills") or []
        desc = "; ".join(s.get("description", "") for s in skills)
        shops.append({"name": c["name"], "url": iface.get("url"), "skill_description": desc})
    return shops


def ask_harness_candidates(item: str, shops: list[dict]) -> list[str]:
    listing = "\n".join(f"  - {s['name']}: {s['skill_description']}" for s in shops)
    prompt = (
        f'A buyer is looking for: "{item}".\n\n'
        f"Here is a directory of shops and what their own skill description says they sell:\n{listing}\n\n"
        f"List up to {CANDIDATES_PER_ITEM} shop names (exact names, comma-separated, best guess "
        "first) that are likely to sell this item.\n"
        "Respond with EXACTLY one line:\nCANDIDATES <name>, <name>, ...\n\n"
        "If none look likely, respond with exactly:\nNONE"
    )
    reply = call_harness(prompt)
    m = re.search(r"CANDIDATES\s+(.+)", reply)
    if not m:
        return []
    said = [n.strip() for n in m.group(1).split(",")]
    valid = {s["name"] for s in shops}
    resolved = []
    for n in said:
        if n in valid:
            resolved.append(n)
            continue
        # guard: the harness sometimes drops a trailing "(item, item)" from the full name
        matches = [v for v in valid if v.startswith(n)]
        if len(matches) == 1:
            resolved.append(matches[0])
    return resolved[:CANDIDATES_PER_ITEM]


async def shop_for_item(item: str, shops: list[dict], remaining_budget: int) -> dict:
    candidates = ask_harness_candidates(item, shops)
    if not candidates:
        return {"item": item, "deal": False, "candidates": []}
    urls = {s["name"]: s["url"] for s in shops}
    names = [n for n in candidates if urls.get(n)]
    tasks = [asyncio.to_thread(negotiate_one, urls[n], item, remaining_budget) for n in names]
    results = await asyncio.gather(*tasks) if tasks else []
    deals = [(n, r) for n, r in zip(names, results) if r["deal"] and r["price"] is not None]
    if not deals:
        return {"item": item, "deal": False, "candidates": candidates}
    shop_name, best = min(deals, key=lambda nr: nr[1]["price"])
    return {"item": item, "deal": True, "price": best["price"], "shop": shop_name, "candidates": candidates}


async def run(directory_path: str, list_path: str, total_budget: int):
    shops = load_directory(directory_path)
    shopping_list = json.load(open(list_path, encoding="utf-8"))["items"]

    bought = []        # [{"item", "shop", "price"}]
    remaining = total_budget
    summary = []        # one line per finished purchase -- the only memory carried forward

    for item in shopping_list:
        if any(b["item"] == item for b in bought):   # guard: never buy the same item twice
            print(f"skip {item}: already bought")
            continue
        result = await shop_for_item(item, shops, remaining)
        print(f"  candidates for {item}: {result['candidates']}")
        if result["deal"] and result["price"] <= remaining:
            bought.append({"item": item, "shop": result["shop"], "price": result["price"]})
            remaining -= result["price"]
            summary.append(f"bought {item} from {result['shop']} at {result['price']}")
            print(summary[-1] + f" (remaining budget: {remaining})")
        else:
            print(f"could not buy {item} within remaining budget {remaining}")

    report = {
        "bought": bought,
        "remaining_budget": remaining,
        "all_items_bought": {b["item"] for b in bought} == set(shopping_list),
        "within_budget": remaining >= 0,
        "no_duplicates": len(bought) == len({b["item"] for b in bought}),
    }
    print("\n" + json.dumps(report, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--directory", required=True)
    ap.add_argument("--list", required=True)
    ap.add_argument("--budget", type=int, required=True)
    args = ap.parse_args()
    asyncio.run(run(args.directory, args.list, args.budget))
