"""Held A/B goods that cannot clear at the market median stop new buying."""

from __future__ import annotations

from r3.market_files import Unit
from r3.overpriced import stuck

NOW = 1_794_600_000.0


def sofa(name: str, cost: int, *, grade: str = "A", hours: float = 5) -> Unit:
    return Unit(
        unit=name,
        sku="G24f5f979",
        kind="goods",
        list_price=3_820_000,
        cost=cost,
        grade=grade,
        supplied_at=NOW - hours * 3600,
    )


def median(_sku: str) -> float:
    return 2_442_240.0


def test_mostly_stuck_sku_is_blocked() -> None:
    units = [sofa("a", 2_429_038), sofa("b", 2_500_000), sofa("c", 1_900_000)]
    rows = stuck(units, median, NOW)
    assert rows["G24f5f979"]["stuck_units"] == 2


def test_sku_with_mostly_sellable_units_keeps_buying() -> None:
    units = [sofa("a", 2_500_000), sofa("b", 2_450_000)] + [
        sofa(f"s{i}", 1_900_000) for i in range(3)
    ]
    assert stuck(units, median, NOW) == {}


def test_c_units_aged_floors_and_no_market_price() -> None:
    # C units never count; a unit held 48 h has a floor of cost itself.
    units = [
        sofa("c1", 3_000_000, grade="C"),
        sofa("c2", 3_000_000, grade="C"),
        sofa("old", 2_430_000, hours=50),
        sofa("new", 2_430_000),
    ]
    assert stuck(units, median, NOW) == {}
    assert (
        stuck([sofa("a", 3_000_000), sofa("b", 3_000_000)], lambda _s: None, NOW) == {}
    )


def test_sku_we_still_sell_keeps_its_restock() -> None:
    units = [sofa("a", 2_500_000), sofa("b", 2_600_000)]
    assert stuck(units, median, NOW, {"G24f5f979": 3}) == {}
    assert "G24f5f979" in stuck(units, median, NOW, {"G24f5f979": 2})
