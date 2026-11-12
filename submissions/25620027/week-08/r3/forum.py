"""Forum posts: verifiable settlement facts (buyer), honest stock facts (shop).

Buyer-role posts earn the buyer's reputation score through other agents' votes, so
they carry what another buyer or shop can check on the public ledger: SKU, grade,
price as a share of list, the shop, and the ledger sequence numbers as ``refs``. No
rumours, no advertising inside reviews, no requests for votes. The kit sends one
post per five minutes per role; this module keeps its own timestamps so it never
queues more than that.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Final

from pydantic import ValidationError

from market_a2a.models import FrozenModel
from r3.market_files import Inventory, read_jsonl_tail

if TYPE_CHECKING:
    from pathlib import Path

STATE_FILE: Final = "r3-forum-state.json"
BUYER_INTERVAL: Final = 300.0
SHOP_INTERVAL: Final = 1800.0
MAX_POST_CHARS: Final = 900


class ForumState(FrozenModel):
    """Our own posting timestamps and the last deal we reported."""

    last_buyer_post: float = 0.0
    last_shop_post: float = 0.0
    last_deal_ts: float = 0.0


def load_state(path: Path) -> ForumState:
    """Read our posting state; missing means never posted."""
    try:
        return ForumState.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError):
        return ForumState()


def save_state(path: Path, state: ForumState) -> None:
    """Write the posting state atomically."""
    tmp = path.with_suffix(".tmp")
    _ = tmp.write_text(state.model_dump_json(), encoding="utf-8")
    _ = tmp.replace(path)


def buyer_post(
    deals: list[dict[str, object]], shop_names: dict[str, str], market_line: str
) -> tuple[str, list[int]]:
    """One factual post about our recent settled purchases, with ledger refs."""
    parts: list[str] = []
    refs: list[int] = []
    for deal in deals[:6]:
        sku = str(deal.get("sku", ""))
        name = str(deal.get("name", "") or sku)[:40]
        grade = str(deal.get("grade", "?"))
        price = deal.get("price")
        value = deal.get("value")
        shop = str(deal.get("shop", ""))
        shop_name = shop_names.get(shop.rstrip("/"), shop.rsplit("/", 1)[-1] or "shop")
        pct = ""
        if (
            isinstance(price, (int, float))
            and isinstance(value, (int, float))
            and value
        ):
            pct = f" ({100 * int(price) // int(value)}% of list)"
        honest = deal.get("claim_honest")
        claim = (
            ""
            if honest is None
            else (", claim honest" if honest else ", CLAIM MISMATCH")
        )
        warranty = deal.get("warranty_hours")
        extra = (
            f", warranty {warranty:g}h"
            if isinstance(warranty, (int, float)) and warranty
            else ""
        )
        parts.append(
            f"{name} [{sku}] grade {grade}{claim}{extra} at {price}{pct} "
            f"from {shop_name}"
        )
        ledger = deal.get("ledger")
        if isinstance(ledger, int):
            refs.append(ledger)
    text = "Settled purchases, verifiable on the ledger refs: " + "; ".join(parts) + "."
    if market_line:
        text += " " + market_line
    return text[:MAX_POST_CHARS], refs[:12]


def shop_post(inventory: Inventory, shop_name: str, warranty_a: int) -> str:
    """Honest stock summary for buyers reading the forum."""
    categories = {u.category.split(">")[-1] for u in inventory.units if u.category}
    food = sum(1 for u in inventory.units if u.kind == "food")
    goods = len(inventory.units) - food
    sample = ", ".join(sorted(categories)[:12])
    warranty = (
        f"{warranty_a}h warranty on A-grade goods" if warranty_a else "no warranty"
    )
    return (
        f"{shop_name}: {len(inventory.units)} units in stock across {len(categories)} "
        f"categories ({food} food, {goods} goods), e.g. {sample}. Market certificate "
        f"attached to every unit, grade claims match the certificate, {warranty}. "
        "Ask by SKU or name; matching metadata.ask closes at once."
    )[:MAX_POST_CHARS]


def enqueue(market_dir: Path, line: dict[str, object]) -> None:
    """Append one line to the kit's outbox; the kit posts it within 10 seconds."""
    with (market_dir / "outbox.jsonl").open("a", encoding="utf-8") as stream:
        _ = stream.write(json.dumps(line, ensure_ascii=False) + "\n")


def maybe_post(  # noqa: PLR0913 - explicit inputs
    market_dir: Path,
    deals_file: Path,
    state_path: Path,
    *,
    inventory: Inventory,
    shop_name: str,
    warranty_a: int,
    shop_names: dict[str, str],
    market_line: str,
    now: float | None = None,
) -> list[str]:
    """Queue at most one buyer post and one shop post when their intervals allow."""
    stamp = now if now is not None else time.time()
    state = load_state(state_path)
    posted: list[str] = []
    if stamp - state.last_buyer_post >= BUYER_INTERVAL:
        deals = [
            d
            for d in read_jsonl_tail(deals_file, 400_000)
            if isinstance(d.get("ts"), (int, float))
            and float(str(d["ts"])) > state.last_deal_ts
        ]
        if deals:
            text, refs = buyer_post(deals[-6:], shop_names, market_line)
            enqueue(market_dir, {"post": text, "refs": refs, "as": "buyer"})
            state = state.model_copy(
                update={
                    "last_buyer_post": stamp,
                    "last_deal_ts": max(float(str(d["ts"])) for d in deals),
                }
            )
            posted.append("buyer")
    if inventory.units and stamp - state.last_shop_post >= SHOP_INTERVAL:
        enqueue(
            market_dir,
            {
                "post": shop_post(inventory, shop_name, warranty_a),
                "refs": [],
                "as": "shop",
            },
        )
        state = state.model_copy(update={"last_shop_post": stamp})
        posted.append("shop")
    if posted:
        save_state(state_path, state)
    return posted
