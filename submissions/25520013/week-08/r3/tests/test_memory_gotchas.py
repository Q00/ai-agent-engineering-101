"""Layer 2' failure-reason index: window required, [t0, t1) boundaries, duplicates, abstain, hour key, catalog
category, every failure event shape the ledger and hooks write, to_json/from_json round trip."""

import json

import pytest
from memory import ABSTAIN
from memory.gotchas import Gotchas

H = 3600
T0 = 1_700_000_000 - 1_700_000_000 % 86400  # 00:00 UTC = 09:00 KST
CAT = {"apple": {"category": "fruit", "name": "Apple", "list_price": 100}}


def ev(seq, ts, type_, src, data, key=None):
    return {
        "seq": seq,
        "ts": ts,
        "type": type_,
        "src": src,
        "key": key or f"k{seq}",
        "role": "buyer",
        "mine": True,
        "data": data,
    }


def events():
    return [
        ev(
            1,
            T0 + 1 * H,
            "reject",
            "payments",
            {
                "order": "o1",
                "shop": "s1",
                "item": "apple",
                "price": 90,
                "reason": "the receipt does not match",
                "reason_code": "mismatch",
            },
        ),
        ev(
            2,
            T0 + 2 * H,
            "fail",
            "market",
            {
                "stage": "result",
                "order": "o2",
                "detail": ["grade", "price"],
                "ledger": 50,
            },
            key="market:50",
        ),
        ev(
            3,
            T0 + 3 * H,
            "broken",
            "market",
            {
                "name": "Mug",
                "sku": "mug",
                "buyer": "b",
                "shop": "s2",
                "broke_at": T0 + 3 * H,
                "ledger": 51,
            },
            key="market:51",
        ),
        ev(
            4,
            T0 + 4 * H,
            "fail",
            "hook",
            {"order": "o3", "stage": "a2a", "detail": "ConnectError"},
            key="a2a:o3:http://s3.example",
        ),
        ev(
            5,
            T0 + 5 * H,
            "decision",
            "hook",
            {
                "hook": "market_buyer",
                "what": "offer_selection",
                "candidates": 3,
                "tried": [
                    {
                        "shop": "s1",
                        "sku": "apple",
                        "rounds": 2,
                        "state": "receipt_refused",
                        "why": "price 120 above cap 100",
                    },
                    {
                        "shop": "s4",
                        "sku": "apple",
                        "rounds": 0,
                        "state": "walked_away",
                        "why": "stated grade C",
                    },
                    {
                        "shop": "s5",
                        "sku": "apple",
                        "rounds": 3,
                        "state": "TASK_STATE_COMPLETED",
                    },
                ],
            },
            key="buy:o4",
        ),
        ev(
            6,
            T0 + 6 * H,
            "reject",
            "payments",
            {
                "order": "o5",
                "shop": "s1",
                "item": "apple",
                "price": 95,
                "reason": "price above the mandate",
                "reason_code": "over_cap",
            },
        ),
        ev(
            7,
            T0 + 7 * H,
            "settle",
            "market",
            {"shop": "s1", "sku": "apple", "amount": 90, "ledger": 52},
        ),
        ev(
            8,
            T0 + 8 * H,
            "fail",
            "market",
            {"stage": "refund", "detail": {"shop": "s2", "order": "o6"}, "ledger": 53},
        ),
        ev(
            9,
            T0 + 9 * H,
            "decision",
            "hook",
            {
                "hook": "market_buyer",
                "what": "offer_selection",
                "candidates": 0,
                "tried": [],
            },
            key="buy:o7",
        ),
    ]


def build():
    g = Gotchas(CAT)
    for e in events():
        g.ingest(e)
    return g


def test_window_required():
    g = build()
    with pytest.raises(TypeError):
        g.query()
    with pytest.raises(ValueError):
        g.query(None, T0)
    with pytest.raises(ValueError):
        g.memos(T0, None)
    with pytest.raises(ValueError):
        g.query(T0 + H, T0)
    with pytest.raises(ValueError):
        g.first_of(["mismatch"], None, T0)


def test_failure_types_and_shapes():
    g = build()
    rows = g.query(T0, T0 + 24 * H)
    assert [(r["type"], r["stage"], r["reason"], r["shop"]) for r in rows] == [
        ("reject", "payment", "mismatch", "s1"),
        ("fail", "result", "grade+price", None),
        ("broken", "broken", "broken", "s2"),
        ("fail", "a2a", "a2a", "http://s3.example"),
        ("decision", "receipt_refused", "price N above cap N", "s1"),
        ("decision", "walked_away", "stated grade C", "s4"),
        ("reject", "payment", "over_cap", "s1"),
        ("fail", "refund", "refund", "s2"),
        ("decision", "no_candidates", "no_candidates", None),
    ]
    assert all(r["seq"] != 7 for r in rows)  # a settle is not a failure
    assert rows[4]["order"] == "o4" and rows[7]["order"] == "o6"


def test_boundaries_half_open():
    g = build()
    assert [r["seq"] for r in g.query(T0 + 1 * H, T0 + 3 * H)] == [
        1,
        2,
    ]  # t1 excluded, t0 included
    assert g.query(T0 + 1 * H, T0 + 1 * H) == []
    assert g.last_failure_before(T0 + 6 * H)["seq"] == 5  # t itself excluded
    assert g.last_failure_before(T0 + 6 * H + 1)["seq"] == 6
    assert g.last_failure_before(T0 + 1 * H) == ABSTAIN  # before the first failure
    assert (
        g.last_failure_before(T0 + 24 * H, shop="s1", type="reject")["reason"]
        == "over_cap"
    )
    assert g.last_failure_before(T0 + 24 * H, shop="nope") == ABSTAIN


def test_first_of_and_filters():
    g = build()
    assert g.first_of(["over_cap", "mismatch"], T0, T0 + 24 * H) == "mismatch"
    assert (
        g.first_of(["over_cap", "mismatch"], T0 + 1 * H + 1, T0 + 24 * H) == "over_cap"
    )
    assert g.first_of(["over_cap", "mismatch"], T0 + 7 * H, T0 + 24 * H) == ABSTAIN
    assert g.first_of(["broken", "reject"], T0, T0 + 24 * H, key="type") == "reject"
    with pytest.raises(TypeError):
        g.query(T0, T0 + H, colour="red")


def test_hour_key_and_category():
    g = build()
    rows = g.query(T0, T0 + 24 * H)
    assert rows[0]["hour"] == 10  # 01:00 UTC -> 10 KST
    assert [r["seq"] for r in g.query(T0, T0 + 24 * H, hour=11)] == [2]
    g0 = Gotchas(tz_hours=0)
    g0.ingest(events()[0])
    assert g0.query(T0, T0 + 24 * H)[0]["hour"] == 1
    assert [r["seq"] for r in g.query(T0, T0 + 24 * H, category="fruit")] == [
        1,
        5,
        5,
        6,
    ]
    g2 = Gotchas()
    g2.ingest(events()[0])
    assert g2.query(T0, T0 + 24 * H)[0]["category"] is None  # no catalog, no category


def test_duplicates_counted_once():
    g = Gotchas()
    e = events()[1]
    assert g.ingest(e) is True
    assert g.ingest(dict(e)) is False  # same seq
    assert g.ingest(dict(e, seq=99)) is False  # same key
    assert g.ingest(dict(e, seq=100, key="other")) is False  # same market ledger #
    assert g.ingest(events()[6]) is False  # settle: not a failure
    assert len(g.query(T0, T0 + 24 * H)) == 1


def test_memos():
    g = build()
    m = g.memos(T0, T0 + 24 * H)
    s1 = [x for x in m if x["shop"] == "s1" and x["stage"] == "payment"]
    assert len(s1) == 2 and all(x["count"] == 1 for x in s1)
    g.ingest(
        ev(
            10,
            T0 + 10 * H,
            "reject",
            "payments",
            {"shop": "s1", "item": "apple", "reason_code": "mismatch"},
        )
    )
    top = g.memos(T0, T0 + 24 * H)[0]
    assert top == {
        "reason": "mismatch",
        "shop": "s1",
        "stage": "payment",
        "count": 2,
        "first_ts": T0 + 1 * H,
        "last_ts": T0 + 10 * H,
        "example_seq": 1,
    }
    late = [x for x in g.memos(T0 + 2 * H, T0 + 24 * H) if x["reason"] == "mismatch"]
    assert late == [
        dict(top, count=1, first_ts=T0 + 10 * H, example_seq=10)
    ]  # memos follow the window


def test_out_of_order_ingest_and_round_trip():
    g = Gotchas(CAT)
    for e in reversed(events()):
        g.ingest(e)
    assert g.to_json() == build().to_json()
    d = json.loads(json.dumps(g.to_json()))
    g2 = Gotchas.from_json(d)
    assert g2.to_json() == g.to_json()
    assert g2.ingest(events()[0]) is False  # seen set survives the round trip
    assert g2.last_failure_before(T0 + 6 * H)["seq"] == 5
