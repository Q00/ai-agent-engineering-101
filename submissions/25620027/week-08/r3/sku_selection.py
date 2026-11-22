"""Shrink actual settled-SKU evidence toward the existing shop fallback."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Self

from pydantic import Field, model_validator

from market_a2a.models import FrozenModel

if TYPE_CHECKING:
    from collections.abc import Mapping

ENABLED_FILE: Final = "r3-sku-selection.enabled"
MIN_KNOWN: Final = 3
SKU_PRIOR: Final = 10


class SkuEvidence(FrozenModel):
    """Only uniquely identified, mature goods with known final credit qualify."""

    known_deals: int = Field(ge=0, strict=True)
    paid_share_sum: float = Field(ge=0, allow_inf_nan=False, strict=True)
    loss_sum: float = Field(ge=0, allow_inf_nan=False, strict=True)

    @model_validator(mode="after")
    def bounded_observations(self) -> Self:
        """Each observed paid share is in [0,1] and loss in [0,2]."""
        if (
            self.paid_share_sum > self.known_deals
            or self.loss_sum > 2 * self.known_deals
        ):
            message = "SKU evidence exceeds physical observation bounds"
            raise ValueError(message)
        return self


@dataclass(frozen=True, slots=True)
class SelectionTerms:
    """Price and loss terms replace, rather than multiply, their shop terms."""

    price: float
    loss: float


def selection_terms(
    row: SkuEvidence | None,
    ratios: Mapping[str, float],
    url: str,
    baseline: SelectionTerms,
    *,
    enabled: bool,
) -> SelectionTerms:
    """Use observed SKU credit without inventing the currently offered grade.

    This is a SKU mixture, not grade-conditioned evidence. Shop advertisements
    do not identify a current unit's grade. Sparse and unknown SKUs retain the
    old path; the caller checks the minimum count before invoking this function.
    """
    if not enabled or row is None or row.known_deals < MIN_KNOWN:
        return baseline
    usual = statistics.median(ratios.values()) if ratios else 0.72
    base_ratio = ratios.get(url.rstrip("/"), usual)
    count = row.known_deals
    ratio = (row.paid_share_sum + SKU_PRIOR * base_ratio) / (count + SKU_PRIOR)
    # Recover the already shrunk shop loss from its factor; then shrink SKU
    # losses toward it. Applying both factors would double-count the same breaks.
    base_loss = (1 - baseline.loss) * max(0.05, 1 - base_ratio)
    loss = (row.loss_sum + SKU_PRIOR * base_loss) / (count + SKU_PRIOR)
    return SelectionTerms(
        price=max(0.3, min(2.0, (1 - ratio) / max(0.05, 1 - usual))),
        loss=max(0.5, 1 - loss / max(0.05, 1 - ratio)),
    )


def scale_weight(
    weight: float, baseline: SelectionTerms, refined: SelectionTerms
) -> float:
    """Keep inactive and sparse paths bit-identical, including score ties."""
    if baseline == refined:
        return weight
    return weight * refined.price * refined.loss / (baseline.price * baseline.loss)
