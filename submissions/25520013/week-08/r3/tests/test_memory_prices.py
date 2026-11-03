"""Layer 2'' per-trade price history: only market settles, t exclusive, [t0, t1), duplicates once, abstain before
the record and for unknown pairs, out-of-order ingest, catalog category, to_json/from_json round trip."""

import json

import pytest
from memory import ABSTAIN
from memory.prices import Prices

T = 1_700_000_000
CAT = {"apple": {"category": "fruit", "name": "Apple", "list_price": 100}}


def settle(seq, ts, price, shop="s1", sku="apple", src="market", ledger=None):
    return {
        "seq": seq,
        "ts": ts,
        "type": "settle",
        "src": src,
        "key": f"market:{ledger or seq}",
        "role": "market",
        "mine": False,
        "data": {
            "order": f"o{seq}",
            "buyer": "b",
            "shop": shop,
            "sku": sku,
            "amount": price,
            "ledger": ledger or seq,
        },
    }


def build():
    h = Prices(CAT)
    for e in [
        settle(1, T + 100, 90),
        settle(2, T + 200, 95),
        settle(3, T + 300, 80),
        settle(4, T + 150, 70, shop="s2"),
    ]:
        h.ingest(e)
    return h


def test_last_before_t_exclusive_and_abstain():
    h = build()
    assert (
        h.last_before("s1", "apple", T + 200) == 90
    )  # the settle at exactly t is excluded
    assert h.last_before("s1", "apple", T + 201) == 95
    assert h.last_before("s1", "apple", T + 10_000) == 80
    assert (
        h.last_before("s1", "apple", T + 100) == ABSTAIN
    )  # at the pair's first record
    assert h.last_before("s1", "apple", T) == ABSTAIN  # before it
    assert h.last_before("nope", "apple", T + 10_000) == ABSTAIN
    assert h.last_before("s1", "pear", T + 10_000) == ABSTAIN


def test_between_and_count_half_open():
    h = build()
    assert [r["price"] for r in h.between("s1", "apple", T + 100, T + 300)] == [90, 95]
    assert h.count("s1", "apple", T + 100, T + 301) == 3
    assert (
        h.count("s1", "apple", T + 101, T + 200) == 0
    )  # inside the record: a real zero
    assert h.between("s1", "apple", T + 200, T + 200) == []
    assert h.count("nope", "apple", T, T + 1000) == ABSTAIN
    assert (
        h.between("s1", "apple", T, T + 100) == ABSTAIN
    )  # window ends at the record start
    assert (
        h.count("s2", "apple", T + 120, T + 150) == 0
    )  # after the record start, before s2's first trade
    with pytest.raises(ValueError):
        h.between("s1", "apple", None, T)
    with pytest.raises(ValueError):
        h.count("s1", "apple", T + 10, T)
    with pytest.raises(TypeError):
        h.count("s1", "apple", T)


def test_only_market_settles_and_duplicates_once():
    h = Prices()
    assert h.ingest(settle(1, T, 90)) is True
    assert h.ingest(settle(1, T, 90)) is False  # same event
    assert (
        h.ingest(dict(settle(9, T, 90, ledger=1), key="x")) is False
    )  # same market ledger #
    assert (
        h.ingest(settle(2, T + 1, 50, src="deal")) is False
    )  # our deal.json copy, not the market's settle
    assert h.ingest(settle(3, T + 1, 50, src="payments")) is False
    assert (
        h.ingest(
            {
                "seq": 4,
                "ts": T + 2,
                "type": "fail",
                "src": "market",
                "data": {"shop": "s1", "sku": "apple"},
            }
        )
        is False
    )
    assert h.ingest(settle(5, T + 3, None)) is False  # no price
    assert h.count("s1", "apple", T, T + 10) == 1


def test_out_of_order_category_and_round_trip():
    h = Prices(CAT)
    for e in reversed(
        [
            settle(1, T + 100, 90),
            settle(2, T + 200, 95),
            settle(3, T + 300, 80),
            settle(4, T + 150, 70, shop="s2"),
        ]
    ):
        h.ingest(e)
    assert h.to_json() == build().to_json()
    assert [r["price"] for r in h.between("s1", "apple", T, T + 1000)] == [90, 95, 80]
    assert h.between("s1", "apple", T, T + 1000)[0]["category"] == "fruit"
    d = json.loads(json.dumps(h.to_json()))
    h2 = Prices.from_json(d)
    assert h2.to_json() == h.to_json()
    assert h2.last_before("s1", "apple", T + 201) == 95
    assert h2.ingest(settle(2, T + 200, 95)) is False
    assert (
        h2.ingest(settle(5, T + 400, 85)) is True
        and h2.last_before("s1", "apple", T + 500) == 85
    )
