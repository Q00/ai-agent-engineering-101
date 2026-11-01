"""Demanding reviews of real settlements; no invented defects or LLM calls."""
import argparse
import json
import math
import time
from pathlib import Path

from feedback import append_event
from inventory import read_json
from payment_memory import recent_jsonl
from buyer_policy import load_policy

ROOT = Path(__file__).resolve().parent


def purchase_terms(deal, records):
    """Use the policy pinned before this payment, even if config later changes."""
    order_id, receipt = deal["order"]["order"], deal["receipt"]
    for record in reversed(records):
        if (record.get("order") == order_id and record.get("item") == receipt.get("item")
                and record.get("price") == receipt.get("price")
                and (record.get("observed_receipt") or {}).get("task") == receipt.get("task")
                and receipt.get("task") and record.get("result") == "receipt_issued"
                and record.get("url") == deal["settled"].get("shop")):
            return record.get("purchase_policy")
    return None


def review(deal, *, terms=None, now=None):
    settled, receipt = deal["settled"], deal["receipt"]
    if type(settled.get("ledger")) is not int or not isinstance(settled.get("sku"), str):
        raise ValueError("Expected the kit's Round 3 settlement receipt")
    price = receipt.get("price")
    if type(price) not in (int, float) or not math.isfinite(price) or price <= 0:
        raise ValueError("Invalid settled price")
    now = time.time() if now is None else now
    config = load_policy()
    accept = terms["accept"] if terms else {"grades": config["grades"], "warranty_hours": config["goods_warranty_hours"]}
    kind = terms.get("kind") if terms else deal["order"].get("kind")
    reasons, notes, ceilings = [], [], []

    def fault(code, description, ceiling):
        reasons.append(code)
        notes.append(description)
        ceilings.append(ceiling)

    if settled.get("claim_honest") is False:
        fault("claim_mismatch", "The recorded grade claim did not match the delivered grade.", 1)
    grade = settled.get("grade")
    if grade not in ("A", "B", "C"):
        fault("grade_unreported", "The settlement did not report a usable quality grade.", 2)
    elif grade not in accept.get("grades", ["A"]):
        fault("grade_below_requirement", f"Delivered grade {grade} did not meet my quality requirement.", 2)
    if settled.get("value") == 0:
        fault("zero_value", "The market recorded zero usable value for this purchase.", 1)
    if kind == "goods":
        warranty = settled.get("warranty_hours")
        if type(warranty) not in (int, float) or not math.isfinite(warranty):
            fault("warranty_unreported", "The settlement did not report a warranty duration.", 2)
        elif warranty < accept.get("warranty_hours", 0):
            fault("short_warranty", f"Recorded warranty of {warranty:g} h was shorter than required.", 2)
    elif kind == "food":
        expiry = settled.get("expires_at")
        if type(expiry) not in (int, float) or not math.isfinite(expiry):
            fault("expiry_unreported", "The settlement did not report the food's expiry time.", 2)
        else:
            remaining = (expiry - now) / 3600
            required = accept.get("min_hours_left")
            if required is None:
                required = deal["order"].get("use_within_hours", 0) + config["food_buffer_hours"]
            if remaining <= 0:
                fault("expired_at_review", "The food was expired at review time; this does not establish its condition at payment.", 1)
            elif remaining < required:
                fault("short_shelf_life_at_review", f"Only {remaining:.2f} h remained at review time, below my required margin.", 2)
    else:
        fault("kind_unreported", "Product type was unavailable for a full quality assessment.", 3)
    target = terms.get("target_price") if terms else None
    limit = terms.get("price_limit") if terms else None
    if limit is not None and price > limit:
        fault("above_price_limit", "The price exceeded my recorded purchasing limit.", 1)
    elif target is not None and price > target:
        fault("above_target_price", "The paid price was above my preferred value target.", 2)
    if target is None:
        fault("price_context_unavailable", "My original price target was unavailable, so value was not fully assessed.", 3)
    stars = min(ceilings) if ceilings else (5 if price <= target * .8 else 4)
    if not notes:
        notes.append("The recorded quality, warranty or shelf-life conditions and my price target were satisfied.")
    text = f"Settled {settled['sku']} at {price:g}; grade {grade}. " + " ".join(notes) + f" Ledger {settled['ledger']}."
    # No budget, private target amount or price-limit amount appears in public text.
    return {"stars": stars, "review": text, "reasons": reasons, "reviewed_at": now}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deal", type=Path, required=True)
    args = parser.parse_args()
    deal = read_json(args.deal)
    terms = purchase_terms(deal, recent_jsonl(ROOT / "data" / "market-memory.jsonl"))
    result = review(deal, terms=terms)
    # Do not copy private order budget/mandates into review text or shared evidence.
    append_event(ROOT / "logs" / "settled-deals.jsonl", {
        "order": deal["order"]["order"], "receipt": deal["receipt"], "settled": deal["settled"],
        "assessment": result, "policy_version": terms.get("version") if terms else None})
    print(json.dumps({"stars": result["stars"], "review": result["review"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
