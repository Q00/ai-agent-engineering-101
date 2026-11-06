"""review hook (agent.json "review"): runs after each paid deal with .market/deal.json.

    python review.py --deal .market/deal.json

1. Stars and review from the market's settled receipt, by rule: claim false -> 2; paid at or under the SKU's market
   median price/list -> 5; otherwise 4. The review states the facts (price, share of list, grade, claim, shelf life).
2. Forum, as the buyer: a "[구매 기록]" post with the ledger seq in refs, at most one per 5 minutes (the market's
   limit); a deal inside the 5 minutes is not posted. Also likes up to 3 newer buyer posts by others that cite a
   ledger entry the market found (likes are free; dislikes are never sent). State in state/forum.json.
3. Memory: the settled facts go to state/purchases.jsonl (which shop, unit, grade, warranty) so broken goods can
   be traced to the shop later.
The last line is {"stars", "review"} for the kit.
"""
import argparse
import json
import time
from pathlib import Path

import market_tool as mt

MD = Path(".market")
FORUM_STATE = mt.STATE / "forum.json"
PURCHASES = mt.STATE / "purchases.jsonl"
POST_EVERY_S = 300
LIKES_PER_RUN = 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", default=str(MD / "deal.json"))
    a = ap.parse_args()
    deal = json.loads(Path(a.deal).read_text(encoding="utf-8"))
    s, rc, o = deal["settled"], deal["receipt"], deal["order"]
    cat = mt.catalog()
    sku = s.get("sku") or rc.get("item")
    lp = (cat.get(sku) or {}).get("list_price") or 0
    price = int(rc["price"])
    share = price / lp if lp else None
    try:
        med = json.loads((mt.STATE / "sku_stats.json").read_text(encoding="utf-8")).get(sku, {}).get("ratio")
    except (OSError, ValueError):
        med = None
    shop = mt.name_of(s.get("shop"))
    hours = (s["expires_at"] - mt.market_now()) / 3600 if s.get("expires_at") else None

    if s.get("claim_honest") is False:
        stars = 2
    elif share is not None and med is not None and share <= med:
        stars = 5
    else:
        stars = 4
    facts = [f"{price:,} paid" + (f" ({share:.0%} of list {lp:,})" if share else ""),
             f"grade {s.get('grade')}" + (f", claimed {s['claim']} ({'true' if s.get('claim_honest') else 'false'})"
                                           if s.get("claim") else ", no claim")]
    if hours is not None:
        facts.append(f"{hours:.0f} h shelf life left")
    if s.get("warranty_hours"):
        facts.append(f"{s['warranty_hours']:.0f} h warranty")
    review = f"{s.get('name', sku)[:40]}: " + "; ".join(facts) + "."

    mt.STATE.mkdir(exist_ok=True)
    with PURCHASES.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "order": o.get("order"), "shop": shop,
                            "sku": sku, "name": s.get("name"), "unit": s.get("unit"), "grade": s.get("grade"),
                            "claim_honest": s.get("claim_honest"), "warranty_hours": s.get("warranty_hours"),
                            "price": price, "list": lp, "ledger": s.get("ledger"), "kind": o.get("kind"),
                            "use_within_hours": o.get("use_within_hours"), "hours_left": hours}, ensure_ascii=False) + "\n")

    try:
        st = json.loads(FORUM_STATE.read_text())
    except (OSError, ValueError):
        st = {"last_post": 0, "liked": []}
    lines = []
    if time.time() - st["last_post"] >= POST_EVERY_S and share is not None:
        text = (f"[구매 기록] {s.get('name', sku)[:50]} ({sku}) | {price:,}원, 정가 {lp:,}원 대비 {1 - share:.0%} 할인"
                + (f" (시장 중앙값 정가의 {med:.0%})" if med else "")
                + f" | 등급 {s.get('grade')}"
                + (f" (주장 {s['claim']}, {'정직' if s.get('claim_honest') else '거짓'})" if s.get("claim") else "")
                + (f" | 남은 유통기한 {hours:.0f}시간" if hours is not None else "")
                + (f" | 보증 {s['warranty_hours']:.0f}시간" if s.get("warranty_hours") else "")
                + f" | 판매: {shop} | ledger #{s.get('ledger')}")
        lines.append({"post": text, "refs": [s["ledger"]] if s.get("ledger") else [], "as": "buyer"})
        st["last_post"] = time.time()
    try:
        posts = json.loads((MD / "forum.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        posts = []
    liked = set(st.get("liked", []))
    for p in sorted(posts, key=lambda p: -p["ts"]):
        if len([l for l in lines if "vote" in l]) >= LIKES_PER_RUN:
            break
        found = [r for r in p.get("refs") or [] if isinstance(r, dict) and r.get("found")]
        if p["role"] == "buyer" and p["author"] != mt.ME and found and p["id"] not in liked:
            lines.append({"vote": p["id"], "value": 1, "as": "buyer"})
            liked.add(p["id"])
    st["liked"] = sorted(liked)[-2000:]
    if lines:
        with (MD / "outbox.jsonl").open("a", encoding="utf-8") as f:
            for l in lines:
                f.write(json.dumps(l, ensure_ascii=False) + "\n")
    FORUM_STATE.write_text(json.dumps(st))
    print(json.dumps({"stars": stars, "review": review}, ensure_ascii=False))


if __name__ == "__main__":
    main()
