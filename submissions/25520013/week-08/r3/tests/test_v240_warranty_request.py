"""v2.4 (2026-11-07, stars): a goods receipt carries the policy's warranty hours when the request mentions a warranty
and price x (1 - fail rate) still clears the floor, even where the policy's warranty_uplift test leaves it off."""

import inspect

import pytest

from tests import test_v210_stars as T

S, P, R3 = T.S, T.P, T.R3
ASK = "I want to buy 매트리스 (G0000a1). I buy grade A. Grade B only with a warranty of at least 6 hours on the receipt."


@pytest.fixture(autouse=True)
def _buyer_log_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("R3_BUYER_LOG", str(tmp_path / "buyer_attempts.jsonl"))  # never the repo's logs/


@pytest.fixture
def pol():
    return P.load(R3 / "policy.json")


def _hours(pol, unit):
    c = P.resolve(pol, unit.get("category"), "goods")
    return min(int(c["warranty_hours"][unit["grade"]]), int(c.get("max_warranty_hours", 6)))


def test_warranty_when_asked_and_the_price_covers_the_refunds(pol):
    u = T.goods("u1", "A", 1, cost=550_000)
    assert _hours(pol, u) > 0
    assert S.warranty_on_request(ASK, u, pol, None, 700_000, 600_000) == _hours(pol, u)   # 700k x 0.95 >= 600k
    assert S.warranty_on_request("매트리스 보증 있나요 (G0000a1)", u, pol, None, 700_000, 600_000) == _hours(pol, u)


def test_no_warranty_when_not_asked_or_the_margin_is_too_thin(pol):
    u = T.goods("u1", "A", 1, cost=550_000)
    assert S.warranty_on_request("I want to buy 매트리스 (G0000a1)", u, pol, None, 700_000, 600_000) == 0
    assert S.warranty_on_request(ASK, u, pol, None, 620_000, 600_000) == 0                 # 620k x 0.95 < 600k
    assert S.warranty_on_request(ASK, T.food("f1", 600), pol, None, 2000, 1050) == 0


def test_the_category_fail_rate_from_stats_is_used(pol, monkeypatch):
    u = T.goods("u1", "A", 1)
    monkeypatch.setattr(P, "_stat", lambda stats, kind, key: {"n": 50, "fail_rate": 0.2})
    assert S.warranty_on_request(ASK, u, pol, object(), 700_000, 600_000) == 0             # 700k x 0.8 < 600k
    monkeypatch.setattr(P, "_stat", lambda stats, kind, key: {"n": 5, "fail_rate": 0.2})  # too few: prior 0.05
    assert S.warranty_on_request(ASK, u, pol, object(), 700_000, 600_000) == _hours(pol, u)


def test_the_accept_path_adds_it_only_when_the_policy_did_not():
    src = inspect.getsource(S.Seller._execute)
    assert '"text": text,' in src
    i = src.index("terms = P.receipt_terms(")
    assert i < src.index('if not terms.get("warranty_hours"):') < src.index("warranty_on_request(deal.get(\"text\")") < src.index("receipt = sign(receipt)")
