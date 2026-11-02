"""Evidence-based post-settlement review hook. No model or network calls."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time

from r3_policy import buyer_accept

ROOT = Path(__file__).resolve().parent


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
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
