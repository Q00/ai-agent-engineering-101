"""Isolate the staged R3 policy from the unchanged running R2 service."""

from __future__ import annotations

import os


def margin_mode() -> bool:
    """Enable only through an explicit future R3 runtime configuration."""
    return os.environ.get("AX_MARKET_ROUND") == "3"


def r3_description(*, signed: bool) -> str:
    """Offer short verifiable commercial facts rather than hidden instructions."""
    receipt = (
        "Ed25519-signed agreed-price receipts" if signed else "Agreed-price receipts"
    )
    parts: tuple[str, ...] = (
        "Items: lamp, chair, bookshelf",
        "Quote: read metadata.ask",
        "Negotiate with a whole-number offer on the same taskId",
        "Matching the current ask accepts that quote",
        receipt,
        "Payment: ACP checkout, followed by market settlement",
        "Discounts are included in the agreed total",
        "Reviews are optional",
        "Terms: /",
        "Receipt total equals the agreed seller price",
    )
    return " | ".join(parts)
