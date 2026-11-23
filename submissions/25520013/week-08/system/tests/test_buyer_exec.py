"""buyer_exec runs a validated plan against a fake A2A shop (no network)."""

import json

import pytest
from kernel import buyer_exec as bx
from kernel import rules
from kernel.trajectory import Trajectory

A, B = "https://a.example", "https://b.example"
CTX = {
    "budget": 1000,
    "shops": [
        {"url": A, "offers": [{"sku": "BED1", "name": "Oak Bed"}]},
        {"url": B, "offers": [{"sku": "BED1", "name": "Oak Bed"}]},
    ],
}
ORDER = {"order": "o1", "item": "BED1", "budget": 1000}


class FakeShop:
    """Asks the prices in `asks` in turn; an offer >= the current ask completes with a receipt at that offer
    (or at `charge` if set). After the last ask it keeps repeating it."""

    def __init__(self, asks, charge=None):
        self.asks, self.charge = list(asks), charge
        self.i, self.got, self.cancelled = 0, [], False

    def task(self, st, text="", receipt=None):
        t = {
            "id": "t1",
            "status": {"state": st, "message": {"parts": [{"text": text}]}},
        }
        if receipt:
            t["artifacts"] = [{"parts": [{"data": receipt}]}]
        return {"result": {"task": t}}

    def __call__(self, body):
        if body["method"] == "CancelTask":
            self.cancelled = True
            return {"result": {}}
        text = body["params"]["message"]["parts"][0]["text"]
        ask = self.asks[min(self.i, len(self.asks) - 1)]
        if text.isdigit():
            offer = int(text)
            self.got.append(offer)
            if offer >= ask:
                price = self.charge or offer
                rec = {
                    "shop": "x",
                    "item": "BED1",
                    "price": price,
                    "task": "t1",
                    "sig_seller": "c2ln",
                }
                return self.task(bx.COMPLETED, "Deal.", rec)
            self.i += 1
            return self.task(
                bx.INPUT_REQUIRED,
                f"Counteroffer: {self.asks[min(self.i, len(self.asks) - 1)]}",
            )
        return self.task(bx.INPUT_REQUIRED, f"We have it, asking {ask}")


class Net:
    """transport(url, body, timeout) over fake shops keyed by base url; optional clock step per request."""

    def __init__(self, shops, clock=None, step=0.0, raise_for=None):
        self.shops, self.clock, self.step, self.raise_for = (
            shops,
            clock,
            step,
            raise_for,
        )
        self.timeouts = []

    def __call__(self, url, body, timeout):
        self.timeouts.append(timeout)
        if self.clock is not None:
            self.clock[0] += self.step
        base = url.split("/.well-known")[0].removesuffix("/a2a")
        if self.raise_for == base:
            raise bx.Timeout("slow shop")
        if body is None:
            return {"supportedInterfaces": [{"url": base + "/a2a"}]}
        return self.shops[base](body)


def plan(*cands):
    return {
        "candidates": [
            dict(shop=s, sku="BED1", offers=o, max_pay=m) for s, o, m in cands
        ]
    }


@pytest.fixture
def traj(tmp_path):
    return Trajectory(str(tmp_path / "t.jsonl"))


def rows(traj):
    return [r for r in traj.since(0) if r["kind"] == "buyer_exec"]


def test_accepts_when_ask_within_max_pay_after_second_offer(traj):
    shop = FakeShop([600, 550, 480])
    out = bx.execute(
        plan((A, [300, 400], 500)), CTX, ORDER, traj, transport=Net({A: shop})
    )
    assert shop.got == [300, 400, 480]
    assert out["shop"] == A and out["receipt"]["price"] == 480 and out["stars"] == 4
    att, outcome = rows(traj)
    assert (
        att["event"] == "attempt"
        and att["result"] == "accepted"
        and att["final_ask"] == 480
    )
    assert att["offers_sent"] == [300, 400, 480] and att["shop"].startswith("h")
    assert outcome["event"] == "outcome" and outcome["deal"] and outcome["price"] == 480
    assert "example" not in json.dumps(traj.since(0))


def test_walks_when_offers_run_out_then_next_candidate(traj):
    a, b = FakeShop([900, 850, 800]), FakeShop([450])
    out = bx.execute(
        plan((A, [300, 400], 500), (B, [200], 500)),
        CTX,
        ORDER,
        traj,
        transport=Net({A: a, B: b}),
    )
    assert a.got == [300, 400] and a.cancelled
    assert b.got == [450] and out["shop"] == B and out["receipt"]["price"] == 450
    r = rows(traj)
    assert [x.get("result") for x in r] == ["walked", "accepted", None]
    assert r[0]["final_ask"] == 800 and r[2]["tried"] == 2


def test_never_pays_above_max_pay(traj):
    shop = FakeShop(
        [300], charge=700
    )  # the shop signs a higher price than the one we accepted
    out = bx.execute(plan((A, [100], 500)), CTX, ORDER, traj, transport=Net({A: shop}))
    assert out["receipt"] is None and out["review"] == "no deal"
    assert all(x <= 500 for x in shop.got)
    assert rows(traj)[0]["result"] == "error" and not rows(traj)[-1]["deal"]


def test_never_sends_above_budget(traj):
    shop = FakeShop([900])
    ctx = dict(CTX, budget=400)
    out = bx.execute(plan((A, [100], 500)), ctx, ORDER, traj, transport=Net({A: shop}))
    assert out["receipt"] is None and shop.got == [100]


def test_deadline_stops_the_loop(traj):
    clock = [0.0]
    a, b = FakeShop([900]), FakeShop([10])
    net = Net({A: a, B: b}, clock=clock, step=10.0)
    out = bx.execute(
        plan((A, [100, 200, 300], 500), (B, [5], 500)),
        CTX,
        ORDER,
        traj,
        transport=net,
        deadline=25.0,
        clock=lambda: clock[0],
    )
    assert out["receipt"] is None and b.got == []
    r = rows(traj)
    assert r[0]["result"] == "timeout" and a.cancelled
    assert r[-1]["stop"] == "deadline" and r[-1]["tried"] == 1


def test_request_timeout_moves_on(traj):
    b = FakeShop([100])
    out = bx.execute(
        plan((A, [100], 500), (B, [50], 500)),
        CTX,
        ORDER,
        traj,
        transport=Net({B: b}, raise_for=A),
    )
    assert out["shop"] == B and out["receipt"]["price"] == 100
    assert [x.get("result") for x in rows(traj)][:2] == ["timeout", "accepted"]


def test_empty_plan_buys_nothing(traj):
    out = bx.execute({"candidates": []}, CTX, ORDER, traj, transport=None)
    assert out["receipt"] is None and rows(traj)[-1]["tried"] == 0


@pytest.mark.parametrize(
    "cand,budget",
    [
        ({"offers": [300, 600], "max_pay": 500}, 1000),  # offer above max_pay
        ({"offers": [300], "max_pay": 1001}, 1000),  # max_pay above budget
        ({"offers": [True], "max_pay": 500}, 1000),  # bool price
        ({"offers": [300], "max_pay": True}, 1000),  # bool max_pay
        (
            {"shop": "https://z.example", "offers": [300], "max_pay": 500},
            1000,
        ),  # unknown shop
        ({"sku": "SOFA1", "offers": [300], "max_pay": 500}, 1000),  # unknown sku
    ],
)
def test_validator_rejects(cand, budget):
    raw = {"candidates": [dict({"shop": A, "sku": "BED1"}, **cand)]}
    d, errors = rules.validate("buyer.plan", raw, dict(CTX, budget=budget))
    assert errors and d == {"candidates": []}


def test_accept_conditions_shape_and_passthrough():
    from kernel import rules

    ctx = {"budget": 1000, "shops": [{"url": "https://a", "offers": [{"sku": "S"}]}]}
    cand = {"shop": "https://a", "sku": "S", "offers": [500], "max_pay": 800}
    ok = {"candidates": [cand], "accept": {"min_hours_left": 3, "grades": ["B", "A"], "cert": True}}
    d, e = rules.validate("buyer.plan", ok, ctx)
    assert not e and d["accept"] == {"min_hours_left": 3, "grades": ["A", "B"], "cert": True}
    for bad in ({"grades": ["D"]}, {"min_hours_left": -1}, {"cert": 1}, {"price_floor": 3}, [1]):
        d, e = rules.validate("buyer.plan", {"candidates": [cand], "accept": bad}, ctx)
        assert e and d == {"candidates": []}, bad


def test_accept_goes_to_output_line(traj):
    acc = {"min_hours_left": 3, "grades": ["A", "B"]}
    out = bx.execute({"candidates": [], "accept": acc}, CTX, ORDER, traj, transport=None)
    assert out["accept"] == acc and "accept" not in bx.execute({"candidates": []}, CTX, ORDER, traj, transport=None)
