"""Week 07 — the market buyer: picks a shop from a list, trades over A2A, remembers, reviews.

    python market_buyer.py --shops shops.json --item lamp --budget 120

{shops} is a JSON list of {"name", "url", "reviews": [{"stars", "review", "round"}, ...]}, newest
review first. The buyer:
  1. reads the list and its own memory file (buyer_memory.json, keyed by shop NAME — a shop's URL
     changes every time its kit restarts) and writes why it picked a shop to stdout ([choose]),
  2. negotiates with that one shop through week-06's buyer.negotiate() — same A2A client, same
     budget guard in code, A2A-Version: 1.0 on every request, taskId on every follow-up,
  3. compares the receipt with what it agreed, writes stars and a one-line review, updates memory,
  4. prints exactly one JSON object as its LAST line:
       {"shop": "<card URL>", "receipt": {...} or null, "stars": 1-5, "review": "..."}

Shop choice: code first drops shops it has reason to avoid — bad reviews (avg < 2.5 over >= 2
reviews, or a receipt-mismatch complaint), or a mismatch / rip-off in its own memory — then the
harness picks among what is left, seeing each shop's review summary and the buyer's own history.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import harness
from buyer import negotiate

MEMORY = Path("buyer_memory.json")
MISMATCH_WORDS = ("receipt said", "receipt says", "receipt price", "overcharg", "higher than", "charged more", "rip")


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- memory

def load_memory() -> dict:
    if MEMORY.exists():
        try:
            return json.loads(MEMORY.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"shops": {}}


def save_memory(mem: dict) -> None:
    MEMORY.write_text(json.dumps(mem, ensure_ascii=False, indent=1), encoding="utf-8")


def remember(mem: dict, shop_name: str, record: dict) -> None:
    s = mem["shops"].setdefault(shop_name, {"trades": [], "avoid": False, "avoid_reason": None})
    s["trades"].append(record)
    if record.get("mismatch"):
        s["avoid"], s["avoid_reason"] = True, f"receipt {record['receipt_price']} != agreed {record['agreed']} ({record['when']})"


def my_history(mem: dict, shop_name: str) -> str:
    s = mem["shops"].get(shop_name)
    if not s:
        return "no past trades"
    t = s["trades"]
    bought = [x for x in t if x["status"] == "bought"]
    parts = [f"{len(t)} past trade(s), {len(bought)} deal(s)"]
    if bought:
        parts.append("paid " + ", ".join(str(x["receipt_price"]) for x in bought[-3:]))
    if s.get("avoid"):
        parts.append(f"AVOID: {s['avoid_reason']}")
    return "; ".join(parts)


# ---------------------------------------------------------------- shop choice

def review_summary(shop: dict):
    rs = shop.get("reviews") or []
    stars = [r["stars"] for r in rs if isinstance(r.get("stars"), (int, float))]
    avg = statistics.mean(stars) if stars else None
    complaints = [r["review"] for r in rs if any(w in str(r.get("review", "")).lower() for w in MISMATCH_WORDS)]
    return avg, len(rs), complaints


def eligible(shops: list, mem: dict):
    """Code-side filter: shops with evidence of dishonesty are not contacted at all."""
    keep, dropped = [], []
    for s in shops:
        avg, n, complaints = review_summary(s)
        own = mem["shops"].get(s["name"], {})
        why = None
        if own.get("avoid"):
            why = f"my own memory: {own['avoid_reason']}"
        elif n >= 2 and avg is not None and avg < 2.5:
            why = f"reviews average {avg:.1f} over {n}"
        elif len(complaints) >= 2:
            why = f"{len(complaints)} reviews report a receipt that did not match the agreed price"
        (dropped if why else keep).append((s, why))
    return [s for s, _ in keep], dropped


def choose(shops: list, mem: dict, item: str, budget: int):
    keep, dropped = eligible(shops, mem)
    for s, why in dropped:
        log(f"[skip] {s['name']} ({s['url']}): {why}")
    if not keep:                                   # nothing trustworthy: take the least bad rather than give up
        log("[choose] every shop has a warning; falling back to the best-reviewed one")
        keep = sorted(shops, key=lambda s: -(review_summary(s)[0] or 0))[:1]
    if len(keep) == 1:
        s = keep[0]
        avg, n, _ = review_summary(s)
        log(f"[choose] {s['url']}: {s['name']} is the only shop without a warning "
            f"(reviews {n}, avg {avg if avg is None else round(avg, 1)}; {my_history(mem, s['name'])})")
        return s
    rows = []
    for s in keep:
        avg, n, _ = review_summary(s)
        latest = " | ".join(f"{r['stars']}★ {str(r['review'])[:80]}" for r in (s.get("reviews") or [])[:2]) or "no reviews"
        rows.append(f"- name={s['name']!r} url={s['url']} reviews={n} avg_stars={None if avg is None else round(avg, 2)} "
                    f"latest: {latest} | my history: {my_history(mem, s['name'])}")
    data = harness.ask_json(
        f"You are buying a {item} with a budget of {budget}. Pick ONE shop to negotiate with. Prefer shops whose "
        f"reviews say the receipt matched the agreed price and whose prices came down; prefer shops you have good "
        f"history with; avoid shops with complaints about receipts.\nShops:\n" + "\n".join(rows) +
        '\nReply with exactly one JSON object: {"name": "<shop name>", "why": "<one sentence>"}.', log)
    pick = next((s for s in keep if data and s["name"] == data.get("name")), None)
    if pick is None:
        harness.CALLS["fallbacks"] += 1
        pick = max(keep, key=lambda s: (review_summary(s)[0] or 0, len(mem["shops"].get(s["name"], {}).get("trades", []))))
        log(f"[choose] {pick['url']}: fallback to best average stars ({pick['name']})")
    else:
        log(f"[choose] {pick['url']}: {data.get('why', '')} ({pick['name']})")
    return pick


# ---------------------------------------------------------------- review

def write_review(item: str, budget: int, st: dict, mismatch: bool, shop_name: str):
    receipt = st.get("receipt") or {}
    facts = (f"Item {item}, budget {budget}, shop {shop_name!r}. Outcome: {st['status']}. "
             f"Offers I made: {[o for _, o in st['history']]}; asks I saw: {[a for a, _ in st['history']] + ([st['ask']] if st['ask'] else [])}. "
             f"Agreed price: {st['history'][-1][1] if st['status'] == 'bought' and st['history'] else None}; "
             f"receipt price: {receipt.get('price')}; receipt matches agreed price: {not mismatch}.")
    data = harness.ask_json(
        "Write a review of this shop for other buyers, from the facts only. " + facts +
        ' Reply with exactly one JSON object: {"stars": <integer 1-5>, "review": "<one sentence>"}.', log)
    stars = data.get("stars") if isinstance(data, dict) else None
    review = str(data.get("review", "")).strip() if isinstance(data, dict) else ""
    if not (isinstance(stars, int) and 1 <= stars <= 5) or not review:
        harness.CALLS["fallbacks"] += 1
        if mismatch:
            stars, review = 1, f"Agreed {st['history'][-1][1]}, the receipt said {receipt.get('price')}."
        elif st["status"] == "bought":
            stars, review = 4, f"Deal at {receipt.get('price')} after {st['rounds']} offers, receipt matched."
        else:
            stars, review = 2, f"No deal: the shop stayed at {st['ask']} against my budget {budget}."
    if mismatch and stars > 2:                     # a wrong receipt is never a good review, whatever the prose
        stars = 1
    return stars, review


# ---------------------------------------------------------------- main

def main():
    global MEMORY
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--memory", default=str(MEMORY))
    args = ap.parse_args()
    MEMORY = Path(args.memory)

    shops = json.loads(Path(args.shops).read_text(encoding="utf-8"))
    mem = load_memory()
    log(f"market_buyer · {args.item} budget {args.budget} · {len(shops)} shops · memory {MEMORY} "
        f"({len(mem['shops'])} shops remembered) · harness: {harness.COMMAND}")
    shop = choose(shops, mem, args.item, args.budget)

    st = negotiate(shop["url"], args.item, args.budget, shop=shop["name"])
    receipt = st.get("receipt")
    agreed = st["history"][-1][1] if st["history"] else None
    rprice = receipt.get("price") if isinstance(receipt, dict) else None
    mismatch = bool(st["status"] == "bought" and rprice is not None and agreed is not None and int(rprice) != int(agreed))
    if mismatch:
        log(f"[receipt] MISMATCH: agreed {agreed} but the receipt says {rprice}")
    stars, review = write_review(args.item, args.budget, st, mismatch, shop["name"])

    remember(mem, shop["name"], {"when": time.strftime("%Y-%m-%dT%H:%M:%S"), "item": args.item, "budget": args.budget,
                                 "status": st["status"], "agreed": agreed, "receipt_price": rprice,
                                 "mismatch": mismatch, "stars": stars, "rounds": st["rounds"], "url": shop["url"]})
    save_memory(mem)
    log(f"[memory] {shop['name']}: {my_history(mem, shop['name'])}")
    log(f"[done] {st['status']} harness calls {harness.CALLS['n']} fallbacks {harness.CALLS['fallbacks']}")

    out = {"shop": shop["url"], "receipt": receipt if st["status"] == "bought" else None, "stars": stars, "review": review}
    print(json.dumps(out, ensure_ascii=False), flush=True)       # the last line: the kit reads this


if __name__ == "__main__":
    main()
