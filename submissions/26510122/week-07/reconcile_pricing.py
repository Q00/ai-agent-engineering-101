"""Match issued seller receipts to authoritative Round 2 ledger results.

Run periodically; a stored ledger cursor makes repeated scans incremental.
Rows with no matching receipt remain unresolved or receipt-pending.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from urllib.request import urlopen

from pricing import PricingRecorder


def ledger_page(market: str, after: int) -> list[dict]:
    with urlopen(f"{market.rstrip('/')}/api/ledger?after={after}", timeout=20) as response:
        page = json.load(response)
    if not isinstance(page, list):
        raise ValueError("unexpected ledger response")
    return page


def seek_first_relevant_entry(market: str, earliest_at: str | None) -> int:
    """Skip entries older than the first local attempt, retaining an hour's margin."""
    started = datetime.fromisoformat(earliest_at).timestamp() if earliest_at else datetime.now(timezone.utc).timestamp()
    target = started - 3600
    high = 1
    while True:
        page = ledger_page(market, high)
        if not page or float(page[0]["ts"]) >= target:
            break
        high *= 2
    low = 0
    while low < high:
        middle = (low + high) // 2
        page = ledger_page(market, middle)
        if page and float(page[0]["ts"]) < target:
            low = middle + 1
        else:
            high = middle
    return low


def reconcile(recorder: PricingRecorder, market: str, shop: str, max_pages: int = 1000) -> dict:
    cursor = recorder.ledger_cursor()
    if cursor == 0:
        cursor = seek_first_relevant_entry(market, recorder.earliest_attempt_at())
    matched = 0
    counted = 0
    complete = False
    for _ in range(max_pages):
        page = ledger_page(market, cursor)
        if not page:
            complete = True
            break
        for entry in page:
            seq = int(entry["seq"])
            if seq <= cursor:
                raise ValueError("ledger page did not advance")
            if entry.get("round") == 2 and entry.get("kind") == "result":
                data = entry.get("data") or {}
                receipt = data.get("receipt") or {}
                task_id = receipt.get("task")
                if isinstance(task_id, str) and receipt.get("shop") == shop:
                    trade = recorder.get(task_id)
                    if (trade and trade["receipt_issued"]
                            and receipt.get("item") == trade["item"]
                            and receipt.get("price") == trade["final_price"]):
                        checks = data.get("checks") or {}
                        accepted = bool(data.get("verified")) and bool(checks) and all(
                            value is True for value in checks.values())
                        recorder.market_result(task_id, accepted, f"ledger seq {seq}")
                        matched += 1
                        counted += int(accepted)
            cursor = seq
        recorder.set_ledger_cursor(cursor)
        if len(page) < 500:
            complete = True
            break
    return {"cursor": cursor, "matched": matched, "counted": counted, "complete": complete}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", default="https://market.ourolabs.ai")
    parser.add_argument("--shop", default="Luna Shop")
    parser.add_argument("--db", default="state/pricing_state.sqlite")
    parser.add_argument("--max-pages", type=int, default=1000)
    args = parser.parse_args()
    print(json.dumps(reconcile(PricingRecorder(args.db), args.market, args.shop, args.max_pages)))


if __name__ == "__main__":
    main()
