"""Writes logs/challenge-1/ and logs/challenge-2/ from my own records and the market's public stats.

    python challenges.py

Inputs: shop_history.jsonl (my shop), buyer_memory.jsonl (my buyer), logs/market-kit.log, and
https://market.ourolabs.ai/api/stats and /api/board (read only; a snapshot is saved next to the summary).
"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
MARKET = "https://market.ourolabs.ai"
ITEMS = ("lamp", "chair", "bookshelf")
# when the shop's pricing changed (local time, from the git log and logs/market-kit.log)
PHASES = [
    ("P0 starter pricing", "2026-10-20 00:00:00", "2026-10-20 11:55:49"),
    ("P1 data-driven opening ask", "2026-10-20 11:55:49", "2026-10-20 12:16:08"),
    ("P2 boutique (+ launch price, certificates)", "2026-10-20 12:16:08", "2099-01-01 00:00:00"),
]


def jsonl(path):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def avg(xs):
    return f"{sum(xs) / len(xs):.1f}" if xs else "-"


def challenge1():
    out = HERE / "logs" / "challenge-1"
    out.mkdir(parents=True, exist_ok=True)
    stats = httpx.get(f"{MARKET}/api/stats", timeout=30).json()
    board = httpx.get(f"{MARKET}/api/board", timeout=30).json()
    (out / "market-stats.json").write_text(json.dumps(stats, indent=1) + "\n", encoding="utf-8")
    me_board = next(s for s in board["shops"] if s["name"] == "bjeon01")
    (out / "market-board-bjeon01.json").write_text(json.dumps(me_board, indent=1) + "\n", encoding="utf-8")

    hist = jsonl(HERE / "shop_history.jsonl")
    lines = ["# Challenge 1: my shop's prices before and after the change", ""]
    lines.append("## My shop by pricing phase (shop_history.jsonl; P0 deals are the signed receipts in market-kit.log)")
    lines.append("phase | negotiations | deals | deal rate | avg price lamp / chair / bookshelf")
    for name, a, b in PHASES:
        rows = [h for h in hist if a <= h["time"] < b]
        deals = [h for h in rows if h["outcome"] == "deal"]
        rate = f"{len(deals) / len(rows):.0%}" if rows and name[:2] != "P0" else "-"
        prices = " / ".join(avg([h["price"] for h in deals if h["item"] == it]) for it in ITEMS)
        lines.append(f"{name} | {len(rows) if name[:2] != 'P0' else '-'} | {len(deals)} | {rate} | {prices}")
    p2 = [h for h in hist if h["time"] >= PHASES[2][1] and h["outcome"] == "deal"]
    for label, rows in (("  P2 launch-price deals", [h for h in p2 if h.get("benefit")]),
                        ("  P2 boutique-price deals", [h for h in p2 if not h.get("benefit")])):
        lines.append(f"{label} | | {len(rows)} | | " + " / ".join(avg([h['price'] for h in rows if h['item'] == it])
                                                         for it in ITEMS))
    lines.append(f"  certificates issued: {sum(1 for h in p2 if h.get('certificate'))}")
    lines += ["", "## Market dashboard per round (/api/stats): my shop against the market"]
    lines.append("round | my deals | my share | my reviews | my stars | market deals | market avg lamp / chair / bookshelf")
    for r, v in sorted(stats["rounds"].items()):
        me = next((s for s in v["shops"] if s["shop"] == "bjeon01"), {})
        mk = " / ".join(f"{v['items'][it]['mean']:.1f}" for it in ITEMS if it in v["items"])
        lines.append(f"R{r} | {me.get('deals')} | {me.get('share')} | {me.get('reviews')} | {me.get('stars')} | "
                     f"{v['deals']} | {mk}")
    r2 = [h for h in hist if h["time"] >= "2026-10-20 12:31:04" and h["outcome"] == "deal"]
    lines.append("my avg price in R2 (from 2026-10-20 12:31) lamp / chair / bookshelf: "
                 + " / ".join(avg([h["price"] for h in r2 if h["item"] == it]) for it in ITEMS))
    lines.append(f"board now: deals {me_board['deals']}, visits {me_board['visits']}, stars {me_board['stars']}, "
                 f"revenue {me_board['revenue']}, profit {me_board['profit']}, alive {me_board['alive']}")
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def challenge2():
    out = HERE / "logs" / "challenge-2"
    out.mkdir(parents=True, exist_ok=True)
    mem = jsonl(HERE / "buyer_memory.jsonl")
    log = (HERE / "logs" / "market-kit.log").read_text(encoding="utf-8", errors="ignore").splitlines()
    board = {s["name"]: s for s in httpx.get(f"{MARKET}/api/board", timeout=30).json()["shops"]}
    lines = ["# Challenge 2: dishonest participants my buyer met, and how it stopped trading with them", ""]
    lines.append(f"buyer memory: {len(mem)} trades, {mem[0]['time']} .. {mem[-1]['time']}")
    lines.append("")
    lines.append("shop | house? | what happened | when flagged | trades with it afterwards")
    flagged = {}
    for i, m in enumerate(mem):
        bad = m.get("receipt_ok") is False or m.get("signed") is False
        if bad and m["shop"] not in flagged:
            flagged[m["shop"]] = i
    for shop, i in flagged.items():
        m = mem[i]
        what = (f"unsigned receipt ({m['item']} at {m['receipt_price']})" if m.get("signed") is False
                else f"agreed {m['agreed']}, receipt {m['receipt_price']} ({m['item']})")
        later = [x for x in mem[i + 1:] if x["shop"] == shop]
        house = "house" if board.get(shop, {}).get("house") else "student"
        lines.append(f"{shop} | {house} | {what} | {m['time']} | {len(later)}")
    lines += ["", "Round 0 shops whose receipts had no sig_seller (from market-kit.log, before the buyer kept memory):"]
    unsigned, shop = Counter(), None
    for line in log:
        m = re.search(r"\[kit\] shop for this order: (\S+)", line)
        if m:
            shop = m.group(1)
        elif "[order " in line:
            shop = None
        elif "[sign] receipt has no sig_seller" in line and shop:
            unsigned[shop] += 1
    for s, n in unsigned.most_common():
        lines.append(f"  {s}: {n} unsigned receipt(s); now {board.get(s, {}).get('deals')} counted deals in the market")
    avoid = [l for l in log if "[avoid]" in l]
    lines.append(f"[avoid] lines in market-kit.log: {len(avoid)} (first: {avoid[0][:150] if avoid else '-'})")
    lines += ["", "## The episodes, verbatim from logs/market-kit.log"]
    for pat in (r"agreed 100, receipt says 115", r"agreed 280, receipt says 320"):
        idx = next(k for k, l in enumerate(log) if re.search(pat, l))
        start = max(k for k in range(idx) if "[order " in log[k])
        lines += [l[:260] for l in log[start:idx + 6]
                  if re.search(r"\[order|\[choose\]|-> '|\[receipt\]|\[harness\] \{\"stars|ledger #", l)]
        lines.append("")
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    challenge1()
    print()
    challenge2()
