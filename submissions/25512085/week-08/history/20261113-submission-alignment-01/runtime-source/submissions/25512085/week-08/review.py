"""Round 3 Post-Settlement Verification, Broken Item Attribution & Forum Engine (review.py).

Invoked by market_kit.py v4 after each settled deal via:
    python review.py --deal .market/deal.json

Responsibilities:
1. Inspects `settled` mandate_receipt in `deal.json` (`grade`, `claim`, `claim_honest`, `value`, `ledger`).
2. Updates shop honesty & settlement metrics in `state_hub.json` (`store.py`), and confirms the deal
   `market_buyer.py` parked in `pending_deals` as a paid purchase (`my_purchases`, with its ledger ID)
   and as a shop-memory entry (`memory.json`). Unpaid deals never get here, so they are never recorded.
3. Checks `.market/broken.jsonl` to attribute any broken non-food items back to the selling shop.
4. Picks the facts for a ledger-referenced (`refs: [ledger_seq]`) buyer forum post (respecting the 5-minute
    rate limit) and hands them to `forum_writer.py` (deterministic by default); casts strategic votes on
   informative peer forum posts to maximize Buyer Forum Score (30% of Buyer ALL!).
5. Prints final `{stars, review}` JSON line.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from market_buyer import load_memory, update_memory
from forum_writer import build_analysis_brief, check_post
from store import (
    load_catalog,
    load_json_safe,
    mutate_hub,
    queue_forum_action,
    sync_broken_and_payments,
)

MY_AGENT_NAME = "mjforge"
BASE_DIR = Path(__file__).resolve().parent


def start_forum_writer(market_dir: Path, brief: dict[str, Any]) -> None:
    """Write the brief and start forum_writer.py in its own session without waiting for it. If it cannot start,
    post the template text now so the post is not lost."""
    brief_path = market_dir / f"forum_brief_{int(time.time() * 1000)}.json"
    try:
        brief_path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
        kw = ({"start_new_session": True} if os.name != "nt"
              else {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP})
        subprocess.Popen([sys.executable, str(BASE_DIR / "forum_writer.py"), str(brief_path)], cwd=str(BASE_DIR),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    except OSError:
        if check_post(brief['fallback']) is None:
            queue_forum_action(market_dir, {"post": brief["fallback"], "refs": brief["refs"], "as": "buyer"})


def evaluate_settled_deal(deal_data: dict[str, Any], hub: dict[str, Any], market_dir: Path) -> tuple[int, str]:
    order = deal_data.get("order") or {}
    receipt = deal_data.get("receipt") or {}
    settled = deal_data.get("settled") or {}

    shop_url = str(settled.get("shop") or "").rstrip("/")
    sku = str(settled.get("sku") or receipt.get("item") or "")
    name = str(settled.get("name") or order.get("item") or sku)
    price = int(receipt.get("price") or 0)
    budget = int(order.get("budget") or price)
    value = int(settled.get("value") or price)
    grade = settled.get("grade") or "A"
    claim = settled.get("claim")
    claim_honest = settled.get("claim_honest")
    warranty = int(settled.get("warranty_hours") or 0)
    ledger_seq = settled.get("ledger")

    surplus = value - price

    # Update shop_trust in state_hub.json
    if shop_url:
        st = hub["shop_trust"].setdefault(shop_url, {
            "name": str(receipt.get("shop") or ""),
            "honest": 0, "dishonest": 0, "broken": 0, "settled": 0, "refuses": 0, "last_ts": 0
        })
        if receipt.get("shop"):
            st["name"] = str(receipt.get("shop"))
        # "settled" is counted once, from the paid line the kit writes to payments.jsonl
        # (sync_broken_and_payments below); counting it here as well counted every deal twice
        if claim_honest is False:
            st["dishonest"] = st.get("dishonest", 0) + 1
        elif claim_honest is True:
            st["honest"] = st.get("honest", 0) + 1
        st["last_ts"] = int(time.time())

    if isinstance(ledger_seq, int):
        refs = hub.setdefault("demand_radar", {}).setdefault("recent_refs", [])
        if ledger_seq not in refs:
            refs.append(ledger_seq)
            hub["demand_radar"]["recent_refs"] = refs[-25:]

    # The kit runs this hook only for paid deals, so this is where a purchase becomes real: take the deal
    # market_buyer.py parked under this order id and record it, with what the market says was shipped.
    order_id = str(order.get("order") or "")
    pending = (hub.get("pending_deals") or {}).pop(order_id, None) if order_id else None
    purchase = dict(pending or {
        "order": order.get("order"), "category": order.get("category") or order.get("item"),
        "shop_name": str(receipt.get("shop") or ""), "list_price": value,
    })
    memory_entry = purchase.pop("memory", None)
    purchase.update({
        "sku": sku, "name": name, "shop_url": shop_url or purchase.get("shop_url", ""), "price": price,
        "warranty_hours": warranty, "ledger": ledger_seq, "ts": int(time.time()), "broke": False,
    })
    purchases = hub.setdefault("my_purchases", [])
    purchases.append(purchase)
    if len(purchases) > 150:
        hub["my_purchases"] = purchases[-150:]

    # Sync any broken goods notifications from .market/broken.jsonl
    newly_broken = sync_broken_and_payments(hub, market_dir)

    # Determine post-settlement star rating & factual review
    if claim_honest is False:
        stars = 1
        review = (
            f"[Audit Alert] Dishonest grade claim at ledger #{ledger_seq}: shop claimed grade {claim}, "
            f"but market certificate proved actual grade {grade} for {name} ({sku}) at {price}."
        )
    elif value == 0:
        stars = 1
        review = (
            f"[Zero Value] Item {name} ({sku}) settled at {price} (ledger #{ledger_seq}) delivered 0 utility value "
            f"(insufficient shelf life for required {order.get('use_within_hours')}h window)."
        )
    elif surplus >= int(value * 0.20):
        stars = 5
        review = (
            f"[Verified Deal #{ledger_seq}] Bought {name} ({sku}, grade {grade}, honest={claim_honest}, "
            f"warranty={warranty}h) at {price} vs list value {value} (surplus +{surplus}, budget {budget})."
        )
    elif surplus >= 0:
        stars = 4
        review = (
            f"[Settled #{ledger_seq}] Purchased {name} ({sku}, grade {grade}, warranty={warranty}h) at {price} "
            f"(list value {value}, surplus +{surplus})."
        )
    else:
        stars = 3
        review = (
            f"[Settled #{ledger_seq}] Paid {price} for {name} ({sku}, grade {grade}, list value {value})."
        )

    # Shop memory (memory.json, read by market_buyer.py when ranking shops) gets the deal only now, paid,
    # with the stars this review gives
    if memory_entry is not None and purchase.get("shop_name"):
        update_memory(load_memory(), purchase["shop_name"], dict(memory_entry, stars=stars, outcome="settled"))

    # Trigger Buyer Forum Posting (every >= 315s) with real ledger refs to earn upvotes!
    now = int(time.time())
    f_state = hub.setdefault("forum_state", {"last_buyer_post_ts": 0, "last_shop_post_ts": 0, "voted_posts": []})
    last_buyer_post = int(f_state.get("last_buyer_post_ts", 0))

    if (now - last_buyer_post) >= 315 and isinstance(ledger_seq, int):
        shops = load_json_safe(market_dir / 'shops.json', None)
        if isinstance(shops, dict):
            shops = shops.get('shops', [])
        brief = build_analysis_brief(hub, load_catalog(market_dir), shops, now, {
            'sku': sku, 'paid': price, 'value': value, 'ledger': ledger_seq,
            'category': purchase.get('category') or sku,
        })
        if brief:
            brief.update(ledger=ledger_seq, market_dir=str(market_dir))
            start_forum_writer(market_dir, brief)
            f_state["last_buyer_post_ts"] = now

    # Cast strategic votes on peer forum posts with ledger refs (no rate limit on votes)
    forum_posts = load_json_safe(market_dir / "forum.json", []) or []
    voted_set = set(f_state.get("voted_posts") or [])
    new_votes = 0
    for p in forum_posts[:25]:
        p_id = p.get("id")
        author = str(p.get("author") or "")
        if not p_id or p_id in voted_set or MY_AGENT_NAME in author.lower():
            continue
        refs = p.get("refs") or []
        txt = str(p.get("text") or "").lower()
        # Upvote constructive posts that cite ledger refs; downvote spam/empty posts
        if refs and len(txt) >= 30:
            queue_forum_action(market_dir, {"vote": p_id, "value": 1, "as": "buyer"})
            voted_set.add(p_id)
            new_votes += 1
        if new_votes >= 3:
            break
    f_state["voted_posts"] = list(voted_set)[-200:]

    return stars, review


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True, help="Path to .market/deal.json")
    args = ap.parse_args()

    deal_path = Path(args.deal)
    market_dir = deal_path.parent
    deal_data = load_json_safe(deal_path, {}) or {}

    # Read, update and save the hub under one lock so concurrent writers' updates are not overwritten
    with mutate_hub() as hub:
        stars, review = evaluate_settled_deal(deal_data, hub, market_dir)

    print(json.dumps({"stars": stars, "review": review}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
