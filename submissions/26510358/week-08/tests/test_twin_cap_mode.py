"""twin category_cap_mode: raise_all (old default), live (never raise plan's global caps), listed (raise only
the listed categories, the others back at the live caps)."""
import pytest

from r3lib import config, twin

CAT = {
    "K1": {"sku": "K1", "kind": "food", "list_price": 10000, "category": "식품>김치/반찬/즉석요리>김치"},
    "S1": {"sku": "S1", "kind": "goods", "list_price": 100000, "category": "가구/침구>가구>소파/의자/스툴"},
    "D1": {"sku": "D1", "kind": "goods", "list_price": 100000, "category": "가구/침구>침구>이불"},
}
SHARES = {"식품>김치/반찬/즉석요리>김치": 0.40}


def policy(mode=None, **kw):
    o = {"category_budget_share": SHARES, "STOCK_BUDGET_MAX": 1_000_000, "MAX_STOCK_COST": 1_000_000}
    if mode:
        o["category_cap_mode"] = mode
    o.update(kw)
    return twin.make_policy(o)


class _Stock:
    def __init__(self, units=None):
        self.units = units or {}


def test_default_is_raise_all_and_live_policy_has_the_key():
    live = twin.live_policy()
    assert live["category_cap_mode"] == "raise_all"
    assert twin.cap_mode(policy()) == "raise_all"
    assert not twin.restock_differs(live, twin.make_policy({}))
    assert twin.restock_differs(live, twin.make_policy({"category_cap_mode": "live"}))


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        twin.cap_mode({"category_cap_mode": "all"})


def test_raise_all_and_listed_raise_global_caps_live_does_not():
    for mode in (None, "raise_all", "listed"):
        p = twin.effective_plan_policy(policy(mode))
        assert p["CATEGORY_MAX_SHARE"] == 0.40 and p["DEEP_CATEGORY_SHARE"] == 0.40
        assert p["_default_share"] == (config.CATEGORY_MAX_SHARE, config.DEEP_CATEGORY_SHARE)
    p = twin.effective_plan_policy(policy("live"))
    assert p["CATEGORY_MAX_SHARE"] == config.CATEGORY_MAX_SHARE
    assert p["DEEP_CATEGORY_SHARE"] == config.DEEP_CATEGORY_SHARE
    assert "_default_share" not in p


def test_effective_plan_policy_leaves_config_unchanged():
    before = (config.CATEGORY_MAX_SHARE, config.DEEP_CATEGORY_SHARE)
    for mode in ("raise_all", "live", "listed"):
        twin.effective_plan_policy(policy(mode))
    assert (config.CATEGORY_MAX_SHARE, config.DEEP_CATEGORY_SHARE) == before


def _cap(p, share, price):
    stock_max = min(p["STOCK_BUDGET_MAX"], max(p["MAX_STOCK_COST"], p["STOCK_BUDGET_SHARE"] * 0))
    return int(share * stock_max // (price * 0.75))


LINES = [{"sku": "K1", "qty": 1000}, {"sku": "S1", "qty": 1000}, {"sku": "D1", "qty": 1000}]


def test_raise_all_leaves_unlisted_lines_alone():
    p = twin.effective_plan_policy(policy("raise_all"))
    out = twin._apply_category_shares(LINES, _Stock(), 0, p, CAT)
    assert out[1:] == LINES[1:]
    assert out[0] == {"sku": "K1", "qty": _cap(p, 0.40, 10000)}


def test_listed_cuts_unlisted_back_to_live_caps_deep_aware():
    p = twin.effective_plan_policy(policy("listed"))
    deep = {"가구/침구>가구>소파/의자/스툴"}
    out = twin._apply_category_shares(LINES, _Stock(), 0, p, CAT, deep=deep)
    assert out == [{"sku": "K1", "qty": _cap(p, 0.40, 10000)},
                   {"sku": "S1", "qty": _cap(p, config.DEEP_CATEGORY_SHARE, 100000)},
                   {"sku": "D1", "qty": _cap(p, config.CATEGORY_MAX_SHARE, 100000)}]


def test_listed_with_unknown_deep_uses_the_deep_share():
    p = twin.effective_plan_policy(policy("listed"))
    out = twin._apply_category_shares(LINES, _Stock(), 0, p, CAT, deep=None)
    assert out[2] == {"sku": "D1", "qty": _cap(p, config.DEEP_CATEGORY_SHARE, 100000)}


def test_listed_counts_held_stock():
    p = twin.effective_plan_policy(policy("listed"))
    held = _Stock({"D1": [75000] * _cap(p, config.CATEGORY_MAX_SHARE, 100000)})
    out = twin._apply_category_shares(LINES, held, 0, p, CAT, deep=set())
    assert all(ln["sku"] != "D1" for ln in out)


def test_listed_without_raise_keeps_unlisted_lines():
    p = twin.effective_plan_policy(policy("listed", category_budget_share={"식품>김치/반찬/즉석요리>김치": 0.20}))
    assert "_default_share" not in p
    out = twin._apply_category_shares(LINES, _Stock(), 0, p, CAT, deep=set())
    assert out[1:] == LINES[1:]


def test_restock_tick_passes_deep_only_in_listed_mode(monkeypatch):
    seen = []

    class Plan:
        def __call__(self, inv, ts, adjust, policy):
            seen.append(("plan", config.CATEGORY_MAX_SHARE))
            return []

        def deep_categories(self, ts):
            seen.append(("deep", ts))
            return set()

    class W:
        catalog = CAT
        unit_cost = {}
        fill_rate = 1.0

    stock = twin._Stock({}, CAT)
    for mode, raised, asked in (("raise_all", 0.40, False), ("live", config.CATEGORY_MAX_SHARE, False),
                                ("listed", 0.40, True)):
        seen.clear()
        twin._restock_tick(W(), policy(mode), stock, 1_000_000, 100.0, 0, Plan())
        assert seen[0] == ("plan", raised)
        assert (("deep", 100.0) in seen) == asked
    assert config.CATEGORY_MAX_SHARE == twin.live_policy()["CATEGORY_MAX_SHARE"]
