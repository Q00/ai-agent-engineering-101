"""Proposal 2 (approved 11-08): our own confirmed sales can stand in for the market median test in restock.

The market median is read from a bounded sample of public rows in which our own sales under the price cap sit near .75 of list,
so the test `median <= .75 x list x (1 + storage)` can block a SKU we sell at a profit. A goods SKU with 3 or more of our
completed sales in the last 24 hours, whose Book (our paid price minus our real unit cost) sums above zero, skips that test.
Nothing else in restock changes. Read on every restock run from median_bypass.json; missing or broken means off.
"""

from collections import defaultdict
import json
import math
from pathlib import Path

from market_state import task_file

CONFIG = Path(__file__).resolve().parent / "median_bypass.json"
WINDOW, MIN_SALES = 86400, 3
# A payment completes before an offer expires plus this grace, as in the seller's hold rule.
GRACE = 90


def enabled(path=None):
    try:
        return json.loads(Path(CONFIG if path is None else path).read_text(encoding="utf-8")).get("enabled") is True
    except (OSError, ValueError, AttributeError):
        return False


def amount(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def confirmed_counts(md, now):
    """{sku: (completed sales, Book sum)} in (now - 24 h, now], or None when an offer file does not parse. Before, for the bypass: SKUs whose completed sales in (now - 24 h, now] number 3 or more with a positive Book sum.
    A SKU with any unreadable or incomplete record in the window goes back to the median test; an offer file that does not parse
    could name any SKU, so it sends every SKU back."""
    md = Path(md)
    books, broken = defaultdict(list), set()
    for path in (md / "signed-offers").glob("*.json"):
        try:
            offer = json.loads(path.read_text(encoding="utf8"))
        except (OSError, ValueError):
            return None
        if not isinstance(offer, dict) or not isinstance(offer.get("sku"), str):
            return None
        sku, expires = offer["sku"], offer.get("expires_at")
        if type(expires) not in (int, float) or not math.isfinite(expires):
            broken.add(sku)
            continue
        if expires + GRACE < now - WINDOW:
            continue  # could not have completed inside the window
        try:
            event = json.loads(task_file(md, "checkout-events", offer["task"]).read_text(encoding="utf8")) if isinstance(offer.get("task"), str) else None
        except FileNotFoundError:
            continue  # never paid
        except (OSError, ValueError):
            broken.add(sku)
            continue
        if not isinstance(event, dict):
            broken.add(sku)
            continue
        if event.get("status") != "completed":
            continue
        at = event.get("at")
        if type(at) not in (int, float) or not math.isfinite(at):
            broken.add(sku)
            continue
        if not now - WINDOW < at <= now:
            continue
        if not (amount(offer.get("price")) and amount(offer.get("cost"))):
            broken.add(sku)
            continue
        books[sku].append(offer["price"] - offer["cost"])
    return {sku: (len(rows), sum(rows)) for sku, rows in books.items() if sku not in broken}


def confirmed_skus(md, now):
    """SKUs whose completed sales in (now - 24 h, now] number 3 or more with a positive Book sum; empty when an offer file does not parse."""
    counts = confirmed_counts(md, now)
    if counts is None:
        return set()
    return {sku for sku, (n, book) in counts.items() if n >= MIN_SALES and book > 0}


def for_run(md, now):
    """The bypass set for one restock run: empty when off or on any error, which is today's behaviour."""
    try:
        return set(confirmed_skus(md, now)) if enabled() else set()
    except Exception:
        return set()
