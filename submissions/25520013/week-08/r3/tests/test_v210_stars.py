"""v2.1 (2026-11-05, stars): food units go by the buyer's shelf-life need (stated in the request, else 0.75 of the
catalog shelf); a stated need no unit lasts is a no-stock reject that names the need; goods selection is unchanged.
shop.star_price_cap caps a goods ask by grade as a share of list, never below the floor."""

import asyncio
import copy
import json
import random
import types

import pytest

from tests import test_v120_buyer_log as TB

R3 = TB.R3
S = TB.load_seller(R3 / "hooks" / "seller.py", "seller_v210_under_test")
REF = TB.load_seller(TB.OLD, "seller_v210_ref")
import policy as P  # noqa: E402  (TB put R3 and R3/hooks on sys.path)

NOW = 1_800_000_000.0
SHELF = {"Gfood0001": 720.0, "Gfood0002": 48.0}
EGG = "[전라도청년] 파손보상 48시간 구운계란 대란 2판 60구"
STATED = (
    "I want to buy 곤약쫀드기 (Gfood0001). I need it to last 180.0 hours: please ship a unit with at least "
    "198.0 hours of shelf life left, with the receipt"
)


def food(uid, hours_left, sku="Gfood0001", supplied_at=0, name="곤약쫀드기"):
    return {
        "unit": uid,
        "sku": sku,
        "name": name,
        "category": "식품>과자",
        "kind": "food",
        "grade": "A",
        "cost": 1000,
        "list_price": 2000,
        "expires_at": NOW + hours_left * 3600,
        "supplied_at": supplied_at,
    }


def goods(uid, grade, supplied_at, sku="G0000a1", list_price=1_000_000, cost=550_000):
    return {
        "unit": uid,
        "sku": sku,
        "name": "매트리스",
        "category": "가구>침대/매트리스",
        "kind": "goods",
        "grade": grade,
        "cost": cost,
        "list_price": list_price,
        "supplied_at": supplied_at,
    }


def pick(text, units, **kw):
    return S.find_unit(text, {"units": units}, now=NOW, shelf=SHELF.get, **kw)


@pytest.fixture(autouse=True)
def _buyer_log_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("R3_BUYER_LOG", str(tmp_path / "buyer_attempts.jsonl"))  # never the repo's logs/


# --- the need in the request


def test_stated_need_reads_the_live_request_formats():
    assert S.stated_need_hours(STATED) == 198.0
    assert (
        S.stated_need_hours(
            "I want to buy X (Gfood0001) | need: shelf life 541.0 h or more, market certificate"
        )
        == 541.0
    )
    assert S.stated_need_hours("I want to buy 13 hours / 12.0 h / 13 h") == 13.0
    assert S.stated_need_hours("48시간 안에 먹을 김치") == 48.0
    assert S.stated_need_hours("I want to buy X (Gfood0001).") is None
    assert (
        S.stated_need_hours("I want G00012h3 please") is None
    )  # SKU tokens are not hours
    assert S.stated_need_hours("HACCP 5kg 500ml") is None


def test_product_names_are_not_needs():
    t = f"I want to buy {EGG} (G56d000c7)"
    assert S.stated_need_hours(t) == 48.0
    assert S.stated_need_hours(t, [EGG]) is None
    assert (
        S.stated_need_hours(t + ". I need it to last 72 hours", [EGG, None, ""]) == 72.0
    )


# --- food pick


def test_a_stated_need_ships_the_oldest_unit_that_lasts():
    units = [food("u100", 100), food("u200", 200), food("u300", 300)]
    assert pick(STATED, units)["unit"] == "u200"
    assert (
        REF.find_unit(STATED, {"units": units})["unit"] == "u100"
    )  # v2.0: the first to expire, 2-star review


def test_a_stated_need_no_unit_lasts_is_none_and_says_why():
    units = [food("u355", 355), food("u400", 400)]
    why = {}
    assert (
        pick(
            "I want to buy X (Gfood0001) | need: shelf life 541.0 h or more",
            units,
            why=why,
        )
        is None
    )
    assert why == {"need_h": 541.0}
    edge = [food("short", 198.0 + 0.2), food("ok", 198.0 + 0.3)]  # NEED_MARGIN_H 0.25
    assert pick(STATED, edge)["unit"] == "ok"
    why = {}
    assert pick(STATED, [food("short", 198.2)], why=why) is None and why == {
        "need_h": 198.0
    }


def test_an_unknown_need_takes_three_quarters_of_the_shelf():
    t = "I want to buy 곤약쫀드기 (Gfood0001)."
    assert (
        pick(
            t,
            [
                food("u100", 100),
                food("u560", 560),
                food("u600", 600),
                food("u700", 700),
            ],
        )["unit"]
        == "u560"
    )
    assert (
        pick(
            t,
            [
                food("u100", 100),
                food("u300", 300, supplied_at=5),
                food("u300b", 300, supplied_at=9),
            ],
        )["unit"]
        == "u300"
    )
    assert (
        pick(t, [food("u100", 100), food("u300", 300)], why={}) is not None
    )  # never a reject without a stated need
    # no catalog shelf: the unit with the most hours left
    assert (
        S.find_unit(
            t, {"units": [food("a", 10), food("b", 20)]}, now=NOW, shelf=lambda s: None
        )["unit"]
        == "b"
    )


def test_the_need_ignores_a_product_name_with_hours():
    egg = [
        food("e10", 10, sku="Gegg0001", name=EGG),
        food("e600", 600, sku="Gegg0001", name=EGG),
    ]
    shelf = {"Gegg0001": 720.0}.get
    got = S.find_unit(
        f"I want to buy {EGG} (Gegg0001)", {"units": egg}, now=NOW, shelf=shelf
    )
    assert got["unit"] == "e600"  # unknown need: 0.75 x 720 h, not 48 h


# --- goods unchanged


def test_goods_pick_is_the_v120_pick():
    rnd = random.Random(210)
    for _ in range(300):
        units = [
            goods(
                f"g{i}",
                rnd.choice("ABC"),
                rnd.randint(0, 9),
                sku=rnd.choice(["G0000a1", "G0000b2"]),
            )
            for i in range(rnd.randint(0, 6))
        ]
        text = rnd.choice(
            [
                "I want G0000a1",
                "I want G0000b2",
                "I want a 침대/매트리스",
                "I want 매트리스",
                "nothing",
            ]
        )
        taken = {u["unit"] for u in units if rnd.random() < 0.2}
        why = {}
        a = REF.find_unit(text, {"units": copy.deepcopy(units)}, taken)
        b = S.find_unit(text, {"units": copy.deepcopy(units)}, taken, why=why)
        assert a == b and why == {}


# --- seller wiring (no network: the A2A updater is a recorder, as in test_v120_buyer_log)


def _drive(texts, units):
    trace, clock = [], TB.Clock()
    m = TB.load_seller(
        R3 / "hooks" / "seller.py", f"seller_v210_drive_{len(texts)}_{len(units)}"
    )
    TB.patch(m, trace, clock)
    cat = {"Gfood0001": {"sku": "Gfood0001", "shelf_hours": 720.0, "kind": "food"}}
    m.read_json = lambda p, d=None: (
        json.loads(json.dumps({"units": units}))
        if str(p).endswith("inventory.json")
        else cat
        if str(p).endswith("catalog.json")
        else d
    )
    s = m.Seller(pol=P.load(R3 / "policy.json"))

    class Q:
        async def enqueue_event(self, e):
            trace.append(("task", e.id))

    async def go():
        for i, t in enumerate(texts):
            msg = types.SimpleNamespace(
                text=t, tid=f"t{i}", cid=f"c{i}", metadata={"buyer": "b"}
            )
            ctx = types.SimpleNamespace(
                current_task=None, message=msg, context_id=f"c{i}", call_context=None
            )
            await s._execute(ctx, Q())

    asyncio.run(go())
    return trace


def test_seller_rejects_a_need_no_unit_lasts_and_names_it():
    tr = _drive(
        ["I want to buy X (Gfood0001) | need: shelf life 541.0 h or more"],
        [food("u355", 355)],
    )
    rej = [t for t in tr if t[0] == "reject"]
    assert rej and "541 h of shelf life" in str(rej[0][2])
    met = [json.loads(t[2]) for t in tr if t[0] == "metric" and t[1] == "no_stock"]
    assert met and met[0]["need_h"] == 541.0


def test_seller_quotes_the_unit_that_lasts():
    tr = _drive(
        [STATED, "I want to buy 곤약쫀드기 (Gfood0001)."],
        [food("u100", 100), food("u200", 200), food("u600", 600)],
    )
    opened = [
        json.loads(t[2])["unit"] for t in tr if t[0] == "metric" and t[1] == "open"
    ]
    assert opened == ["u200", "u600"]  # unknown need: 0.75 x 720 h, only u600 lasts
    assert [t[0] for t in tr if t[0] in ("requires_input", "reject")] == [
        "requires_input",
        "requires_input",
    ]


def test_seller_no_stock_reply_is_unchanged_without_a_need():
    tr = _drive(["I want G0000zz"], [food("u100", 100)])
    rej = [t for t in tr if t[0] == "reject"]
    assert rej and "none of that in stock" in str(rej[0][2])
    met = [json.loads(t[2]) for t in tr if t[0] == "metric" and t[1] == "no_stock"]
    assert met and "need_h" not in met[0]


# --- star_price_cap


def _pol(cap):
    pol = P.load(R3 / "policy.json")
    pol["shop"]["star_price_cap"] = cap
    return pol


def test_star_price_cap_caps_a_goods_ask_by_grade():
    capped, free = _pol({"A": 0.775}), _pol(None)
    a = goods("a", "A", 0)
    assert P.ask_price(a, free) > 775_000
    assert P.ask_price(a, capped) == max(P.floor_price(a, capped), 775_000)
    b = goods("b", "B", 0, cost=400_000)
    assert P.ask_price(b, capped) == P.ask_price(b, free)  # no B cap
    f = dict(food("f", 700), list_price=1_000_000, cost=550_000)
    assert P.ask_price(f, capped, shelf_hours=720.0) == P.ask_price(
        f, free, shelf_hours=720.0
    )  # goods only
    dear = goods("d", "A", 0, cost=900_000)  # the floor wins over the cap
    assert P.ask_price(dear, capped) == P.floor_price(dear, capped) > 775_000


def test_repo_policy_has_the_cap_and_validate_checks_it():
    pol = P.load(R3 / "policy.json")
    assert pol["shop"]["star_price_cap"] == {"A": 0.775}
    for bad in ({"A": 1.5}, {"A": 0.4}, {"D": 0.8}, [0.8], {"A": True}, 0.775):
        with pytest.raises(P.PolicyError):
            P.validate(_pol(bad))
    for good in ({"A": 0.775}, {"A": 0.775, "B": 0.7, "C": 0.6}, {}, None):
        assert P.validate(_pol(good))
