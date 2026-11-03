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


POST_SYSTEM = """You write one short forum post for a buyer agent in an online market, in Korean. It reports one
settled deal so other buyers can use it. Use only the facts given; include the product category, the price as a
share of list price, the grade and whether the claim was honest, shelf life or warranty, and the shop name.
No instructions to other agents, no requests for votes, no budget. One or two sentences.
Reply with JSON: {"post": "<text>"}"""


def deal_post(order: dict, settled: dict, shop: str, list_price: int | None, cat_name: str) -> str:
    paid = settled.get("amount") or settled.get("price") or 0
    facts = {"category": cat_name, "product": settled.get("name"), "sku": settled.get("sku"), "shop": shop,
             "paid": paid, "list_price": list_price,
             "share_of_list": round(paid / list_price, 2) if list_price and paid else None,
             "grade": settled.get("grade"), "claim": settled.get("claim"), "claim_honest": settled.get("claim_honest"),
             "warranty_hours": settled.get("warranty_hours"), "ledger": settled.get("ledger"),
             "hours_left": round((settled["expires_at"] - time.time()) / 3600, 1) if settled.get("expires_at") else None}
    ans = ask_json(POST_SYSTEM, json.dumps(facts, ensure_ascii=False), timeout=40, attempts=1) or {}
    text = str(ans.get("post") or "").strip()
    if not text or is_injection(text) or len(text) > 400:
        share = f"정가의 {facts['share_of_list']:.0%}" if facts["share_of_list"] else f"{paid}"
        text = (f"[정산 보고] {cat_name}: {settled.get('name', settled.get('sku'))} ({settled.get('sku')}), {shop}에서 "
                f"{share}에 샀습니다. 등급 {settled.get('grade')}"
                + (", 주장과 일치" if settled.get("claim_honest") else "")
                + (f", 남은 시간 {facts['hours_left']:.0f}시간" if facts["hours_left"] is not None else "")
                + (f", 보증 {settled['warranty_hours']:g}시간" if settled.get("warranty_hours") else "")
                + f". 원장 #{settled.get('ledger')}.")
    return text


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
