"""Review a settled purchase and publish occasional ledger-backed buyer notes."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from buyer_memory import read_events, read_history, record, resolve_shop_name
from context_compaction import hours_left
from market_scoring import saved_at_price,buyer_cost_reference,VERSION
import decision_audit


OUTBOX = Path(".market/outbox.jsonl")
POST_INTERVAL = 1800


def append_outbox(post: dict, path: Path = OUTBOX) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(post, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        if os.write(fd, data) != len(data):
            raise OSError("short forum outbox write")
    finally:
        os.close(fd)


def run(deal: dict, catalog: dict, *, life=None,allow_forum_publish=False) -> dict:
    order, settled = deal["order"], deal["settled"]
    sku = settled.get("sku") or deal["receipt"].get("item")
    product = catalog.get(sku, {})
    price = int(deal["receipt"]["price"])
    list_price = int(product.get("list_price") or 0)
    cost_reference = buyer_cost_reference(product)
    value = int(settled.get("value") or 0)
    grade = settled.get("grade")
    ledger = settled.get("ledger")
    shop = settled.get("shop")
    history = read_history()
    linked = decision_audit.buyer_outcome_links(history, order=order.get('order'),
                shop=shop, sku=sku, price=price, task_id=deal['receipt'].get('task'))
    record("settled", order=order.get("order"), shop=shop, sku=sku,
           shop_name=resolve_shop_name(shop,order=order.get('order'),history=history),
           name=settled.get("name"), price=price, list_price=list_price,
           grade=grade, value=value, ledger=ledger,
           claim_honest=settled.get("claim_honest"),
           warranty_hours=settled.get("warranty_hours"),expires_at=settled.get('expires_at'),
           score_version=VERSION,score_metric='saved',
           saved_if_usable_at_payment=saved_at_price(list_price,price,(cost_reference or {}).get("unit_cost")) if list_price>0 else None,
           score_cost_reference=cost_reference,
           saved_maturity='estimated payment-time condition only; seller cost is private; market resolves actual cost, breakage and refunds',
           **{k:v for k,v in linked.items() if k not in ('order','shop','shop_name','sku','score_version')},
           outcome_source='kit_settled_review',
           hours_left_at_recording=hours_left(settled.get('expires_at'),
                                              datetime.now(timezone.utc).isoformat()))
    if value <= 0:
        stars = 2
        review = f"Paid {price} for {sku}; the item had no usable value at settlement."
    else:
        discount = (list_price - price) / list_price if list_price > 0 else 0.0
        stars = 5 if discount >= 0.25 and settled.get("claim_honest") is not False else 4
        review = f"Paid {price} for {sku}; certified grade {grade or 'unknown'} at settlement."
    if settled.get("claim_honest") is False:
        stars = min(stars, 2)
        review += " The seller's grade claim did not match the market record."

    if life is not None:
        try:
            # Queue/import trusted kit settlement locally; never run a model or
            # publish from the review hook.
            life.acquire(deal,catalog)
        except Exception as error:
            record('buyer_life_import_failed', ledger=ledger, error=type(error).__name__)
    recent_posts = [e.get("epoch", 0) for e in read_events() if e["event"] == "forum_post"]
    if (allow_forum_publish and life is None and value > 0 and list_price > 0 and price <= list_price * 0.8
            and isinstance(ledger, int) and time.time() - max(recent_posts, default=0) >= POST_INTERVAL):
        discount_pct = round((1 - price / list_price) * 100)
        text = (f"Settled purchase: {settled.get('name') or sku} ({sku}), grade {grade}, "
                f"paid {price} vs list {list_price} ({discount_pct}% less). "
                "Ledger reference attached; quality can change after settlement.")
        append_outbox({"post": text, "refs": [ledger], "as": "buyer"})
        record("forum_post", epoch=time.time(), ledger=ledger, sku=sku)
    return {"stars": stars, "review": review}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deal", type=Path, required=True)
    parser.add_argument('--life',action='store_true')
    args = parser.parse_args()
    deal = json.loads(args.deal.read_text(encoding="utf-8"))
    catalog = json.loads((args.deal.parent / "catalog.json").read_text(encoding="utf-8"))
    life=None
    if args.life:
        from buyer_life import BuyerLife
        life=BuyerLife(market_dir=args.deal.parent)
    print(json.dumps(run(deal, catalog,life=life), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
