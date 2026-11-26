"""The owner stopped serving one buyer, recognised only by its opening wording."""

from __future__ import annotations

import pytest

from r3 import seller
from r3.market_files import CatalogItem
from r3.seller import declined_buyer

DECLINED = (
    "I want to buy 파로마 렌지대 (G7b5d49aa). What is your price?",
    (
        "I want to buy 동치미 5kg (Ga6acb15a). I need it to last 126 hours: please "
        "offer only a unit with at least 126.5 hours of shelf life left, with the "
        "market's certificate. What is your price?"
    ),
    (
        "I want to buy 수건 (G292fc972). I buy grade A. I will not buy grade B or C: "
        "please quote a grade I buy. What is your price?"
    ),
    # dzzdd010 (owner 11/14 15:5x; 30-minute trial 17:38-18:09, blocked again)
    "I want to buy x (G8aa8f196). It must last 49 hours (need 49 h of shelf life).",
    # dzzdd010 goods form (11/16 21:1x)
    (
        "I want to buy 소파 (G33cc4ecf). I need a warranty of 6 hours or more "
        "(warranty 6 h), grade A or B preferred."
    ),
    # yms979 (owner 11/17 05:4x)
    (
        "I want to buy 두부과자 (G1234abcd). It must last 180 hours. Any grade is fine "
        "as long as it lasts that long: please quote your cheapest unit that does, "
        "and say how many hours it has left."
    ),
)

SERVED = (
    "I want to buy Gf0e58a76. Please quote a price in credits.",
    "I want to buy 사세 직화K버팔로윙 1kg (G2560a8c9)",
    "I want to buy 아이시스 2L 6개 (G7fa48eaa). It must last at least 74 hours.",
    "What is your price?",
    "I want to buy X (G1234abcd). What is your price? I need grade A.",
    "I want to buy X (G1234abcd), it must last at least 120 hours",
    "I want to buy X (G1234abcd). Must last at least 540.0 hours.",
)


@pytest.mark.parametrize("text", DECLINED)
def test_declined_buyer_openings_are_recognised(text: str) -> None:
    assert declined_buyer(text)


@pytest.mark.parametrize("text", SERVED)
def test_other_buyers_are_still_served(text: str) -> None:
    assert not declined_buyer(text)


def test_shelf_form_food_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    # 11/16 18:59-20:59 food asks in the shelf form were served; blocked again since.
    catalog = {
        "G66ded25b": CatalogItem(
            sku="G66ded25b", category="김치", kind="food", list_price=17_900
        ),
        "G8aa8f196": CatalogItem(
            sku="G8aa8f196", category="가구", kind="goods", list_price=90_000
        ),
    }
    food = (
        "I want to buy 총각김치 (G66ded25b). "
        "It must last 85 hours (need 85 h of shelf life)."
    )
    goods = (
        "I want to buy x (G8aa8f196). It must last 49 hours (need 49 h of shelf life)."
    )
    assert declined_buyer(food, catalog)  # switch off
    monkeypatch.setattr(seller, "SHELF_FORM_FOOD_OPEN", True)
    assert not declined_buyer(food, catalog)
    assert declined_buyer(goods, catalog)
