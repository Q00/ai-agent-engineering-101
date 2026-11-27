"""v1.31 (2026-11-05): a SKU's units above the category level (stock_target / skus_per_category, rounded up) do not
fill the category need. Live case at 07:04: the deployment computer held 11 sofa G5e3784e6 (slow) and 2 G00327e80, so the sofa need
was 3 and v1.30 put all 3 on G8ec51935, while the market settled G8ec51935 235 times in 3 h (24 at our shop). With
the level 6 the need is 8. The food and storage caps keep counting every unit on hand."""

from tests import test_v130_sku_fill as T

P, SOFA, BED = T.P, T.SOFA, T.BED


def _units(cat, counts):
    rows = {s: lp for s, lp, _ in T.SOFA_SKUS + T.BED_SKUS}
    return [T._unit(s, cat, rows[s]) for s, n in counts.items() for _ in range(n)]


def _plan(units, bed_k=3, balance=200_000_000):
    catalog, demand, stats = T._world()
    pol = T._pol()
    pol["categories"][BED]["skus_per_category"] = bed_k
    buy, why = P.restock_plan(
        T._inv(units, balance), demand, catalog, pol, stats, activity={}, now=T.NOW
    )
    return {b["sku"]: b["qty"] for b in buy}, why


def _sofa(q):
    return tuple(q.get(s) for s, _, _ in T.SOFA_SKUS)


def _bed(q):
    return tuple(q.get(s) for s, _, _ in T.BED_SKUS)


def test_a_slow_pile_does_not_block_the_fast_skus():
    # 07:04 live: G8ec51935 0 (3 sold by 06:59), G00327e80 2, G5e3784e6 11; level ceil(16 / 3) = 6, over 5
    q, why = _plan(_units(SOFA, {"G00327e80": 2, "G5e3784e6": 11}))
    assert _sofa(q) == (5, 3, None)  # need 16 - 13 + 5 = 8 (qty_per_buy 8)
    assert P.level_fill(16 - 13, [0, 2, 11]) == [3, 0, 0]  # what v1.30 bought at 07:04


def test_without_a_pile_the_need_is_unchanged():
    q, why = _plan(_units(SOFA, {"G8ec51935": 5, "G00327e80": 5, "G5e3784e6": 4}))
    assert _sofa(q) == (1, 1, None)  # need 16 - 14 = 2 as before, so k = 2 (the top 2 SKUs)


def test_a_category_at_or_over_its_level_everywhere_buys_nothing():
    for counts in (
        {"G8ec51935": 6, "G00327e80": 6, "G5e3784e6": 11},
        {"G8ec51935": 7, "G00327e80": 7, "G5e3784e6": 7},
    ):
        q, why = _plan(_units(SOFA, counts))
        assert _sofa(q) == (None, None, None)


def test_the_level_follows_skus_per_category():
    units = _units(BED, {"G1515d951": 9, "G07ec17d5": 5})
    q, why = _plan(units, bed_k=3)  # level 7, over 2, need 20 - 14 + 2 = 8
    assert _bed(q) == (None, 2, 6)
    q, why = _plan(units, bed_k=4)  # level 5, over 4, need 10 (3 SKUs in this catalog)
    assert _bed(q) == (None, 3, 7)


def test_a_pile_in_another_category_does_not_count():
    units = _units(SOFA, {"G8ec51935": 5, "G00327e80": 5, "G5e3784e6": 5}) + _units(
        BED, {"G1515d951": 15}
    )
    q, why = _plan(units)
    assert _sofa(q) == (
        1,
        None,
        None,
    )  # sofa need 16 - 15 = 1, the mattress pile is not sofa's


def test_units_without_a_catalog_sku_count_in_full():
    units = [
        {
            "category": SOFA,
            "kind": "goods",
            "cost": 300000,
            "list_price": 570000,
            "grade": "A",
        }
        for _ in range(11)
    ] + _units(SOFA, {"G00327e80": 2})
    q, why = _plan(units)
    assert _sofa(q) == (2, None, 1)  # need 16 - 13 = 3, nothing over a level


def test_the_storage_cap_still_counts_every_unit(monkeypatch):
    monkeypatch.setattr(P, "goods_cap", lambda *a, **k: 14)
    q, why = _plan(_units(SOFA, {"G00327e80": 2, "G5e3784e6": 11}))
    assert _sofa(q) == (1, None, None)  # min(need 8, cap 14 - 13 units on hand) = 1
