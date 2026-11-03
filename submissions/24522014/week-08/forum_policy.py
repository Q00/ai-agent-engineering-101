"""Queue a bounded factual buyer report using the course outbox contract."""

from datetime import datetime, timezone
import json
from pathlib import Path
import time

from market_state import read, write


def queue_report(md, deal, result, now=None):
    now = time.time() if now is None else now
    settled, receipt = (deal.get(k) or {} for k in ("settled", "receipt"))
    ledger = settled.get("ledger")
    if result.get("stars") != 5 or type(ledger) is not int or ledger <= 0:
        return "insufficient_evidence"
    price, value = receipt.get("price"), settled.get("value")
    if type(price) not in (int, float) or type(value) not in (int, float) or not settled.get("sku"):
        return "insufficient_evidence"
    md = Path(md)
    md.mkdir(parents=True, exist_ok=True)
    state = read(md / "forum-policy.json", {"ledger_refs": [], "last_at": 0, "day": "", "count": 0})
    if ledger in state["ledger_refs"]:
        return "already_queued"
    day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
    count = state["count"] if state["day"] == day else 0
    if count >= 4 or now-state["last_at"] < 3600:
        return "rate_limited"
    text = (f"Buyer settlement report #{ledger}: {str(settled['sku'])[:80]}, paid {price} credits, "
            f"market value at settlement {value}, initial surplus {value-price}. "
            f"Recorded grade {settled.get('grade')}; warranty {settled.get('warranty_hours') or 0} h. "
            "Receipt and order matched. Later breakage/refunds are not assessed in this report.")
    message = {"post": text, "refs": [ledger], "as": "buyer"}
    # One append is the kit's interface; the kit handles sending/rate-limit retries.
    with (md / "outbox.jsonl").open("a", encoding="utf8") as stream:
        stream.write(json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n")
    write(md / "forum-policy.json", {"ledger_refs": (state["ledger_refs"]+[ledger])[-1000:],
                                     "last_at": now, "day": day, "count": count+1})
    return "queued"
