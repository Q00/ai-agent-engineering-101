"""v2.1a (2026-11-05, the verifier's edge cases of v2.1): product names and their own hour tokens leave the request
case-insensitively; '1,000 hours' reads 1000 h; the food pick runs only when every unit that fits is food with an
expiry, so a mixed fit keeps the v1.20 pick."""

import copy
import random

import pytest

from tests import test_v210_stars as T

S, REF, NOW, EGG = T.S, T.REF, T.NOW, T.EGG
food, goods = T.food, T.goods


@pytest.fixture(autouse=True)
def _buyer_log_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "R3_BUYER_LOG", str(tmp_path / "buyer_attempts.jsonl")
    )  # never the repo's logs/


def test_a_name_in_another_case_is_not_a_need():
    kimchi = "24H 숙성 김치"
    units = [
        food("short", 30, sku="Gk0001", name=kimchi),
        food("long", 600, sku="Gk0001", name=kimchi),
    ]
    assert S.stated_need_hours("i want 24h 숙성 김치", [kimchi]) is None
    got = S.find_unit(
        "i want 24h 숙성 김치", {"units": units}, now=NOW, shelf=lambda s: None
    )
    assert (
        got["unit"] == "long"
    )  # unknown need, no catalog shelf: the most hours left (v2.1 read 24 h, shipped 30 h)


def test_a_shortened_name_keeps_its_hour_token_out():
    assert (
        S.stated_need_hours("I want to buy 48시간 구운계란 (G56d000c7)", [EGG]) is None
    )
    assert (
        S.stated_need_hours(
            "I want to buy 48시간 구운계란 (G56d000c7). I need it to last 72 hours",
            [EGG],
        )
        == 72.0
    )
    assert (
        S.stated_need_hours("148시간 구운계란, at least 148시간", [EGG]) == 148.0
    )  # only the name's own token goes


def test_thousands_separators_read_as_one_number():
    assert S.stated_need_hours("1,000 hours please") == 1000.0
    assert S.stated_need_hours("need: shelf life 1,234.5 h or more") == 1234.5
    assert S.stated_need_hours("3, 500 hours") == 500.0
    assert (
        T.pick("I want Gfood0001 1,000 hours", [food("u1", 1), food("u1200", 1200)])[
            "unit"
        ]
        == "u1200"
    )


def test_an_hour_number_right_after_a_period_or_comma_still_reads():
    assert S.stated_need_hours("need: shelf life 48h,72h or more") == 72.0
    assert S.stated_need_hours("I need 48 hours.72 hours is better") == 72.0
    assert S.stated_need_hours("kimchi.48 hours") == 48.0
    assert S.stated_need_hours("구운계란.48시간") == 48.0
    assert S.stated_need_hours("구운계란.48시간", [EGG]) is None  # the name's own token
    for torn in ("12,34 hours", "1234,5 h", "1.2.3 hours", "1,000150시간"):
        assert S.stated_need_hours(torn) is None  # digits around a separator stay one number


def test_a_mixed_fit_keeps_the_v120_pick():
    units = [dict(food("fC", 300), grade="C"), goods("gA", "A", 0, sku="G0000a1")]
    for text in (
        "I want Gfood0001 or G0000a1",
        "I want Gfood0001 or G0000a1, need 500 hours",
    ):
        why = {}
        got = S.find_unit(text, {"units": copy.deepcopy(units)}, why=why)
        assert (
            got == REF.find_unit(text, {"units": copy.deepcopy(units)})
            and got["unit"] == "gA"
            and why == {}
        )


def test_food_without_an_expiry_keeps_the_v120_pick():
    units = [
        dict(food("noexp", 0), expires_at=None),
        dict(food("c200", 200), grade="C"),
    ]
    t = "I want to buy 곤약쫀드기 (Gfood0001)."
    got = S.find_unit(t, {"units": copy.deepcopy(units)}, now=NOW, shelf=T.SHELF.get)
    assert (
        got["unit"]
        == REF.find_unit(t, {"units": copy.deepcopy(units)})["unit"]
        == "noexp"
    )


def test_any_fit_that_is_not_all_dated_food_is_the_v120_pick():
    rnd, n = random.Random(2101), 0
    for _ in range(3000):
        units = []
        for i in range(rnd.randint(1, 6)):
            sku = rnd.choice(["Gfood0001", "G0000a1"])
            if rnd.random() < 0.5:
                u = food(
                    f"f{i}", rnd.uniform(1, 700), sku=sku, supplied_at=rnd.randint(0, 9)
                )
                if rnd.random() < 0.2:
                    u["expires_at"] = None
            else:
                u = goods(f"g{i}", rnd.choice("ABC"), rnd.randint(0, 9), sku=sku)
            if rnd.random() < 0.3:
                u["grade"] = rnd.choice(["A", "B", "C", "expired"])
            units.append(u)
        text = rnd.choice(
            [
                "I want Gfood0001",
                "I want G0000a1",
                "I want Gfood0001 or G0000a1",
                "I want Gfood0001, need 300 hours",
            ]
        )
        taken = {u["unit"] for u in units if rnd.random() < 0.2}
        skus = {s.lower() for s in S.SKU_RE.findall(text)}
        fit = [
            u
            for u in units
            if u["grade"] != "expired"
            and u["unit"] not in taken
            and u["sku"].lower() in skus
        ]
        if fit and all(u.get("kind") == "food" and u.get("expires_at") for u in fit):
            continue  # all dated food: the v2.1 food pick (test_v210_stars)
        n += 1
        why = {}
        a = REF.find_unit(text, {"units": copy.deepcopy(units)}, taken)
        b = S.find_unit(
            text,
            {"units": copy.deepcopy(units)},
            taken,
            now=NOW,
            shelf=T.SHELF.get,
            why=why,
        )
        assert a == b and why == {}
    assert n > 1000
