"""Publish a bounded human-readable view of the same decision memory."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING

from market_a2a.memory_retention import MAX_PER_SHOP, MAX_SHOPS, TTL

if TYPE_CHECKING:
    from market_a2a.memory_models import ShopKnowledge, Trade


def publish_context(
    shops: dict[str, tuple[Trade, ...]],
    profiles: dict[str, ShopKnowledge],
    path: Path,
) -> None:
    """Overwrite the derived view; never load its prose as agent instructions."""
    rows = [
        "# Agent context — own observations only",
        f"Updated: {datetime.now(UTC).isoformat()}",
        f"Source: {path.name}; this generated view is not a payment certificate.",
        f"Retention: {TTL.days} days, {MAX_PER_SHOP}/shop, {MAX_SHOPS} shops; 2 MiB.",
        "Read the JSON before each order; lookup exact evidence within that snapshot.",
    ]
    for name in dict.fromkeys((*profiles, *shops)):
        trades = shops.get(name, ())
        # JSON strings keep external names as data, rather than Markdown headings.
        identity = hashlib.sha256(name.encode()).hexdigest()[:16]
        rows.append(
            " ".join(
                (
                    f"\nShop {identity}: {json.dumps(name, ensure_ascii=False)};",
                    f"{len(trades)} retained observations.",
                )
            )
        )
        profile = profiles.get(name)
        if profile is not None:
            rows.append(profile.model_dump_json())
        for trade in trades[-3:]:
            record = trade.model_dump_json()
            reference = hashlib.sha256(record.encode()).hexdigest()
            rows.append(
                " ".join(
                    (
                        f"- evidence {reference}; item={trade.item!r};",
                        f"negotiated={trade.purchase.completed};",
                        f"price={trade.purchase.price}; observed={trade.observed_at};",
                        "settlement=not_certified",
                    )
                )
            )
    target = path.with_suffix(".context.md")
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            _ = stream.write("\n".join(rows) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            _ = temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
