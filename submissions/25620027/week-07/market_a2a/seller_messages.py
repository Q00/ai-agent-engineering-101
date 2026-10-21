"""Publish already-authorized quote facts and signed sale artifacts over A2A."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from a2a.types import Message, Part
from google.protobuf.json_format import Parse
from google.protobuf.struct_pb2 import Value

from market_a2a.comparison import compare_price, is_fresh
from market_a2a.models import json_value

if TYPE_CHECKING:
    from a2a.server.tasks import TaskUpdater

    from market_a2a.comparison import ComparisonSnapshot
    from market_a2a.models import Product
    from market_a2a.receipts import ReceiptSigner


@dataclass(frozen=True, slots=True)
class QuoteNotice:
    """The commercial facts needed to publish one valid quote."""

    product: Product
    price: int
    previous_ask: int | None
    coupon_applied: bool
    snapshot: ComparisonSnapshot | None


@dataclass(frozen=True, slots=True)
class AgreedSale:
    """A sale whose amount, product and task have passed the action guards."""

    product: Product
    price: int
    task_id: str
    shop: str


def quote_message(updater: TaskUpdater, notice: QuoteNotice) -> Message:
    """Keep coupon credit and dated comparison facts separate from the actual ask."""
    reduction = (
        notice.previous_ask - notice.price if notice.previous_ask is not None else 0
    )
    discount = min(5, reduction) if notice.coupon_applied else 0
    coupon_text = ""
    price_final = notice.price == notice.product.reserve
    if notice.previous_ask is None and not price_final:
        coupon_text = (
            " R2WELCOME: up to 5 price units of the first negotiated "
            "reduction are coupon credit. Larger reductions are possible."
        )
    elif notice.coupon_applied:
        coupon_text = (
            f" R2WELCOME coupon credit: {discount} within a total "
            f"reduction of {reduction} price units."
        )
    comparison = compare_price(notice.product, notice.snapshot)
    comparison_text = ""
    if notice.previous_ask is None and comparison.observed_shop_count:
        comparison_text = "".join(
            (
                f" Observed opening-ask comparison: {comparison.observed_shop_count} ",
                f"other active shops; observed minimum {comparison.lowest_peer_ask}; ",
                f"our new-opening ceiling {comparison.ceiling}. ",
                "Dated comparison: ",
                "https://ax-week07.treasuresky.xyz/#price-comparison .",
            )
        )
    return updater.new_agent_message(
        [
            Part(
                text="".join(
                    (
                        notice.product.name,
                        ".",
                        coupon_text,
                        comparison_text,
                        (
                            " Final price; no further discount or coupon is available."
                            if price_final
                            else ""
                        ),
                        f" Reply with exactly {notice.price} to complete the purchase.",
                        f" Asking price: {notice.price}",
                    )
                )
            )
        ],
        metadata={
            "sku": notice.product.sku,
            "item": notice.product.kind,
            "name": notice.product.name,
            "ask": notice.price,
            "price_final": price_final,
            "coupon_code": "R2WELCOME",
            "coupon_discount": discount,
            "total_reduction": reduction,
            "coupon_applied": "true" if notice.coupon_applied else "false",
            "price_comparison": json_value(comparison),
            "comparison_observed_at": (
                notice.snapshot.observed_at.isoformat()
                if notice.snapshot is not None and is_fresh(notice.snapshot)
                else ""
            ),
        },
    )


async def complete_sale(
    updater: TaskUpdater, sale: AgreedSale, signer: ReceiptSigner | None
) -> None:
    """Sign the accepted amount once and publish an unchanged receipt data Part."""
    receipt = {
        "seller": sale.shop,
        "item": sale.product.kind,
        "sku": sale.product.sku,
        "price": sale.price,
    }
    if signer is not None:
        signed = signer.sign(
            shop=sale.shop,
            item=sale.product.kind,
            price=sale.price,
            task=sale.task_id,
        )
        receipt = signed.model_dump()
        receipt["sku"] = sale.product.sku
    await updater.add_artifact(
        [Part(data=Parse(json.dumps(receipt), Value()), media_type="application/json")],
        name="receipt",
    )
    await updater.complete(
        updater.new_agent_message(
            [
                Part(
                    text=" ".join(
                        (
                            f"Deal: {sale.product.name} at {sale.price}.",
                            "Please leave an honest review of the price and receipt.",
                        )
                    )
                )
            ]
        )
    )
