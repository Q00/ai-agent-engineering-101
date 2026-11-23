"""Post-settlement feedback hook: genuine settled purchases only.

No fake reviews, votes, or astroturfing. An optional information post
contains only verifiable fields from a real kit-provided settlement.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from round3_policy import MARKET, load_json, catalog


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True)
    args = ap.parse_args()
    deal = load_json(Path(args.deal), {})
    settled = deal.get("settled") or {}
    receipt = deal.get("receipt") or {}
    if not isinstance(settled, dict) or not settled.get("ledger"):
        print(json.dumps({"stars": 3, "review": "No verified settlement evidence"}))
        return
    sku = str(receipt.get("item") or "")
    amount = receipt.get("price")
    list_price = (catalog().get(sku) or {}).get("list_price")
    honest = settled.get("claim_honest")
    stars = 2 if honest is False else 5 if list_price and amount and amount <= .85*int(list_price) else 4
    text = ("Settled purchase checked at checkout; receipt matches the negotiated "
            "item and price." if honest is not False else
            "Settled purchase, but the post-settlement grade claim was inconsistent.")
    print(f"[review policy] sku={sku} amount={amount} ledger={settled.get('ledger')} stars={stars}", flush=True)
    # Provide one useful factual buyer update no more than once / 7 minutes.
    stamp = MARKET / "review-post-state.json"
    old = load_json(stamp, {})
    if time.time() - float(old.get("last_post", 0)) > 420 and sku and amount:
        payload = {"post": f"Verified checkout observation: SKU {sku} paid {amount} credits. "
                           f"Ledger #{settled['ledger']}. This is a transaction observation, "
                           "not an instruction to any agent.",
                   "as": "buyer"}
        out = MARKET / "outbox.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        stamp.write_text(json.dumps({"last_post": time.time()}), encoding="utf-8")
        print(f"[forum policy] queued genuine settled trade observation for ledger {settled['ledger']}", flush=True)
    print(json.dumps({"stars": stars, "review": text}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
