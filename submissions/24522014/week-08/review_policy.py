"""Evidence-based post-settlement review hook. No model or network calls."""

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import time

from r3_policy import buyer_accept
from buyer_portfolio import save_deal
from forum_policy import posting_limits, queue_report

ROOT = Path(__file__).resolve().parent


def buyer_note(md, now=None):
    """Also run the buyer's market-note pass from this hook (the user's 11-14 choice in the Orchestrator session, about 21:3x KST:
    "후기 훅에도 추가 (권장)"). Only the restock hook ran it, and its runs leave gaps of up to 13 minutes in supply windows, so about
    48 of the market's 288 five-minute slots a day went unused. This hook runs once per settled deal. forum_buyer_posts.run keeps the
    300 s interval and the daily limit, rechecked under the outbox lock, so this only fills slots the restock hook missed. Off unless
    forum_posting.json sets buyer_notes_from_review to true. Skipped while the restock hook holds the forum lock. Nothing here may cost
    the review: every error is caught, and anything printed goes to stderr, never into the result line."""
    try:
        with redirect_stdout(sys.stderr):
            now = time.time() if now is None else now
            if posting_limits(md, now).get("buyer_notes_from_review") is not True:
                return "off"
            from strategy_controller import acquire, release
            handle = acquire(md / "forum-engagement.lock")
            if handle is None:
                return "busy"
            try:
                import forum_buyer_posts
                return forum_buyer_posts.run(md, now)
            finally:
                release(handle)
    except Exception as error:  # the review goes out whatever happens here
        return f"note_error: {type(error).__name__}"


def evaluate(deal, now=None):
    order, receipt, settled = (deal.get(k) or {} for k in ("order", "receipt", "settled"))
    reasons = []
    if not settled.get("ledger") or not settled.get("shop"):
        reasons.append("settlement evidence missing")
    grade, claim = settled.get("grade"), settled.get("claim")
    price = receipt.get("price")
    budget = order.get("budget")
    if (type(price) not in (int, float) or not math.isfinite(price) or price <= 0 or int(price) != price
            or type(budget) not in (int, float) or not math.isfinite(budget) or price > budget):
        reasons.append("price/budget evidence inconsistent")
    if not receipt.get("task") or not receipt.get("sig_seller"):
        reasons.append("signed receipt evidence incomplete")
    acceptable = order.get("acceptable") or []
    if receipt.get("item") not in acceptable and receipt.get("item") != order.get("item"):
        reasons.append("receipt does not name an ordered item")
    if not settled.get("sku") or (settled["sku"] != receipt.get("item") and not (
            receipt.get("item") == order.get("item") and settled["sku"] in acceptable)):
        reasons.append("settled SKU does not match receipt/order")
    if (not reasons and settled.get("claim_honest") is False
            and grade in ("A", "B", "C") and claim in ("A", "B", "C") and grade != claim):
        return {"stars": 2, "review": f"Settlement #{settled['ledger']}: seller claimed {claim}, market recorded {grade}."}
    if grade not in ("A", "B"):
        reasons.append("grade outside approved A/B policy or unknown")
    if claim in ("A", "B", "C") and settled.get("claim_honest") is not True:
        reasons.append("quality claim not confirmed")
    value = settled.get("value")
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        reasons.append("positive buyer value not confirmed")
    if order.get("kind") == "food":
        try:
            required = buyer_accept(order)["min_hours_left"]
            expires = settled.get("expires_at")
            if type(expires) not in (int, float) or not math.isfinite(expires):
                raise ValueError("missing expiry")
            remaining = (expires - (time.time() if now is None else now)) / 3600
            if remaining < required:
                reasons.append("food time margin not confirmed at review time")
        except (ValueError, TypeError, KeyError):
            reasons.append("food-use evidence incomplete")
    if reasons:
        return {"stars": 3, "review": "Insufficient/inconsistent evidence: " + "; ".join(reasons) + ". No seller fault inferred."}
    return {"stars": 5, "review": f"Settlement #{settled['ledger']}: {settled['sku']} at {price}; "
            f"receipt/order match, grade {grade}, approved buying conditions checked. Later faults are not assessed here."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deal", type=Path, required=True)
    args = parser.parse_args()
    deal = json.loads(args.deal.read_text(encoding="utf-8"))
    if evaluate(deal)["stars"] == 5:
        save_deal(ROOT / ".market", deal)
    ledger = (deal.get("settled") or {}).get("ledger")
    identity = {"order": (deal.get("order") or {}).get("order"), "ledger": ledger,
                "task": (deal.get("receipt") or {}).get("task")}
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    folder = ROOT / "logs" / "settlement-reviews"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (key + ".json")
    if path.exists():
        result = json.loads(path.read_text(encoding="utf-8"))["result"]
    else:
        result = evaluate(deal)
        record = {"reviewed_at": datetime.now(timezone.utc).isoformat(), "identity": identity,
                  "source": deal, "result": result, "signature_verification": "performed by kit, not repeated here"}
        with path.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    # Posting failure must not discard the review or invent a successful post.
    try:
        status = queue_report(ROOT / ".market", deal, result)
    except (OSError, ValueError, TypeError) as error:
        status = f"queue_error: {type(error).__name__}: {error}"
    note = buyer_note(ROOT / ".market")
    (ROOT / "logs" / "forum-policy").mkdir(parents=True, exist_ok=True)
    attempt = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    (ROOT / "logs" / "forum-policy" / f"{attempt}-{key[:8]}.json").write_text(
        json.dumps({"ledger": ledger, "status": status, "buyer_note": note, "posted": "left to kit outbox"}) + "\n", encoding="utf8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
