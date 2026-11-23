"""Store personal trade experience by shop name, without discarding old entries."""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def load_memory(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(data, list):
        raise ValueError("memory must be a JSON array; existing memory was not replaced")
    for row in data:
        if (not isinstance(row, dict) or not isinstance(row.get("shop_name"), str)
                or type(row.get("stars")) is not int or not 1 <= row["stars"] <= 5):
            raise ValueError("invalid trade in memory; existing memory was not replaced")
    return data


def remember(path: Path, *, shop_name: str, item: str, budget: int,
             receipt: dict | None, outcome: str, stars: int, review: str):
    if type(stars) is not int or not 1 <= stars <= 5:
        raise ValueError("stars must be an integer from 1 to 5")
    rows = load_memory(path)
    rows.append({
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "shop_name": shop_name, "item": item, "budget": budget,
        "price": receipt.get("price") if receipt else None,
        "outcome": outcome, "stars": stars, "review": review,
        "receipt": receipt,
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    # A crash while writing must not truncate the previous memory file.
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(rows, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def evaluate_trade(trade: dict, *, item: str, budget: int) -> tuple[int, str, str]:
    """Rate price against budget, not against an assumed market price.

    Receipt signature verification stays with the kit. A suspicious receipt is
    retained unchanged as evidence and is rated separately from normal failure.
    """
    receipt = trade.get("receipt")
    if trade.get("error"):
        return 2, "unavailable", f"Connection/protocol failure: {trade['error']}"
    if trade.get("state") != "TASK_STATE_COMPLETED":
        return 3, "no_deal", f"No deal within budget {budget}; negotiation ended without a receipt."
    if not isinstance(receipt, dict):
        return 1, "invalid_receipt", "Completed task did not include a receipt data Part."
    price = receipt.get("price")
    if (type(price) not in (int, float) or not 0 <= price <= budget
            or not float(price).is_integer() or receipt.get("item") != item
            or receipt.get("task") != trade.get("task_id")
            or trade.get("last_offer") is None or price > trade["last_offer"]):
        return 1, "invalid_receipt", "Receipt does not match the item/task, or exceeds budget or accepted offer."
    stars = 5 if price * 100 <= budget * 80 else 4 if price * 100 <= budget * 90 else 3
    return stars, "deal", (f"Bought {item} for {price:g} within budget {budget}; "
                           f"used {price / budget:.0%} of budget; receipt fields match the negotiation.")
