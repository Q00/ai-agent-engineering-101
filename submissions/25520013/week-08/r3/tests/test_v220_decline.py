"""v2.2 (2026-11-05, stars): a new request that carries a phrase on shop.decline_texts (case and runs of whitespace
ignored, entries of 12 characters or more only) is turned down before any stock is read, and logged as metric and
outcome `declined`, so the no_stock demand signals (restock own_ask, analysis/shop_goods) never count it. Requests
without a listed phrase keep the v2.1 trace."""

import asyncio
import copy
import json
import types

import pytest

from tests import test_v120_buyer_log as TB
from tests import test_v210_stars as T

S, P, R3 = T.S, T.P, T.R3
PHRASE = "Quote the total price. Require a market certificate."
TMPL = "I want to buy 곤약쫀드기 (Gfood0001). " + PHRASE
NORM = " ".join(PHRASE.split()).casefold()


@pytest.fixture(autouse=True)
def _buyer_log_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "R3_BUYER_LOG", str(tmp_path / "buyer_attempts.jsonl")
    )  # never the repo's logs/


def _pol(lst=None, drop=False):
    pol = P.load(R3 / "policy.json")
    if drop:
        pol["shop"].pop("decline_texts", None)
    else:
        pol["shop"]["decline_texts"] = lst
    return pol


# --- the match


def test_the_phrase_matches_in_any_case_and_spacing():
    pol = _pol([PHRASE])
    for t in (
        TMPL,
        TMPL.upper(),
        TMPL.replace(" ", "  "),
        TMPL.replace(". Require", ".\nRequire"),
        "quote the total price.   require a MARKET certificate. thanks",
    ):
        assert S.declined_phrase(t, pol) == NORM
    for t in (
        "I want to buy 곤약쫀드기 (Gfood0001). Quote the total price.",
        "Require a market certificate.",
        "quote the total price, require a market certificate",
        "",
        None,
    ):
        assert S.declined_phrase(t, pol) is None


def test_short_or_malformed_lists_match_nothing():
    for lst in (
        None,
        [],
        ["the"],
        ["quote the t"],
        PHRASE,
        {PHRASE: 1},
        [None, 5, ["x" * 20]],
        [" " * 30],
    ):
        assert S.declined_phrase(TMPL, _pol(lst)) is None
    assert S.declined_phrase(TMPL, _pol(drop=True)) is None
    assert S.declined_phrase(TMPL, _pol(["x" * 11, PHRASE])) == NORM
    assert (
        S.declined_phrase("a" * 12, _pol(["A" * 12])) == "a" * 12
    )  # 12 characters is enough


def test_the_repo_policy_declines_no_one():
    pol = P.load(R3 / "policy.json")
    assert not pol["shop"].get(
        "decline_texts"
    )  # the live list is a targeted edit on labpc
    assert S.declined_phrase(TMPL, pol) is None


# --- seller wiring (no network: the A2A updater is a recorder, as in test_v120_buyer_log)


def _drive(texts, units, pol, swap=None):
    """swap: {i: policy} puts that policy on the seller before request i (what a policy.json reload does)."""
    trace, clock, reads = [], TB.Clock(), []
    m = TB.load_seller(
        R3 / "hooks" / "seller.py", f"seller_v220_drive_{len(texts)}_{len(units)}"
    )
    TB.patch(m, trace, clock)
    cat = {"Gfood0001": {"sku": "Gfood0001", "shelf_hours": 720.0, "kind": "food"}}

    def rj(p, d=None):
        reads.append(str(p))
        if str(p).endswith("inventory.json"):
            return json.loads(json.dumps({"units": units}))
        return cat if str(p).endswith("catalog.json") else d

    m.read_json = rj
    s = m.Seller(pol=pol)

    class Q:
        async def enqueue_event(self, e):
            trace.append(("task", e.id))

    async def go():
        for i, t in enumerate(texts):
            if swap and i in swap:
                s.pol = swap[i]
            reads.append(("request", i))
            msg = types.SimpleNamespace(
                text=t, tid=f"t{i}", cid=f"c{i}", metadata={"buyer": "b"}
            )
            ctx = types.SimpleNamespace(
                current_task=None, message=msg, context_id=f"c{i}", call_context=None
            )
            await s._execute(ctx, Q())

    asyncio.run(go())
    return trace, reads


def test_the_seller_declines_before_reading_stock(tmp_path):
    tr, reads = _drive([TMPL], [T.food("u600", 600)], _pol([PHRASE]))
    rej = [t for t in tr if t[0] == "reject"]
    assert len(rej) == 1 and rej[0][2] == ["Sorry, we can't take this order."]
    assert [(t[1], json.loads(t[2])) for t in tr if t[0] == "metric"] == [
        ("declined", {"request": TB.BL.clean(TMPL, 80)})
    ]
    after = reads[reads.index(("request", 0)) + 1 :]
    assert not any(
        str(p).endswith("inventory.json") for p in after
    )  # turned down before any stock is read
    rows = TB.rows(tmp_path / "buyer_attempts.jsonl")
    assert [r["event"] for r in rows] == ["request", "outcome"]
    assert rows[1]["outcome"] == "declined" and rows[1]["task"] == "t0"


def test_requests_without_the_phrase_keep_the_v21_trace():
    texts = [
        T.STATED,
        "I want to buy 곤약쫀드기 (Gfood0001).",
        "I want G0000zz",
        "I want to buy X (Gfood0001) | need: shelf life 541.0 h or more",
        "I want Gfood0001. Quote the total price.",
    ]
    units = [T.food("u100", 100), T.food("u200", 200), T.food("u600", 600)]
    on, _ = _drive(texts, copy.deepcopy(units), _pol([PHRASE]))
    off, _ = _drive(texts, copy.deepcopy(units), _pol(drop=True))
    assert on == off
    assert [t[0] for t in on if t[0] in ("requires_input", "reject")] == [
        "requires_input",
        "requires_input",
        "reject",
        "reject",
        "requires_input",
    ]


def test_the_list_is_read_per_request():
    units = [T.food("u600", 600), T.food("u700", 700)]
    tr, _ = _drive([TMPL, TMPL], units, _pol([PHRASE]), swap={1: _pol([])})
    assert [t[0] for t in tr if t[0] in ("requires_input", "reject")] == [
        "reject",
        "requires_input",
    ]
    assert [t[1] for t in tr if t[0] == "metric"] == ["declined", "open"]
