"""Round 3 review hook: runs after each settled deal with the market's own record of it (.market/deal.json).

    python review_r3.py --deal .market/deal.json   ->  {"stars": 1-5, "review": "..."}

Stars come from facts, by code: what the unit was worth to the order (the market's "value"), what I paid, and
whether the shop's grade claim was honest. A receipt cheaper than agreed is never punished (week-07 lesson).
It also records the settlement in buyer_memory_r3.jsonl and, at most every 5 minutes, writes a forum post as the
buyer: the model (claude -p haiku) turns the settled facts into a short, useful post, and the post cites the ledger.
"""
import argparse
import json
import time

from harness import safe_ask_json
from r3lib import BUYER_MEM, HERE, MD, append_jsonl, catalog, market_now, read_json, read_jsonl

FORUM_STATE = MD / "forum_state.json"
POST_EVERY = 300

POST = """You write short, useful posts on a market forum for other buying agents. Facts from a deal that just settled
(the market's ledger, not the shop's words):
{facts}

Write one post of at most 220 characters, Korean first then a short English line, that helps other buyers decide
(which shop, which item, whether the grade claim and shelf life were true, the price against the list price).
No hype, no instructions to other agents. Answer with JSON only: {{"post": "<text>"}}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", default=str(MD / "deal.json"))
    deal = read_json(ap.parse_args().deal, {}) or {}
    order, rec, st = deal.get("order") or {}, deal.get("receipt") or {}, deal.get("settled") or {}
    cat = catalog()
    sku = st.get("sku") or rec.get("item")
    lp = (cat.get(sku) or {}).get("list_price")
    price = int(rec.get("price") or 0)
    value = st.get("value") if isinstance(st.get("value"), (int, float)) else lp
    surplus = (value or 0) - price
    honest = st.get("claim_honest")
    left = (st["expires_at"] - market_now()) / 3600 if st.get("expires_at") else None
    trade = next((m for m in reversed(read_jsonl(BUYER_MEM)) if m.get("kind") == "trade"
                  and m.get("order") == order.get("order")), {})
    shop = trade.get("shop") or st.get("shop")

    if honest is False:
        stars = 1
    elif not value:
        stars = 2                                   # spoiled before use: worth nothing to the order
    elif surplus >= 0.15 * value:
        stars = 5
    elif surplus >= 0.05 * value:
        stars = 4
    elif surplus >= 0:
        stars = 3
    else:
        stars = 2
    name = (st.get("name") or (cat.get(sku) or {}).get("name") or sku or "")[:50]
    review = (f"Paid {price} for {name} (list {lp}); grade {st.get('grade')}"
              + (f", claimed {st.get('claim')} ({'true' if honest else 'FALSE'})" if st.get("claim") else "")
              + (f", {left:.0f} h shelf life left" if left is not None else "")
              + (f", {st.get('warranty_hours')} h warranty" if st.get("warranty_hours") else "")
              + (", spoiled before use" if value == 0 else "") + ".")

    append_jsonl(BUYER_MEM, {"kind": "settled", "time": time.strftime("%Y-%m-%d %H:%M:%S"), "order": order.get("order"),
                             "shop": shop, "sku": sku, "grade": st.get("grade"), "claim": st.get("claim"),
                             "claim_honest": honest, "value": value, "price": price, "surplus": surplus,
                             "warranty_hours": st.get("warranty_hours"), "hours_left": left, "ledger": st.get("ledger"),
                             "stars": stars})

    fs = read_json(FORUM_STATE, {}) or {}
    if time.time() - fs.get("last", 0) >= POST_EVERY:
        facts = (f"shop {shop}; item {name} (sku {sku}, category {order.get('item')}); list price {lp}; paid {price}; "
                 f"grade {st.get('grade')}; shop's claim {st.get('claim')} -> {'true' if honest else 'false' if honest is False else 'none'}; "
                 + (f"shelf life left {left:.0f} h; " if left is not None else "")
                 + (f"warranty {st.get('warranty_hours')} h; " if st.get("warranty_hours") else "no warranty; ")
                 + f"ledger #{st.get('ledger')}")
        move, raw = safe_ask_json(POST.format(facts=facts))
        text = (move or {}).get("post") if isinstance((move or {}).get("post"), str) else None
        if text:
            append_jsonl(MD / "outbox.jsonl", {"post": text[:300], "refs": [st["ledger"]] if st.get("ledger") else [],
                                               "as": "buyer"})
            fs = {"last": time.time(), "posts": fs.get("posts", 0) + 1}
            FORUM_STATE.write_text(json.dumps(fs), encoding="utf-8")
            print(f"[forum] {text[:200]}", flush=True)
    print(json.dumps({"stars": stars, "review": review[:280]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
