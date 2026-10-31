"""Week 07, LAB 1 -- the market buyer on the PC: pick a shop from reviews and memory, trade, review.

The kit hands this program a shopping list file, an item and a budget, and
reads one JSON line back:

    python market_buyer.py --shops .market/shops.json --item lamp --budget 120
    ...
    {"shop": "<url from the list>", "receipt": {...} | null, "stars": 4, "review": "..."}

How the shop is chosen, how the negotiation goes and how the review and the
memory are written is buyer_core.py, shared with the Cloudflare buyer (see its
docstring). This file is what only the PC has: the kit's command line, the
memory file next to the shopping list, Claude Code as the harness, and the
Round 0 lessons that only exist in the kit's log.

    Round 0 ran week-06's buyer.py, which has no memory. The wrong receipts it
    caught ("the shop closed but its receipt is wrong: receipt price 308.0 is
    above the 268 that was offered") are read back from the kit's log into
    memory (challenge 2), so a shop that cheated me is avoided even while
    strangers rate it well -- elm-street did, at 3.5 stars.
"""
import argparse
import asyncio
import json
import re
from pathlib import Path

import buyer
import buyer_core
# Re-exported for challenge2.py and make_report.py, which read the buyer's filter.
from buyer_core import aggregate, score, weighted_stars, why_bad  # noqa: F401


# --------------------------------------------------------------- the memory

def mem_path(shops_file: str) -> Path:
    return Path(shops_file).parent / "memory.json"


def load_mem(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                   # noqa: BLE001  (missing or corrupt -> start fresh)
        return {}


def save_mem(path: Path, mem: dict) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(mem, ensure_ascii=False, indent=1), encoding="utf-8")


# ------------------------------------------- what Round 0 taught (challenge 2)

KIT_LOG = Path(__file__).resolve().parent / "logs" / "market-kit.log"
_ORDER = re.compile(r"\[order \S+\] (\w+), budget (\d+), round (\d+)")
_SHOP = re.compile(r"\[kit\] shop for this order: (\S+) ")
_TASK = re.compile(r"\[buyer\] -> .* taskId=(\w+)")
_WRONG = re.compile(r"the shop closed but its receipt is wrong: (.*)")
_OVER = re.compile(r"receipt price ([\d.]+) is above the (\d+) that was offered")


def import_round0(mem: dict, log_path: Path = KIT_LOG) -> int:
    """Fold every wrong receipt week-06's buyer caught in Round 0 into memory,
    once each (keyed by the A2A task id). Returns how many were new."""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    known = {t.get("task") for e in mem.values() for t in e.get("trades", [])}
    added, cur = 0, {}
    for line in lines:                                  # the kit runs one order at a time
        if m := _ORDER.search(line):
            cur = {"item": m[1], "budget": int(m[2])}
        elif m := _SHOP.search(line):
            cur["shop"] = m[1]
        elif m := _TASK.search(line):
            cur["task"] = m[1]
        elif (m := _WRONG.search(line)) and cur.get("shop") and cur.get("task") not in known:
            over = _OVER.search(m[1])
            e = mem.setdefault(cur["shop"], {"fp": None, "trades": []})   # card never seen: fp unknown
            e["trades"].append({"item": cur["item"], "budget": cur["budget"],
                                "price": int(float(over[1])) if over else None,
                                "agreed": int(over[2]) if over else None, "rounds": None,
                                "deal": True, "honest": False, "stars": 1,
                                "task": cur["task"], "src": "round-0 kit log"})
            e["agg"] = aggregate(e["trades"])
            known.add(cur["task"])
            added += 1
            buyer_core.log(f"[market_buyer] memory: {cur['shop']} issued a wrong receipt in Round 0 "
                           f"(task {cur['task']}: {m[1]})")
    return added


# ----------------------------------------------------------------------- main

async def amain(a) -> int:
    shops = json.loads(Path(a.shops).read_text(encoding="utf-8"))
    mpath = mem_path(a.shops)
    mem = load_mem(mpath)
    if import_round0(mem):
        save_mem(mpath, mem)
    client = buyer.A2A()
    try:
        out, _ = await buyer_core.shop_once(buyer.ask_claude, client, shops, mem, a.item, a.budget)
    finally:
        await client.close()
    save_mem(mpath, mem)
    # The kit reads this last line. Everything above is log.
    print(json.dumps(out, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    return asyncio.run(amain(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
