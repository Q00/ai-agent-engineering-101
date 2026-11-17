"""Review hook: stars from the market's own receipt, text from facts, no budget.

Runs after every paid deal with ``deal.json``. The market already judged the grade
claim (``claim_honest``) and certified the grade and shelf life, so the review states
those facts. The private budget never appears in public text. Each reviewed deal is
also appended to our own ``r3-deals.jsonl`` for the forum summary and the report.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Final

from r3.market_files import Deal, read_deal

if TYPE_CHECKING:
    from pathlib import Path

DEALS_FILE: Final = "r3-deals.jsonl"
HOURS: Final = 3600.0
NEUTRAL: Final = 3


def stars_for(deal: Deal, now: float) -> tuple[int, str]:  # noqa: C901, PLR0912
    """Return the star rating and a factual sentence for a settled deal."""
    settled, order = deal.settled, deal.order
    receipt = deal.receipt
    price = int(float(str(receipt.get("price", 0)))) if receipt else 0
    name = settled.name or str(receipt.get("item", "")) if receipt else settled.name
    facts: list[str] = []
    stars = 4
    if settled.claim_honest is False:
        stars = 1
        facts.append(
            f"The shop claimed grade {settled.claim}; "
            f"the market certified {settled.grade}."
        )
    else:
        if settled.claim:
            facts.append(f"Grade claim {settled.claim} matched the certificate.")
        if settled.grade == "A":
            stars = 5
        elif settled.grade == "B":
            stars = 4
        elif settled.grade:
            stars = 3
    if order.kind == "food" and settled.expires_at is not None:
        left = (settled.expires_at - now) / HOURS
        facts.append(f"{left:.0f} h of shelf life left at settlement.")
        if order.use_within_hours is not None and left < float(order.use_within_hours):
            stars = min(stars, 2)
            facts.append(f"That is under the needed {order.use_within_hours} h.")
    if order.kind == "goods":
        if settled.warranty_hours:
            facts.append(f"Warranty {settled.warranty_hours:g} h.")
        else:
            facts.append("No warranty offered.")
            stars = min(stars, 4)
    if receipt.get("cert") is False or (settled.grade == "" and stars > NEUTRAL):
        stars = min(stars, NEUTRAL)
        facts.append("No certificate was shown.")
    if settled.value and price:
        facts.append(
            f"Paid {price}, {100 * price // settled.value}% of the list price."
        )
    text = f"Settled {name} ({settled.sku}) grade {settled.grade or '?'}. " + " ".join(
        facts
    )
    return max(1, min(5, stars)), text[:480]


def record_deal(folder: Path, deal: Deal, stars: int, now: float) -> None:
    """Append the reviewed deal to our own line ledger (beside .market/)."""
    settled, order, receipt = deal.settled, deal.order, deal.receipt
    row = {
        "ts": now,
        "order": order.order,
        "shop": settled.shop,
        "sku": settled.sku,
        "name": settled.name,
        "grade": settled.grade,
        "claim": settled.claim,
        "claim_honest": settled.claim_honest,
        "value": settled.value,
        "price": receipt.get("price"),
        "ledger": settled.ledger,
        "warranty_hours": settled.warranty_hours,
        "kind": order.kind,
        "expires_at": settled.expires_at,
        "stars": stars,
    }
    try:
        with (folder.resolve().parent / DEALS_FILE).open(
            "a", encoding="utf-8"
        ) as stream:
            _ = stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def run(deal_path: Path, now: float | None = None) -> str:
    """Read deal.json, rate it, record it and return the JSON line for the kit."""
    stamp = now if now is not None else time.time()
    deal = read_deal(deal_path)
    stars, text = stars_for(deal, stamp)
    record_deal(deal_path.parent, deal, stars, stamp)
    return json.dumps({"stars": stars, "review": text}, ensure_ascii=False)
