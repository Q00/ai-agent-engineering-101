"""Demanding reviews of real settlements; no invented defects or LLM calls."""
import argparse
import json
import math
import sys
import time
from pathlib import Path

from feedback import append_event
from inventory import read_json
from payment_memory import recent_jsonl
from buyer_forum import queue_confirmed_deal
from market_clock import schedule_now

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


def review(deal, *, terms=None, now=None, market_clock=None):
    settled, receipt = deal["settled"], deal["receipt"]
    if type(settled.get("ledger")) is not int or not isinstance(settled.get("sku"), str):
        raise ValueError("Expected the kit's Round 3 settlement receipt")
    price = receipt.get("price")
    if type(price) not in (int, float) or not math.isfinite(price) or price <= 0:
        raise ValueError("Invalid settled price")
    now = time.time() if now is None else now
    # Both acceptance and preferences belong to this purchase. Never apply a
    # newly edited local policy retroactively to a historical settlement.
    accept = terms.get("accept", {}) if terms else {}
    preferences = terms.get("review_preferences") if terms else None
    legacy = bool(terms) and preferences is None
    kind = terms.get("kind") if terms else deal["order"].get("kind")
    reasons, notes, ceilings, issues, shortfalls = [], [], [], [], []
    # `now` remains the local observation timestamp for file freshness/forum
    # queues. A synchronized market upper bound is separate: the host has been
    # observed ~13 minutes fast. Never silently use its fallback for food.
    expiry_now, clock_width, clock_snapshot = now, 0, None
    if kind == "food" and market_clock is not None:
        synced = (isinstance(market_clock, dict) and market_clock.get("status") == "synced"
                  and all(type(market_clock.get(k)) in (int, float)
                          and math.isfinite(market_clock[k]) for k in ("now", "uncertainty_seconds"))
                  and market_clock["now"] > 0 and 0 <= market_clock["uncertainty_seconds"] <= 6)
        if synced:
            expiry_now, clock_width = market_clock["now"], market_clock["uncertainty_seconds"]
            clock_snapshot = {"status": "synced", "now": expiry_now,
                              "uncertainty_seconds": clock_width}
        else:
            expiry_now = None
            clock_snapshot = {"status": "unavailable", "now": None, "uncertainty_seconds": None}

    def fault(code, description, ceiling):
        reasons.append(code)
        issues.append(code)
        notes.append(description)
        ceilings.append(ceiling)

    def preference(code, description):
        reasons.append(code)
        shortfalls.append(code)
        notes.append(description)

    if settled.get("claim_honest") is False:
        fault("claim_mismatch", "The recorded grade claim did not match the delivered grade.", 1)
    grade = settled.get("grade")
    if grade not in ("A", "B", "C"):
        fault("grade_unreported", "The settlement did not report a usable quality grade.", 2)
    elif accept.get("grades") and grade not in accept["grades"]:
        fault("grade_below_requirement", f"Delivered grade {grade} did not meet my quality requirement.", 2)
    elif preferences and grade not in preferences["grades"]:
        preference("preferred_grade_not_met", f"The accepted grade {grade} was below my preferred quality grade.")
    if type(settled.get("value")) in (int, float) and settled["value"] == 0:
        fault("zero_value", "The market recorded zero usable value for this purchase.", 1)
    if kind == "goods":
        warranty = settled.get("warranty_hours")
        if type(warranty) not in (int, float) or not math.isfinite(warranty):
            fault("warranty_unreported", "The settlement did not report a warranty duration.", 2)
        elif warranty < accept.get("warranty_hours", 0):
            fault("short_warranty", f"Recorded warranty of {warranty:g} h was shorter than required.", 2)
        elif preferences and warranty < preferences["goods_warranty_hours"]:
            preference("preferred_warranty_not_met", f"The accepted warranty of {warranty:g} h was shorter than my preferred coverage.")
    elif kind == "food":
        expiry = settled.get("expires_at")
        if type(expiry) not in (int, float) or not math.isfinite(expiry):
            fault("expiry_unreported", "The settlement did not report the food's expiry time.", 2)
        elif expiry_now is None:
            # An unavailable local observation is not evidence against a shop.
            reasons.append("expiry_clock_unavailable")
            notes.append("Food lifetime could not be assessed because a fresh market clock was unavailable.")
            ceilings.append(3)
        else:
            remaining = (expiry - expiry_now) / 3600
            possible_remaining = remaining + clock_width / 3600
            required = accept.get("min_hours_left")
            if possible_remaining <= 0:
                fault("expired_at_review", "The food was expired at review time; this does not establish its condition at payment.", 1 if legacy else 2)
            elif required is not None and possible_remaining < required:
                fault("short_shelf_life_at_review", f"Only {remaining:.2f} h remained at review time, below my required margin.", 2)
            elif remaining <= 0 or (required is not None and remaining < required):
                reasons.append("expiry_clock_boundary")
                notes.append("Clock uncertainty overlapped the lifetime limit; a shelf-life violation was not established.")
                ceilings.append(3)
            elif preferences and remaining < preferences.get("food_min_hours_left", 0):
                preference("preferred_shelf_life_not_met", "The accepted shelf life was below my preferred margin when reviewed; this does not establish its condition at payment.")
    else:
        fault("kind_unreported", "Product type was unavailable for a full quality assessment.", 3)
    target = terms.get("target_price") if terms else None
    limit = terms.get("price_limit") if terms else None
    if limit is not None and price > limit:
        fault("above_price_limit", "The price exceeded my recorded purchasing limit.", 1)
    elif preferences and preferences.get("price_limit") is not None and price > preferences["price_limit"]:
        preference("above_preferred_price", "The accepted price was above my preferred value range.")
    elif legacy and target is not None and price > target:
        preference("above_target_price", "The paid price was above my preferred value target.")
        ceilings.append(2)  # Preserve the assessment promised by old policies.
    if (preferences and preferences.get("price_limit") is None) or (not preferences and target is None):
        fault("price_context_unavailable", "My original price target was unavailable, so value was not fully assessed.", 3)
    if preferences:
        stars = 2 if len(shortfalls) >= 2 else 3 if shortfalls else 4
        excellent = preferences.get("excellent_price")
        if not shortfalls and excellent is not None and price <= excellent:
            stars = 5
    elif legacy and target is not None:
        stars = 5 if price <= target * .8 else 4
    else:
        stars = 3
    if ceilings:
        stars = min(stars, *ceilings)
    if not notes:
        notes.append("The recorded quality, warranty or shelf-life conditions and my review preferences were satisfied."
                     if preferences else
                     "The recorded quality, warranty or shelf-life conditions and my price target were satisfied.")
    text = f"Settled {settled['sku']} at {price:g}; grade {grade}. " + " ".join(notes) + f" Ledger {settled['ledger']}."
    # No budget, private target amount or price-limit amount appears in public text.
    return {"stars": stars, "review": text, "reasons": reasons, "reviewed_at": now,
            "issues": issues, "preference_shortfalls": shortfalls,
            **({"market_clock": clock_snapshot} if clock_snapshot is not None else {})}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deal", type=Path, required=True)
    args = parser.parse_args()
    deal = read_json(args.deal)
    terms = purchase_terms(deal, recent_jsonl(ROOT / "data" / "market-memory.jsonl"))
    clock = schedule_now(ROOT / ".market", deal["order"].get("round"))
    result = review(deal, terms=terms, market_clock=clock)
    # Do not copy private order budget/mandates into review text or shared evidence.
    append_event(ROOT / "logs" / "settled-deals.jsonl", {
        "order": deal["order"]["order"], "receipt": deal["receipt"], "settled": deal["settled"],
        "assessment": result, "policy_version": terms.get("version") if terms else None})
    try:
        queued = queue_confirmed_deal(ROOT, deal, result)
        print("[buyer-forum] queue=" + queued["status"], file=sys.stderr)
    except Exception as exc:
        # A full disk or malformed spool must not discard a valid review. Do
        # not log the exception message: it could contain private deal data.
        print("[buyer-forum] queue_error=" + type(exc).__name__, file=sys.stderr)
    print(json.dumps({"stars": result["stars"], "review": result["review"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
