"""v2.9 (2026-11-08, furniture warranty): the first reply states the warranty hours the receipt would carry, so a
buyer reading the opening sees it before negotiating. 20:09 labpc: sofa and bed warranty_uplift 0.09 put a 6 h warranty
on every furniture unit, but the opening said nothing about it. The ask must stay the last number in the text."""

import copy
import re

from tests import test_v210_stars as T

S, P, R3 = T.S, T.P, T.R3
BASE = P.load(R3 / "policy.json")
SOFA = "가구/침구>가구>소파/의자/스툴"


def _unit(kind="goods", grade="A"):
    return {
        "unit": "u-1",
        "sku": "G8ec51935",
        "name": "sofa",
        "category": SOFA,
        "kind": kind,
        "grade": grade,
        "cost": 300_000,
        "list_price": 500_000,
        "supplied_at": 0,
        "expires_at": None,
    }


def _pol(uplift):
    pol = copy.deepcopy(BASE)
    pol.setdefault("categories", {}).setdefault(SOFA, {})["warranty_uplift"] = uplift
    return pol


def test_opening_states_the_receipt_warranty():
    pol, u = _pol(0.99), _unit()
    w = P.receipt_terms(u, pol, price=450_000).get("warranty_hours")
    assert w, "fixture should give a warranty"
    txt = S.opening_text(u, pol, 450_000)
    assert f", {w} h warranty: asking 450000." in txt


def test_opening_without_warranty_has_no_warranty_words():
    pol, u = _pol(0.0), _unit()
    assert not P.receipt_terms(u, pol, price=450_000).get("warranty_hours")
    assert "warranty" not in S.opening_text(u, pol, 450_000)


def test_ask_is_still_the_last_number():
    txt = S.opening_text(_unit(), _pol(0.99), 450_000)
    assert re.findall(r"\d+", txt)[-1] == "450000"
