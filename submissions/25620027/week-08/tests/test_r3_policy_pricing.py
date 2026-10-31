"""Policy versions stay bounded; prices derive from cost, grade, age and policy."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from r3.market_files import Unit
from r3.policy import Policy, apply_changes, load_history, load_policy, save_policy
from r3.pricing import claimed_grade, counter, decide, quote
from tests.r3_stock import KIMCHI, LAMP, unit

if TYPE_CHECKING:
    from pathlib import Path


def test_missing_policy_is_defaults_and_saving_versions_keeps_two_previous(
    tmp_path: Path,
) -> None:
    # Given: no policy file yet.
    path = tmp_path / "r3-policy.json"
    assert load_policy(path) == Policy()
    # When: the manager publishes three changes.
    v2 = save_policy(path, apply_changes(Policy(), {"seller.markup_a": 0.9}), "r1")
    v3 = save_policy(path, apply_changes(v2, {"buyer.first_offer_ratio": 0.5}), "r2")
    v4 = save_policy(path, apply_changes(v3, {"restock.units_per_sku": 2}), "r3")
    # Then: the versions count up and only two previous versions are kept.
    assert (v2.version, v3.version, v4.version) == (2, 3, 4)
    history = load_history(path)
    assert history.current.restock.units_per_sku == 2
    assert [p.version for p in history.previous] == [3, 2]
    assert load_policy(path).seller.markup_a == 0.9


def test_changes_outside_bounds_or_unknown_fields_are_rejected() -> None:
    # Given/When/Then: bounds come from the policy model, not the proposer.
    with pytest.raises(ValidationError):
        _ = apply_changes(Policy(), {"seller.markup_a": 1.5})
    with pytest.raises(ValidationError):
        _ = apply_changes(Policy(), {"restock.reserve_cash": -1})
    with pytest.raises(ValueError, match="Unknown policy field"):
        _ = apply_changes(Policy(), {"wallet.budget": 1})
    with pytest.raises(ValueError, match="Unknown policy field"):
        _ = apply_changes(Policy(), {"seller": 1})


def test_quote_prices_from_cost_and_grade_and_never_below_accept_line() -> None:
    now = time.time()
    policy = Policy().seller
    a = Unit.model_validate(unit(LAMP, 1, grade="A", now=now))
    c = Unit.model_validate(unit(LAMP, 2, grade="C", now=now))
    qa, qc = quote(a, now, None, policy), quote(c, now, None, policy)
    # Then: A asks 95% of list, C asks 70%; both accept at cost*(1+accept_margin).
    assert qa.ask == round(40000 * 0.95)
    assert qc.ask == round(40000 * 0.70)
    assert qa.accept_at == round(a.cost * 1.15)
    assert qc.accept_at == round(c.cost * 1.15)
    assert qa.ask >= qa.accept_at >= qa.floor
    # And: a counter moves toward the accept line and stops there.
    moved = counter(qa.ask, qa.accept_at, policy)
    assert qa.accept_at <= moved < qa.ask
    assert counter(qa.accept_at, qa.accept_at, policy) == qa.accept_at


def test_food_near_expiry_clears_below_cost_and_claims_lower_grade_near_boundary() -> (
    None
):
    now = time.time()
    policy = Policy().seller
    nearly_gone = Unit.model_validate(
        unit(KIMCHI, 1, grade="C", now=now, shelf_left_ratio=0.1)
    )
    cleared = quote(nearly_gone, now, 720, policy)
    assert cleared.clearance is True
    assert cleared.accept_at == cleared.floor < nearly_gone.cost
    assert cleared.grade_now == "C"
    # And: a food unit just above the A boundary is claimed as B.
    near_boundary = Unit.model_validate(
        unit(KIMCHI, 2, grade="A", now=now, shelf_left_ratio=0.805)
    )
    assert claimed_grade(near_boundary, now, 720, policy) == "B"
    safe = Unit.model_validate(
        unit(KIMCHI, 3, grade="A", now=now, shelf_left_ratio=0.9)
    )
    assert claimed_grade(safe, now, 720, policy) == "A"


def test_decide_accepts_counters_then_rejects_after_max_rounds() -> None:
    policy = Policy().seller
    assert decide(1200, 1500, 1100, 0, policy) == ("accept", 1200)
    action, new_ask = decide(900, 1500, 1100, 1, policy)
    assert action == "counter"
    assert 1100 <= new_ask < 1500
    assert decide(900, 1500, 1100, policy.max_rounds, policy) == ("reject", 0)
    assert decide(None, 1500, 1100, 0, policy)[0] == "counter"
