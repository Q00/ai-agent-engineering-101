"""v1.28 (2026-11-05): the buyer re-orders its candidates by each shop's record with us (SHOP_FILE): P(deal) from our
attempts in the last SHOP_TTL_S times the expected surplus share (1 - our price/list there), and a shop with an a2a
error in the last SHOP_ERR_S goes last. Until then a SKU listed by several shops was tried in shop-name order, and
180 of 263 failed orders (01:07-04:07 KST) stopped at the 15 s food deadline after 1-2 shops. No network."""

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "hooks")):
    if p not in sys.path:
        sys.path.insert(0, p)
import market_buyer as MB  # noqa: E402
import policy as P  # noqa: E402

T0 = 1793760000.0
PRIOR = 1 - MB.SHOP_PRIOR_RATIO


class Clock:
    def __init__(self, t):
        self.t = t

    def time(self):
        return self.t


def _cand(name, sku="G00000", score=1.0):
    return {
        "shop": {"url": f"http://{name}", "name": name},
        "sku": sku,
        "name": "n",
        "score": score,
        "list_price": 1000,
    }


def _rows(*classes, t=T0, ratio=None):
    return [[t, c, ratio if c == "deal" else None] for c in classes]


def _run_main(
    monkeypatch, tmp_path, now, behave, names=("s0", "s1", "s2"), kind="food"
):
    """One hook run at `now` over candidates `names` (same SKU, equal scores, so the live rule tries them in this
    order). behave: shop name -> "deal" | "r0" | "err". Returns (shops tried in order, decision event data)."""
    clock = Clock(now)
    calls, events = [], []
    pol = P.load()
    order = {
        "order": "o-test",
        "budget": 100000,
        "category": "c",
        "kind": kind,
        "use_within_hours": 24,
    }
    cands = [_cand(n) for n in names]
    real_read = MB.read_json

    def fake_haggle(c, o, p, deadline, accept=None):
        name = c["shop"]["name"]
        calls.append(name)
        b = behave.get(name, "r0")
        if b == "err":
            raise httpx.ReadTimeout("The read operation timed out")
        if b == "deal":
            return {"price": 600, "item": c["sku"]}, {
                "rounds": 1,
                "state": "TASK_STATE_COMPLETED",
            }
        return None, {"rounds": 0, "state": "TASK_STATE_REJECTED"}

    class Rec:
        def __init__(self, *a, **k):
            pass

        def write(self, *a, **k):
            pass

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
        if str(path).endswith((MB.SHELF_FILE, MB.SHOP_FILE)):
            return real_read(path, d)
        return d

    def hook_event(typ, key, data, side):
        events.append((typ, key, data))
        return 1

    monkeypatch.setenv("R3_STATE", str(tmp_path / "state"))
    monkeypatch.setattr(MB, "time", clock)
    monkeypatch.setattr(MB, "haggle", fake_haggle)
    monkeypatch.setattr(MB, "Recorder", Rec)
    monkeypatch.setattr(MB, "Reputation", Rep)
    monkeypatch.setattr(MB, "ingest", lambda md, collect=None: (0, {}, 0.0))
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
    monkeypatch.setattr(MB, "hook_event", hook_event)
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
    dec = [d for t, k, d in events if t == "decision"][-1]
    return calls, dec


def _mem(tmp_path):
    return json.loads((tmp_path / "state" / MB.SHOP_FILE).read_text(encoding="utf-8"))[
        "shops"
    ]


# --- weights and ordering


def test_weight_prior_record_price_and_error_penalty():
    assert (
        abs(MB.shop_weight({}, "http://x", "food", T0) - 0.5 * PRIOR) < 1e-9
    )  # never tried: 0.15
    never = {"http://x|food": _rows(*["r0"] * 10)}
    assert abs(MB.shop_weight(never, "http://x", "food", T0) - PRIOR / 12) < 1e-9
    good = {"http://x|food": _rows("deal", "deal", "deal", "r0", ratio=0.6)}
    want = 4 / 6 * (1 - (3 * 0.6 + 2 * MB.SHOP_PRIOR_RATIO) / 5)
    assert abs(MB.shop_weight(good, "http://x", "food", T0) - want) < 1e-9
    err = {"http://x|food": [[T0 - 5 * 60, "err", None]]}
    assert (
        abs(MB.shop_weight(err, "http://x", "food", T0) - PRIOR / 3 * MB.SHOP_ERR_PEN)
        < 1e-9
    )
    old_err = {"http://x|food": [[T0 - MB.SHOP_ERR_S - 1, "err", None]]}
    assert abs(MB.shop_weight(old_err, "http://x", "food", T0) - PRIOR / 3) < 1e-9


def test_rerank_puts_the_accepting_shop_first_and_keeps_ties_in_order():
    mem = {
        "http://a-never|food": _rows("r0", "r0", "r0"),
        "http://c-good|food": _rows("deal", "deal", ratio=0.6),
    }
    out = MB.rerank(
        [_cand("a-never"), _cand("b-new"), _cand("c-good")], mem, "food", T0
    )
    assert [c["shop"]["name"] for c in out] == ["c-good", "b-new", "a-never"]
    assert out[1]["shop_w"] == round(0.5 * PRIOR, 4)
    same = MB.rerank([_cand("x2"), _cand("x1"), _cand("x3")], {}, "food", T0)
    assert [c["shop"]["name"] for c in same] == [
        "x2",
        "x1",
        "x3",
    ]  # no record: choose_offers' order


def test_rerank_keeps_a_higher_score_ahead_of_a_small_weight_edge():
    hi, lo = _cand("hi", "G1", score=10.0), _cand("lo", "G2", score=1.0)
    mem = {"http://lo|food": _rows("deal", "deal", "deal", ratio=0.5)}
    assert [c["shop"]["name"] for c in MB.rerank([lo, hi], mem, "food", T0)] == [
        "hi",
        "lo",
    ]


def test_the_record_is_kept_per_order_kind():
    mem = {
        "http://a|goods": _rows("deal", "deal", ratio=0.6),
        "http://b|food": _rows("deal", ratio=0.6),
    }
    out = MB.rerank([_cand("a"), _cand("b")], mem, "food", T0)
    assert [c["shop"]["name"] for c in out] == ["b", "a"]


# --- the state file


def test_load_drops_old_and_malformed_rows_and_keeps_the_newest(tmp_path):
    path = tmp_path / MB.SHOP_FILE
    rows = [[T0 - MB.SHOP_TTL_S - 1, "deal", 0.5], ["bad"], [T0 - 10, "r0"]] + [
        [T0 - 100 + i, "r0", None] for i in range(MB.SHOP_KEEP + 5)
    ]
    path.write_text(
        json.dumps({"v": 1, "shops": {"http://a|food": rows, "http://b|food": "x"}}),
        encoding="utf-8",
    )
    mem = MB.load_shops(path, T0)
    assert list(mem) == ["http://a|food"] and len(mem["http://a|food"]) == MB.SHOP_KEEP
    assert mem["http://a|food"][-1][0] == T0 - 100 + MB.SHOP_KEEP + 4
    path.write_text("{not json", encoding="utf-8")
    assert MB.load_shops(path, T0) == {}
    assert MB.load_shops(tmp_path / "missing.json", T0) == {}


def test_save_merges_rows_another_process_wrote_meanwhile(tmp_path):
    path = tmp_path / "state" / MB.SHOP_FILE
    MB.save_shops(path, [("http://a|food", [T0, "r0", None])], T0)
    MB.save_shops(
        path, [("http://b|food", [T0 + 1, "deal", 0.6])], T0 + 1
    )  # a second hook, its own new rows
    mem = MB.load_shops(path, T0 + 2)
    assert mem == {
        "http://a|food": [[T0, "r0", None]],
        "http://b|food": [[T0 + 1, "deal", 0.6]],
    }
    assert not list(path.parent.glob("*.tmp"))


# --- the hook


def test_the_hook_learns_from_an_order_and_reorders_the_next(monkeypatch, tmp_path):
    calls, dec = _run_main(monkeypatch, tmp_path, T0, {"s1": "deal"})
    assert calls == ["s0", "s1"] and dec["shop_err"] is None
    mem = _mem(tmp_path)
    assert mem["http://s0|food"] == [[T0, "r0", None]] and mem["http://s1|food"] == [
        [T0, "deal", 0.6]
    ]
    calls, dec = _run_main(monkeypatch, tmp_path, T0 + 60, {"s1": "deal"})
    assert calls == ["s1"]
    assert [r["shop"] for r in dec["ranked"]] == ["s1", "s2", "s0"] and dec["ranked"][
        1
    ]["w"] == round(0.5 * PRIOR, 4)
    calls, _ = _run_main(
        monkeypatch, tmp_path, T0 + 60 + MB.SHOP_TTL_S + 1, {"s1": "deal"}
    )
    assert calls == ["s0", "s1"]  # the record has expired: back to the live order


def test_a_timeout_sends_the_shop_last_for_twenty_minutes(monkeypatch, tmp_path):
    calls, dec = _run_main(monkeypatch, tmp_path, T0, {"s0": "err", "s1": "deal"})
    assert calls == ["s0", "s1"] and _mem(tmp_path)["http://s0|food"] == [
        [T0, "err", None]
    ]
    calls, dec = _run_main(
        monkeypatch, tmp_path, T0 + 60, {"s2": "deal"}, names=("s0", "s2", "s3")
    )
    assert calls == ["s2"] and [r["shop"] for r in dec["ranked"]] == ["s2", "s3", "s0"]
    calls, _ = _run_main(
        monkeypatch,
        tmp_path,
        T0 + MB.SHOP_ERR_S + 1,
        {"s3": "deal"},
        names=("s0", "s3"),
    )
    assert (
        calls[0] == "s3"
    )  # no penalty any more, but one attempt and no deal still ranks below a fresh shop


def test_a_broken_memory_never_costs_the_order(monkeypatch, tmp_path):
    (tmp_path / "state").mkdir(parents=True)
    (tmp_path / "state" / MB.SHOP_FILE).write_text("{oops", encoding="utf-8")
    calls, dec = _run_main(monkeypatch, tmp_path, T0, {"s0": "deal"})
    assert calls == ["s0"] and dec["shop_err"] is None
    assert _mem(tmp_path) == {"http://s0|food": [[T0, "deal", 0.6]]}  # rewritten clean

    def boom(*a, **k):
        raise RuntimeError("weights broke")

    monkeypatch.setattr(MB, "shop_weight", boom)
    calls, dec = _run_main(monkeypatch, tmp_path, T0 + 60, {"s1": "deal"})
    assert calls == ["s0", "s1"] and dec["shop_err"].startswith(
        "RuntimeError: weights broke"
    )
