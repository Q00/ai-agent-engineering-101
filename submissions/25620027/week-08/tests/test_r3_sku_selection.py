"""Actual settled-SKU price/loss selection with sparse and safety fallbacks."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from pydantic import JsonValue, TypeAdapter, ValidationError

from r3 import buyer
from r3.market_files import CatalogItem, Order, ShopEntry, ShopOffer
from r3.sku_selection import SelectionTerms, SkuEvidence, scale_weight, selection_terms
from tests.test_r3_saved_feedback import (
    BAD,
    append_sale,
    publish,
    setup_sources,
    sync_facts,
)

if TYPE_CHECKING:
    from pathlib import Path


def pick(
    *, evidence: dict[str, dict[str, SkuEvidence]], food: bool = False
) -> buyer.Choice:
    """Exercise the production candidate loop with two available SKUs."""
    result = buyer.choose(
        Order(
            order="distinct",
            acceptable=("cheap", "costly"),
            kind="food" if food else "goods",
        ),
        (
            ShopEntry(
                name="one",
                url=BAD,
                offers=tuple(ShopOffer(sku=s, qty=1) for s in ("cheap", "costly")),
            ),
        ),
        {s: CatalogItem(sku=s, list_price=100) for s in ("cheap", "costly")},
        {},
        {},
        ratios={BAD: 0.72},
        surplus_score=True,
        sku_evidence=evidence,
    )
    assert result is not None
    return result


def test_same_shop_skus_rank_by_actual_discount() -> None:
    rows = {
        BAD: {
            "cheap": SkuEvidence(known_deals=20, paid_share_sum=12.0, loss_sum=0.0),
            "costly": SkuEvidence(known_deals=20, paid_share_sum=16.0, loss_sum=0.0),
        }
    }
    assert pick(evidence=rows).sku == "cheap"


def test_refund_loss_can_reverse_price_preference() -> None:
    rows = {
        BAD: {
            "cheap": SkuEvidence(known_deals=20, paid_share_sum=12.0, loss_sum=10.0),
            "costly": SkuEvidence(known_deals=20, paid_share_sum=14.0, loss_sum=0.0),
        }
    }
    assert pick(evidence=rows).sku == "costly"


def test_sparse_history_retains_exact_score_and_tie() -> None:
    baseline = pick(evidence={})
    sparse = pick(
        evidence={
            BAD: {"cheap": SkuEvidence(known_deals=2, paid_share_sum=0.2, loss_sum=0.0)}
        }
    )
    assert (sparse.sku, sparse.score, sparse.price_term) == (
        baseline.sku,
        baseline.score,
        baseline.price_term,
    )


def test_food_does_not_borrow_goods_loss_or_price() -> None:
    baseline = pick(evidence={}, food=True)
    other = pick(
        evidence={
            BAD: {
                "cheap": SkuEvidence(known_deals=50, paid_share_sum=1.0, loss_sum=0.0)
            }
        },
        food=True,
    )
    assert (other.sku, other.score) == (baseline.sku, baseline.score)


def test_disabled_refinement_is_bit_identical() -> None:
    base = SelectionTerms(price=0.9132481, loss=0.671)
    refined = selection_terms(
        SkuEvidence(known_deals=30, paid_share_sum=15.0, loss_sum=0.0),
        {BAD: 0.7},
        BAD,
        base,
        enabled=False,
    )
    assert scale_weight(0.83728471, base, refined) == 0.83728471


def test_replacement_does_not_multiply_shop_loss_twice() -> None:
    base = SelectionTerms(price=1.0, loss=0.5)
    refined = selection_terms(
        SkuEvidence(known_deals=100, paid_share_sum=72.0, loss_sum=0.0),
        {BAD: 0.72},
        BAD,
        base,
        enabled=True,
    )
    # A hundred useful units can recover the old shop loss; it is not reapplied.
    assert scale_weight(0.5, base, refined) > 0.95


@pytest.mark.parametrize(
    ("paid", "loss"), [(4.0, 0.0), (0.0, 7.0), (float("nan"), 0.0)]
)
def test_incoherent_evidence_rejected(paid: float, loss: float) -> None:
    with pytest.raises(ValidationError):
        _ = SkuEvidence(known_deals=3, paid_share_sum=paid, loss_sum=loss)


def test_publisher_groups_actual_ledger_sku_and_normalizes_list(tmp_path: Path) -> None:
    paths = setup_sources(tmp_path)
    ledger, facts, catalog, output = paths
    _ = catalog.write_text(
        json.dumps(
            {
                "sku": {"kind": "goods", "list_price": 100},
                "other": {"kind": "goods", "list_price": 200},
            }
        )
    )
    append_sale(ledger, 1, sku="sku", unit="one", paid=60)
    append_sale(ledger, 2, sku="other", unit="two", paid=160)
    sync_facts(ledger, facts)
    publish(paths)
    cache = buyer.SavedFeedback.model_validate_json(
        output.with_name(buyer.SKU_FEEDBACK_FILE).read_bytes()
    )
    assert cache.skus[BAD]["sku"].paid_share_sum == 0.6
    assert cache.skus[BAD]["other"].paid_share_sum == 0.8


def test_unknown_and_duplicate_units_do_not_become_sku_evidence(tmp_path: Path) -> None:
    paths = setup_sources(tmp_path)
    ledger, facts, _, output = paths
    append_sale(ledger, 1, unit="same")
    append_sale(ledger, 2, unit="same")
    append_sale(ledger, 3, unit="")
    sync_facts(ledger, facts)
    publish(paths)
    assert (
        buyer.SavedFeedback.model_validate_json(
            output.with_name(buyer.SKU_FEEDBACK_FILE).read_bytes()
        ).skus
        == {}
    )


def test_old_feedback_cache_uses_default_empty_sku_map() -> None:
    cache = buyer.SavedFeedback.model_validate(
        {"generated_at": 100.0, "ledger_latest_at": 100.0, "shops": {}}
    )
    assert cache.skus == {}


def test_primary_cache_retains_previous_strict_schema(tmp_path: Path) -> None:
    paths = setup_sources(tmp_path)
    ledger, facts, _, output = paths
    append_sale(ledger, 1, unit="one")
    sync_facts(ledger, facts)
    publish(paths)
    assert set(
        TypeAdapter(dict[str, JsonValue]).validate_json(output.read_bytes())
    ) == {
        "generated_at",
        "ledger_latest_at",
        "maturity_seconds",
        "owner_control_until",
        "shops",
    }
