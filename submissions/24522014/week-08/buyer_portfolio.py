"""Own settled purchases and conservative breakage/refund attribution."""

import time
from pathlib import Path

from market_state import read, write


def save_deal(md, deal, now=None):
    order, receipt, settled = (deal.get(k) or {} for k in ("order", "receipt", "settled"))
    ledger = settled.get("ledger")
    if type(ledger) is not int or ledger <= 0 or not settled.get("shop") or not settled.get("sku"):
        return
    path = Path(md) / "buyer-deals" / f"{ledger}.json"
    if path.exists():
        return
    write(path, {"ledger": ledger, "purchased_at": time.time() if now is None else now,
                 "order": order.get("order"), "shop": settled["shop"], "unit": settled.get("unit"),
                 "sku": settled["sku"], "name": settled.get("name"), "price": receipt.get("price"),
                 "value_at_purchase": settled.get("value"), "grade": settled.get("grade"),
                 "warranty_hours": settled.get("warranty_hours") or 0,
                 "fault": None, "refund_confirmed": False})


def reconcile(md, events, now=None):
    now = time.time() if now is None else now
    folder = Path(md) / "buyer-deals"
    records = [(p, read(p)) for p in sorted(folder.glob("*.json"))[-1000:]]
    for event in events:
        if event.get("round") != 3 or event.get("kind") not in ("broken", "refund"):
            continue
        data = event.get("data") or {}
        # A name/SKU alone cannot distinguish repeated purchases from different shops.
        matches = [(p, r) for p, r in records if (
            (data.get("unit") and data["unit"] == r.get("unit")) or
            (data.get("settlement_ledger") == r["ledger"]))]
        if len(matches) != 1:
            continue
        path, record = matches[0]
        if event["kind"] == "broken":
            record["fault"] = {"ledger": event.get("seq"), "at": event.get("ts"), "matched_by": "unit_or_settlement"}
        else:
            record["refund_confirmed"] = True
            record["refund_ledger"] = event.get("seq")
        write(path, record)
    return [r for _, r in records if 0 <= now-r["purchased_at"] <= 7*86400]
