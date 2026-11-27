"""v1.22: food orders stop new attempts and haggle rounds after FOOD_DEADLINE_S (15 s, policy
buyer.defaults.{kind}.deadline_s overrides); goods keep DEADLINE_S (200 s) and their normal rpc timeouts. Under the
food deadline each haggle message waits at most until the deadline (floor RPC_FLOOR_S), except the message that
accepts the shop's ask (the checkout step), which keeps the normal 60 s and is never cut. No network: time, endpoint,
rpc and cancel are faked."""

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


class Clock:
    def __init__(self, t):
        self.t = t

    def time(self):
        return self.t


def _task(st, text="", receipt=None):
    t = {
        "id": "task-1",
        "status": {"state": st, "message": {"parts": [{"text": text}]}},
    }
    if receipt:
        t["artifacts"] = [{"parts": [{"data": receipt}]}]
    return t


def _cand(sku="G00001"):
    return {
        "shop": {"url": "http://s0", "name": "s0"},
        "sku": sku,
        "name": "Milk",
        "list_price": 1000,
        "max_pay": 850,
        "open": 550,
        "score": 1.0,
    }


def _fake_shop(monkeypatch, clock, replies, step_s):
    """replies: list of (state, text, receipt) answered in turn; each message costs step_s[i] seconds."""
    sent = []

    def rpc(url, text, task_id=None, timeout=60):
        i = len([s for s in sent if "text" in s])
        sent.append({"at": clock.t, "text": text, "timeout": timeout})
        clock.t += step_s[min(i, len(step_s) - 1)]
        st, txt, rec = replies[min(i, len(replies) - 1)]
        return _task(st, txt, rec)

    monkeypatch.setattr(MB, "time", clock)
    monkeypatch.setattr(
        MB,
        "endpoint",
        lambda url, timeout=20: sent.append({"card_timeout": timeout}) or url,
    )
    monkeypatch.setattr(MB, "rpc", rpc)
    monkeypatch.setattr(MB, "cancel", lambda url, tid: None)
    return sent


def _msgs(sent):
    return [s for s in sent if "text" in s]


# --- deadline per kind


def test_defaults_food_15_goods_200():
    pol = P.load()
    assert MB.FOOD_DEADLINE_S == 15 and MB.DEADLINE_S == 200
    assert MB.kind_deadline_s({"kind": "food"}, pol) == 15
    assert MB.kind_deadline_s({"kind": "goods"}, pol) == 200
    assert (
        MB.kind_deadline_s({}, pol) == 200
    )  # no kind = goods (as elsewhere in the hook)


def test_live_policy_has_no_deadline_key():
    b = P.load()["buyer"]["defaults"]
    assert "deadline_s" not in b["food"] and "deadline_s" not in b["goods"]


def test_food_stops_new_attempts_after_15s_goods_after_200s():
    pol = P.load()
    food, goods = {"kind": "food"}, {"kind": "goods"}
    assert MB.attempt_allowed(1, 15, T0 + 15, food, pol) == (True, None)
    assert MB.attempt_allowed(1, 15.5, T0 + 15.5, food, pol) == (False, "deadline")
    assert MB.attempt_allowed(1, 150, T0 + 150, goods, pol) == (True, None)
    assert MB.attempt_allowed(1, 201, T0 + 201, goods, pol) == (False, "deadline")
    assert MB.attempt_deadline(0, T0, food, pol) == T0 + 15
    assert MB.attempt_deadline(0, T0, goods, pol) == T0 + 200


def test_policy_override():
    pol = P.load()
    pol["buyer"]["defaults"]["food"]["deadline_s"] = 40
    pol["buyer"]["defaults"]["goods"]["deadline_s"] = 100
    assert P.validate(pol)
    assert MB.attempt_allowed(1, 30, T0 + 30, {"kind": "food"}, pol) == (True, None)
    assert MB.attempt_allowed(1, 41, T0 + 41, {"kind": "food"}, pol) == (
        False,
        "deadline",
    )
    assert MB.attempt_deadline(0, T0, {"kind": "food"}, pol) == T0 + 40
    assert MB.attempt_allowed(1, 101, T0 + 101, {"kind": "goods"}, pol) == (
        False,
        "deadline",
    )


@pytest.mark.parametrize("v", [0, -1, 281, True, "x"])
def test_validate_rejects_bad_deadline(v):
    pol = P.load()
    pol["buyer"]["defaults"]["food"]["deadline_s"] = v
    with pytest.raises(P.PolicyError):
        P.validate(pol)


# --- haggle: rounds and rpc timeouts


def test_food_haggle_stops_rounds_at_deadline(monkeypatch):
    clock = Clock(T0)
    sent = _fake_shop(
        monkeypatch,
        clock,
        [("TASK_STATE_INPUT_REQUIRED", "Milk: asking 2000", None)],
        [4],
    )
    rec, info = MB.haggle(
        _cand(), {"kind": "food", "budget": 900}, P.load(), T0 + 15, {}
    )
    msgs = _msgs(sent)
    assert rec is None and info["deadline_cut"] is True
    assert all(m["at"] < T0 + 15 for m in msgs)  # nothing sent at or after the deadline
    assert len(msgs) == 4  # opening + 3 rounds (T0, +4, +8, +12)


def test_goods_haggle_keeps_rounds_and_timeouts(monkeypatch):
    clock = Clock(T0)
    sent = _fake_shop(
        monkeypatch,
        clock,
        [("TASK_STATE_INPUT_REQUIRED", "Drill: asking 2000", None)],
        [4],
    )
    rec, info = MB.haggle(
        _cand(), {"kind": "goods", "budget": 900}, P.load(), T0 + 200, {}
    )
    msgs = _msgs(sent)
    assert rec is None and "deadline_cut" not in info
    assert len(msgs) > 4 and msgs[-1]["at"] > T0 + 15  # goods haggle on past 15 s
    assert {m["timeout"] for m in msgs} == {60} and sent[0]["card_timeout"] == 20


def test_food_rpc_timeout_capped_by_time_left(monkeypatch):
    clock = Clock(T0)
    sent = _fake_shop(
        monkeypatch,
        clock,
        [("TASK_STATE_INPUT_REQUIRED", "Milk: asking 2000", None)],
        [5, 5, 4.5, 1],
    )
    MB.haggle(_cand(), {"kind": "food", "budget": 900}, P.load(), T0 + 15, {})
    msgs = _msgs(sent)
    assert sent[0]["card_timeout"] == 15
    # sent at T0, T0+5, T0+10, T0+14.5: time left 15, 10, 5, 0.5 -> floor 3
    assert [m["timeout"] for m in msgs] == [15, 10, 5, MB.RPC_FLOOR_S]


def test_checkout_in_progress_at_deadline_is_completed(monkeypatch):
    clock = Clock(T0)
    receipt = {
        "shop": "s0",
        "item": "G00001",
        "price": 800,
        "task": "task-1",
        "sig_seller": "x",
    }
    replies = [
        ("TASK_STATE_INPUT_REQUIRED", "Milk: asking 900", None),
        ("TASK_STATE_INPUT_REQUIRED", "Counteroffer: 800", None),
        ("TASK_STATE_COMPLETED", "Sold", receipt),
    ]
    # opening 5 s, first offer 9 s (now T0+14 < deadline), the accepting offer takes 30 s (ends past the deadline)
    sent = _fake_shop(monkeypatch, clock, replies, [5, 9, 30])
    rec, info = MB.haggle(
        _cand(), {"kind": "food", "budget": 900}, P.load(), T0 + 15, {}
    )
    msgs = _msgs(sent)
    assert [m["text"] for m in msgs[1:]] == ["550", "800"]
    assert msgs[2]["timeout"] == 60  # the checkout step keeps its normal timeout
    assert clock.t > T0 + 15
    assert (
        rec is not None
        and rec["price"] == 800
        and info["state"] == "TASK_STATE_COMPLETED"
    )
    assert "deadline_cut" not in info


# --- main: attempts and metrics


def _run_main(monkeypatch, tmp_path, kind, step_s, n_cands=8, pol=None):
    clock = Clock(T0)
    calls, rows = [], []
    pol = pol or P.load()
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
            "sku": "G%d" % i,
            "name": "n",
            "score": 1.0,
            "list_price": 1000,
        }
        for i in range(n_cands)
    ]

    def fake_haggle(c, o, p, deadline, accept=None):
        calls.append((c["shop"]["url"], deadline))
        clock.t += step_s
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

    monkeypatch.setenv("R3_STATE", str(tmp_path / "state"))
    monkeypatch.setattr(MB, "time", clock)
    monkeypatch.setattr(MB, "haggle", fake_haggle)
    monkeypatch.setattr(MB, "Recorder", Rec)
    monkeypatch.setattr(MB, "Reputation", Rep)
    monkeypatch.setattr(MB, "ingest", lambda md, collect=None: (0, {}, 0.0))
    monkeypatch.setattr(
        MB,
        "read_json",
        lambda path, d=None: order if str(path).endswith("order.json") else d,
    )
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
    monkeypatch.setattr(MB.P, "choose_offers", lambda *a, **k: cands)
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


def test_main_food_stops_after_15s(monkeypatch, tmp_path):
    calls, out = _run_main(monkeypatch, tmp_path, "food", 6)
    # attempts start at elapsed 0, 6, 12; at 18 > 15 the loop stops
    assert len(calls) == 3 and {d for _, d in calls} == {T0 + 15}
    assert (
        out["stop"] == "deadline"
        and out["deadline_hit"] is True
        and out["deadline_s"] == 15
    )


def test_main_goods_keeps_200s(monkeypatch, tmp_path):
    calls, out = _run_main(monkeypatch, tmp_path, "goods", 6)
    assert len(calls) == 6 and calls[0][1] == T0 + 200
    assert (
        out["stop"] == "max_shops"
        and out["deadline_hit"] is False
        and out["deadline_s"] == 200
    )


def test_main_food_override(monkeypatch, tmp_path):
    pol = P.load()
    pol["buyer"]["defaults"]["food"]["deadline_s"] = 30
    calls, out = _run_main(monkeypatch, tmp_path, "food", 6, pol=pol)
    assert len(calls) == 6 and calls[0][1] == T0 + 30 and out["deadline_s"] == 30
