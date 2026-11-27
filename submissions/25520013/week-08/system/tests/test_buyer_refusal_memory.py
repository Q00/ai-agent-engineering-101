"""v1.22 (food only): (shop, SKU) pairs that failed on shelf life (kit checkout refusal from payments.jsonl, or our
walk-away after a stated shelf life) are skipped until the refused unit expires (else 6 h), with a fallback to the
unfiltered ranking when every candidate is skipped; food scores are weighted by 1 - p_refuse(use_within_hours /
shelf_hours); the food opening message states the hours the unit must last. No network."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "hooks")):
    if p not in sys.path:
        sys.path.insert(0, p)
import market_buyer as MB  # noqa: E402
import policy as P  # noqa: E402

T0 = 1793756000.0
H = 3600


class Clock:
    def __init__(self, t):
        self.t = t

    def time(self):
        return self.t


def _reject(shop, sku, reason, code):
    return {
        "type": "reject",
        "data": {
            "order": "o-old",
            "shop": shop,
            "item": sku,
            "price": 1,
            "reason": reason,
            "reason_code": code,
        },
    }


def _run_main(monkeypatch, tmp_path, now, events=(), kind="food", walk=None, n_cands=3):
    """One hook run at time `now`. events: ledger events this run ingests. walk: shop url whose attempt walks away
    with a stated short shelf life. Returns the shop urls tried, in order, and the metrics outcome."""
    clock = Clock(now)
    calls, rows = [], []
    pol = P.load()
    order = {
        "order": "o-test",
        "budget": 100000,
        "category": "c",
        "kind": kind,
        "use_within_hours": 24,
    }
    cands = [
        {
            "shop": {"url": "http://s%d" % i, "name": "s%d" % i},
            "sku": "G%05d" % i,
            "name": "n",
            "score": 1.0,
            "list_price": 1000,
        }
        for i in range(n_cands)
    ]
    real_read = MB.read_json

    def fake_haggle(c, o, p, deadline, accept=None):
        calls.append(c["shop"]["url"])
        if c["shop"]["url"] == walk:
            return None, {
                "rounds": 0,
                "state": "walked_away",
                "why": "stated 10 h shelf life",
            }
        return None, {"rounds": 1, "state": "TASK_STATE_REJECTED"}

    class Rec:
        def __init__(self, *a, **k):
            pass

        def write(self, decision, seq, **k):
            rows.append(k)

    class Rep:
        def __init__(self, *a, **k):
            pass

        def update(self):
            pass

        def table(self, prior):
            return {}

    def read_json(path, d=None):
        if str(path).endswith("order.json"):
            return order
        if str(path).endswith(MB.SHELF_FILE):
            return real_read(path, d)
        return d

    def ingest(md, collect=None):
        collect.extend(events)
        return 0, {}, 0.0

    monkeypatch.setenv("R3_STATE", str(tmp_path / "state"))
    monkeypatch.setattr(MB, "time", clock)
    monkeypatch.setattr(MB, "haggle", fake_haggle)
    monkeypatch.setattr(MB, "Recorder", Rec)
    monkeypatch.setattr(MB, "Reputation", Rep)
    monkeypatch.setattr(MB, "ingest", ingest)
    monkeypatch.setattr(MB, "read_json", read_json)
    monkeypatch.setattr(
        MB.OW,
        "resolve",
        lambda pol: {
            "names": set(),
            "urls": set(),
            "pubkeys": {},
            "source": "test",
            "error": None,
        },
    )
    monkeypatch.setattr(MB, "volume_level", lambda side, pol: (0.0, 0))
    monkeypatch.setattr(MB, "catch_up_stats", lambda catalog: (None, {}, None))
    monkeypatch.setattr(MB.P, "load", lambda *a, **k: dict(pol, _hash="test"))
    monkeypatch.setattr(MB.P, "choose_offers", lambda *a, **k: [dict(c) for c in cands])
    monkeypatch.setattr(MB.P, "accept_policy", lambda *a, **k: {})
    monkeypatch.setattr(MB, "hook_event", lambda *a, **k: 1)
    monkeypatch.setattr(MB, "emit", lambda out: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "market_buyer.py",
            "--shops",
            str(tmp_path / "shops.json"),
            "--order",
            str(tmp_path / "order.json"),
        ],
    )
    MB.main()
    return calls, rows[-1]["outcome"]


def _state(tmp_path):
    return json.loads((tmp_path / "state" / MB.SHELF_FILE).read_text(encoding="utf-8"))


# --- refusal memory


def test_kit_refusal_skipped_until_unit_expires(monkeypatch, tmp_path):
    ev = _reject(
        "http://s0",
        "G00000",
        "checkout refused: 3.0 h of shelf life left, the policy asks for 26",
        "checkout_refused",
    )
    calls, out = _run_main(monkeypatch, tmp_path, T0, [ev])
    assert calls == ["http://s1", "http://s2"] and out["shelf_skipped"] == 1
    st = _state(tmp_path)
    assert st["v"] == 1 and st["pairs"]["http://s0|G00000"]["until"] == T0 + 3 * H
    calls, _ = _run_main(
        monkeypatch, tmp_path, T0 + 2 * H
    )  # next order, still within the TTL
    assert calls[0] == "http://s1"
    calls, out = _run_main(
        monkeypatch, tmp_path, T0 + 3 * H + 1
    )  # the refused unit has expired
    assert calls[0] == "http://s0" and out["shelf_skipped"] == 0
    assert _state(tmp_path)["pairs"] == {}  # pruned


def test_refusal_without_hours_uses_6h_ttl(monkeypatch, tmp_path):
    ev = _reject("s0", "G00000", "shelf life too short", "shelf_life")  # shop by name
    calls, _ = _run_main(monkeypatch, tmp_path, T0, [ev])
    assert calls[0] == "http://s1"
    assert _state(tmp_path)["pairs"]["s0|G00000"]["until"] == T0 + 6 * H
    assert _run_main(monkeypatch, tmp_path, T0 + 6 * H - 1)[0][0] == "http://s1"
    assert _run_main(monkeypatch, tmp_path, T0 + 6 * H + 1)[0][0] == "http://s0"


def test_other_refusals_not_remembered(monkeypatch, tmp_path):
    ev = _reject("http://s0", "G00000", "price above the mandate", "over_cap")
    calls, out = _run_main(monkeypatch, tmp_path, T0, [ev])
    # v1.28b: any refused checkout sends s0 last for SHOP_ERR_S; the shelf filter still skips nothing
    assert calls[0] == "http://s1" and out["shelf_skipped"] == 0
    assert not (tmp_path / "state" / MB.SHELF_FILE).exists()


def test_walkaway_on_stated_shelf_life_is_remembered(monkeypatch, tmp_path):
    calls, _ = _run_main(monkeypatch, tmp_path, T0, walk="http://s0")
    assert calls[0] == "http://s0"
    assert _state(tmp_path)["pairs"]["http://s0|G00000"]["until"] == T0 + 10 * H
    calls, _ = _run_main(monkeypatch, tmp_path, T0 + 60)
    assert calls[0] == "http://s1"


def test_all_skipped_falls_back_to_unfiltered(monkeypatch, tmp_path):
    evs = [
        _reject("http://s%d" % i, "G%05d" % i, "2 h of shelf life left", "shelf_life")
        for i in range(3)
    ]
    calls, out = _run_main(monkeypatch, tmp_path, T0, evs)
    assert (
        calls == ["http://s0", "http://s1", "http://s2"] and out["shelf_skipped"] == 3
    )


def test_goods_not_filtered(monkeypatch, tmp_path):
    ev = _reject("http://s0", "G00000", "2 h of shelf life left", "shelf_life")
    calls, out = _run_main(monkeypatch, tmp_path, T0, [ev], kind="goods")
    # v1.28b: the refused checkout sends s0 last; goods are not filtered on shelf life
    assert calls[0] == "http://s1" and out["shelf_skipped"] == 0
    calls, _ = _run_main(
        monkeypatch, tmp_path, T0 + 60, kind="food"
    )  # remembered for the next food order
    assert calls[0] == "http://s1"


# --- p_refuse weighting


@pytest.mark.parametrize(
    "use,shelf,p",
    [
        (5, 100, 0.017),
        (24.9, 100, 0.017),
        (25, 100, 0.11),
        (50, 100, 0.34),
        (75, 100, 0.64),
        (200, 100, 0.64),
        (None, 100, None),
        (24, None, None),
        (24, 0, None),
    ],
)
def test_p_refuse_steps(use, shelf, p):
    assert P.p_refuse(use, shelf) == p


def _offers(kind, shelf_a=30, shelf_b=200):
    pol = P.load()
    shops = [
        {
            "url": "http://x",
            "name": "x",
            "offers": [{"sku": "GA0001", "qty": 1}, {"sku": "GB0001", "qty": 1}],
        }
    ]
    catalog = {
        "GA0001": {"list_price": 1000, "shelf_hours": shelf_a},
        "GB0001": {"list_price": 800, "shelf_hours": shelf_b},
    }
    order = {"kind": kind, "budget": 100000, "use_within_hours": 24}
    own = {"names": set(), "urls": set(), "pubkeys": {}}
    return P.choose_offers(order, shops, catalog, pol, {}, own)


def test_p_refuse_weighting_reorders_food():
    # A: list 1000, r = 24/30 = 0.8 -> x0.36; B: list 800, r = 24/200 = 0.12 -> x0.983
    out = _offers("food")
    assert [c["sku"] for c in out] == ["GB0001", "GA0001"]
    assert out[1]["score"] == pytest.approx((1000 - 550) * (1 - 0.64))


def test_p_refuse_skipped_when_missing_and_for_goods():
    assert [c["sku"] for c in _offers("food", shelf_a=None, shelf_b=None)] == [
        "GA0001",
        "GB0001",
    ]
    assert [c["sku"] for c in _offers("goods")] == ["GA0001", "GB0001"]


# --- opening message


def _opening(monkeypatch, order, accept):
    sent = []

    def rpc(url, text, task_id=None, timeout=60):
        sent.append(text)
        return {
            "id": "t",
            "status": {
                "state": "TASK_STATE_REJECTED",
                "message": {"parts": [{"text": "none"}]},
            },
        }

    monkeypatch.setattr(MB, "endpoint", lambda url, timeout=20: url)
    monkeypatch.setattr(MB, "rpc", rpc)
    monkeypatch.setattr(MB, "cancel", lambda url, tid: None)
    cand = {
        "shop": {"url": "http://s0", "name": "s0"},
        "sku": "G00001",
        "name": "Milk",
        "list_price": 1000,
        "max_pay": 850,
        "open": 550,
    }
    MB.haggle(cand, order, P.load(), 1e12, accept)
    return sent[0]


def test_food_opening_states_hours(monkeypatch):
    order = {"kind": "food", "use_within_hours": 24}
    accept = P.accept_policy(order, P.load())
    assert accept["min_hours_left"] == 26  # use_within_hours + hours_margin 2
    assert (
        _opening(monkeypatch, order, accept)
        == "I want to buy Milk (G00001). The unit must last at least 26 hours."
    )


def test_goods_opening_unchanged(monkeypatch):
    order = {"kind": "goods"}
    assert (
        _opening(monkeypatch, order, P.accept_policy(order, P.load()))
        == "I want to buy Milk (G00001)"
    )
