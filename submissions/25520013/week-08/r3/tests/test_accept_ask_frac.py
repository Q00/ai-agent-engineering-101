"""v1.21: buyer accept_ask_frac. Absent key = the v1.20 next_offer for every input (randomized grid, seed 20261104);
with a = 0.6 early asks above list x a are countered, the final round accepts up to max_pay, a counter never
exceeds the ask, and relax moves a toward the relaxed max_pay_frac. Pure: no network."""

import copy
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "hooks")):
    if p not in sys.path:
        sys.path.insert(0, p)
import policy as P  # noqa: E402

SEED = 20261104


def _ref_next_offer(cand, pol, order, rounds, last_ask, relax=0.0):
    """Copy of the v1.20 policy.next_offer body (before accept_ask_frac), the equivalence reference."""
    c = P.buyer_terms(order, pol, relax)
    if last_ask is not None and last_ask <= cand["max_pay"] and rounds > 0:
        return int(last_ask)
    o = int(cand["open"] + rounds * c["step_frac"] * cand["list_price"])
    return min(o, cand["max_pay"])


def _pol(a=None, max_rounds=None, kind="goods", cat=None):
    pol = copy.deepcopy(P.load())
    blk = pol["buyer"]["defaults"][kind]
    if max_rounds is not None:
        blk["max_rounds"] = max_rounds
    if a is not None:
        if cat is None:
            blk["accept_ask_frac"] = a
        else:
            pol["buyer"].setdefault("categories", {})[cat] = {"accept_ask_frac": a}
    return pol


def _cand(lp, pol, kind="goods", budget=None):
    c = P.buyer_terms({"kind": kind}, pol)
    mp = int(lp * c["max_pay_frac"])
    if budget:
        mp = min(mp, budget)
    return {"list_price": lp, "max_pay": mp, "open": max(1, int(lp * c["open_frac"]))}


def test_policy_ships_without_accept_ask_frac():
    pol = P.load()
    assert pol["bounds"]["accept_ask_frac"] == [0.3, 1.0]
    for blk in list(pol["buyer"]["defaults"].values()) + list(
        (pol["buyer"].get("categories") or {}).values()
    ):
        assert "accept_ask_frac" not in blk


def test_absent_key_matches_v120_on_random_grid():
    rng = random.Random(SEED)
    base = P.load()
    n = 0
    for _ in range(4000):
        kind = rng.choice(["food", "goods"])
        pol = _pol(max_rounds=rng.randint(1, 10), kind=kind)
        lp = rng.randint(1, 500_000)
        cap = int(lp * rng.uniform(0.3, 1.0))
        mp = max(1, min(cap, rng.choice([cap, rng.randint(1, 600_000)])))
        cand = {
            "list_price": lp,
            "max_pay": mp,
            "open": max(1, int(lp * rng.uniform(0.2, 0.9))),
        }
        order = {"kind": kind, "category": rng.choice([None, "x>y"])}
        relax = rng.choice([0.0, 0.5, 1.0])
        rounds = rng.randint(0, 8)
        ask = rng.choice(
            [
                None,
                mp,
                mp - 1,
                mp + 1,
                int(mp * rng.uniform(0.5, 1.5)),
                rng.randint(0, 2 * lp),
            ]
        )
        ref = _ref_next_offer(cand, pol, order, rounds, ask, relax)
        if rounds == 0 and ask is not None and ref > ask:  # v1.21: the opening offer is clamped to a lower first ask
            ref = int(ask)
        assert P.next_offer(cand, pol, order, rounds, ask, relax) == ref
        n += 1
    assert n == 4000 and base["buyer"]["defaults"]["goods"]["max_rounds"] == 5


def test_final_round_definition_matches_haggle_limit():
    assert P.HAGGLE_ROUNDS == 8
    src = (ROOT / "hooks" / "market_buyer.py").read_text()
    assert "rounds < P.HAGGLE_ROUNDS" in src and "rounds < 8" not in src


LP = 100_000


@pytest.mark.parametrize("kind", ["food", "goods"])
def test_a06_early_round_ask_at_070_is_countered(kind):
    pol = _pol(a=0.6, kind=kind)
    pol["buyer"]["defaults"][kind]["step_frac"] = 0.01  # counters stay under 70k up to the final round
    cand = _cand(LP, pol, kind)  # max_pay 85,000, open 55,000
    ask = int(0.7 * LP)
    for rounds in (1, 2, 3):  # max_rounds 5 -> final round 4
        o = P.next_offer(cand, pol, {"kind": kind}, rounds, ask)
        assert o < ask
        assert o == min(int(cand["open"] + rounds * 0.01 * LP), cand["max_pay"])
    assert P.next_offer(cand, pol, {"kind": kind}, 4, ask) == ask


def test_a06_final_round_accepts_up_to_max_pay():
    pol = _pol(a=0.6)
    cand = _cand(LP, pol)
    for ask in (int(0.7 * LP), cand["max_pay"]):
        assert P.next_offer(cand, pol, {"kind": "goods"}, 4, ask) == ask
    o = P.next_offer(cand, pol, {"kind": "goods"}, 4, cand["max_pay"] + 1)  # above the cap: counter
    assert o == min(int(cand["open"] + 4 * 0.07 * LP), cand["max_pay"])


def test_final_round_capped_by_haggle_rounds():
    pol = _pol(a=0.6, max_rounds=10)  # max_rounds above haggle's 8: final = round 7
    pol["buyer"]["defaults"]["goods"]["step_frac"] = 0.01
    cand = _cand(LP, pol)
    ask = int(0.8 * LP)
    assert P.next_offer(cand, pol, {"kind": "goods"}, 6, ask) < ask
    assert P.next_offer(cand, pol, {"kind": "goods"}, 7, ask) == ask


def test_a06_ask_at_055_is_accepted():
    pol = _pol(a=0.6)
    cand = _cand(LP, pol)
    assert P.next_offer(cand, pol, {"kind": "goods"}, 1, int(0.55 * LP)) == int(
        0.55 * LP
    )


def test_a06_category_override_through_resolve():
    pol = _pol(a=0.6, cat="침대")
    cand = _cand(LP, pol)
    ask = int(0.7 * LP)
    assert (
        P.next_offer(cand, pol, {"kind": "goods", "category": "가구>침대"}, 1, ask)
        < ask
    )
    assert (
        P.next_offer(cand, pol, {"kind": "goods", "category": "가구>소파"}, 1, ask)
        == ask
    )


def test_a06_counter_never_exceeds_ask_and_never_exceeds_cap():
    rng = random.Random(SEED)
    for _ in range(3000):
        pol = _pol(a=0.6, max_rounds=rng.randint(1, 10))
        lp = rng.randint(10, 500_000)
        budget = rng.choice([None, rng.randint(1, lp)])
        cand = _cand(lp, pol, budget=budget)
        rounds = rng.randint(0, 8)
        ask = rng.choice([None, rng.randint(0, 2 * lp)])
        relax = rng.choice([0.0, 0.5, 1.0])
        o = P.next_offer(cand, pol, {"kind": "goods"}, rounds, ask, relax)
        assert o <= cand["max_pay"]
        if budget:
            assert o <= budget
        if ask is not None:
            assert o <= ask


def test_counter_reaches_the_ask_at_the_cap():
    pol = _pol(a=0.6)
    cand = _cand(LP, pol)
    pol["buyer"]["defaults"]["goods"]["step_frac"] = 0.3  # o hits max_pay at round 1
    ask = int(0.8 * LP)
    assert P.next_offer(cand, pol, {"kind": "goods"}, 1, ask) == ask


def test_relax_moves_a_toward_relaxed_max_pay():
    pol = _pol(a=0.6)
    pol["volume"]["relax_max_pay_frac"] = 0.95
    for r in (0.0, 0.5, 1.0):
        c = P.buyer_terms({"kind": "goods"}, pol, r)
        assert c["accept_ask_frac"] == pytest.approx(
            0.6 + r * (c["max_pay_frac"] - 0.6)
        )
    assert P.buyer_terms({"kind": "goods"}, pol, 1.0)[
        "accept_ask_frac"
    ] == pytest.approx(0.95)
    assert "accept_ask_frac" not in P.buyer_terms({"kind": "goods"}, _pol(), 1.0)
