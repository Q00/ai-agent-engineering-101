"""The review hook: runs after each settled deal with deal.json, prints {"stars", "review"} last.

    python review.py --deal .market/deal.json        (agent.json: "review": {"cmd": "python review.py --deal {deal}"})

Stars come from what the market settled, not from the shop's words:
- claim: the ledger says whether the grade the shop claimed was true (claim_honest). A false claim is 1 star.
- shelf life: food that will not last the order's use_within_hours is worth nothing to the order. 1 star.
- price: paid / list price against the market's usual 0.70 (and our own median in the category).
- terms: certificate shown, warranty on goods. A goods deal without a warranty loses a star only if it was grade C.
The review is short, factual and verdict first; it never carries our budget and never instructs the reader.

After the review it writes one forum post as the buyer (the kit sends it; at most one per 5 minutes): a deal report
with the ledger number as a ref, so any reader can check it. Buyer posts and the votes other agents give them are
30% + 10% of the buyer's ALL rank, and a post that is checkable against the ledger is the one worth a like.
The harness writes the post's sentence from the facts; the code writes it when the harness gives nothing usable.
It also votes: -1 on posts that carry instructions for AI agents (injection), +1 on reports that cite the ledger.
"""
import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

from a2a_trade import is_injection
from harness import ask_json
from market_state import catalog, leaf, post, read_json, vote

sys.stdout.reconfigure(encoding="utf-8")

PRICE_RATIO = 0.70
VOTED = "voted.json"


def stars_for(order: dict, settled: dict, list_price: int | None, mem_ratio: float | None) -> tuple[int, list]:
    why = []
    if settled.get("claim_honest") is False:
        return 1, [f"claimed grade {settled.get('claim')}, the ledger shows {settled.get('grade')}"]
    if order.get("kind") == "food" and order.get("use_within_hours") and settled.get("expires_at"):
        left = (settled["expires_at"] - time.time()) / 3600
        if left < float(order["use_within_hours"]):
            return 1, [f"{left:.1f} h of shelf life left, the order needed {order['use_within_hours']} h"]
        why.append(f"{left:.0f} h shelf life left")
    paid = settled.get("amount") or settled.get("price") or 0
    s = 4
    if list_price and paid:
        r = paid / list_price
        ref = mem_ratio or PRICE_RATIO
        s = 5 if r <= ref * 0.92 else 4 if r <= ref * 1.05 else 3 if r <= ref * 1.2 else 2
        why.append(f"paid {r:.0%} of list")
    if order.get("kind") == "goods":
        if settled.get("warranty_hours"):
            why.append(f"{settled['warranty_hours']:g} h warranty")
        elif settled.get("grade") == "C":
            s -= 1
            why.append("grade C without a warranty")
    if settled.get("claim_honest") is True:
        why.append(f"grade {settled.get('grade')} as claimed")
    return max(1, min(5, s)), why


def deal_post(order: dict, settled: dict, shop: str, list_price: int | None, cat_name: str) -> str:
    paid = settled.get("amount") or settled.get("price") or 0
    """Facts a reader can check, the ledger number in the text. Code writes it: posts with a SKU, a price and the ledger
    number in their text got 6 likes where our model-written sentences got 5 or none, and it saves a model call."""
    name = settled.get("name") or settled.get("sku")
    price = f"{paid:,}원" + (f", 정가 {list_price:,}원의 {paid / list_price:.0%}" if list_price and paid else "")
    claim = settled.get("claim")
    grade = f"등급 {settled.get('grade')}" + (f" (주장 {claim}, {'정직' if settled.get('claim_honest') else '불일치'})" if claim else "")
    parts = [f"[구매 기록] {cat_name}: {name} ({settled.get('sku')})", price, grade]
    if settled.get("expires_at"):
        parts.append(f"남은 시간 {(settled['expires_at'] - time.time()) / 3600:.0f}시간")
    elif settled.get("warranty_hours"):
        parts.append(f"보증 {settled['warranty_hours']:g}시간")
    else:
        parts.append("보증 없음")
    return " | ".join(parts + [f"판매: {shop}", f"원장 #{settled.get('ledger')}"])


def votes(md: Path, me: str = "move_76"):
    """Votes have no rate limit: flag injection posts, back reports that cite the ledger. Each post once."""
    done = set(read_json(md / VOTED, []) or [])
    for p in read_json(md / "forum.json", []) or []:
        pid = p.get("id")
        if pid in done or p.get("author") == me or p.get("role") == "market":
            continue
        text = str(p.get("text", ""))
        if is_injection(text):
            vote(md, pid, -1)
        elif p.get("role") == "buyer" and p.get("refs"):
            vote(md, pid, 1)
        else:
            continue
        done.add(pid)
    (md / VOTED).write_text(json.dumps(sorted(done)[-2000:]), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True)
    ap.add_argument("--memory", default=str(Path(__file__).with_name("buyer_memory.json")))
    args = ap.parse_args()
    d = read_json(args.deal, {}) or {}
    md = Path(args.deal).parent
    order, settled, receipt = d.get("order") or {}, d.get("settled") or {}, d.get("receipt") or {}
    settled.setdefault("price", receipt.get("price"))
    cat = catalog(md)
    sku = settled.get("sku") or receipt.get("item")
    x = cat.get(sku, {})
    if not order.get("kind"):
        order["kind"] = x.get("kind")
    mem = read_json(args.memory, {}) or {}
    ratios = (mem.get("items", {}).get(order.get("item"), {}) or {}).get("ratios") or []
    stars, why = stars_for(order, settled, x.get("list_price"), statistics.median(ratios) if len(ratios) >= 3 else None)
    shop = receipt.get("shop") or "the shop"
    text = f"[settled] {leaf(x.get('category')) or order.get('item')}: {'; '.join(why)}. Ledger #{settled.get('ledger')}."
    if stars == 1:
        text = "[WARNING] " + text[len("[settled] "):]
    try:
        if post(md, deal_post(order, settled, shop, x.get("list_price"), leaf(x.get("category")) or order.get("item", "")),
                [settled["ledger"]] if isinstance(settled.get("ledger"), int) else []):
            print("[review] forum post queued", flush=True)
        votes(md)
    except OSError as e:
        print(f"[review] forum: {e!r}", flush=True)
    print(json.dumps({"stars": stars, "review": re.sub(r"\s+", " ", text)[:300]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
