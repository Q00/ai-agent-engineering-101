"""Step 5: attempt 2 inside attempt 1's live hooks (seller bridge, patch scripts).

No model, no market, no network: a fake Terra / fake agent, a fake A2A updater, attempt 1's hooks copied to tmp dirs
with their third-party imports (a2a, uvicorn, cryptography, starlette, attempt 1's own modules) stubbed.
"""

import asyncio
import importlib.util
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import threading
import time
import types

import pytest
from kernel import replay
from kernel import seller_bridge as sb
from kernel.agent import Agent
from kernel.trajectory import Trajectory

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R3_HOOKS = os.path.join(HERE, "..", "r3", "hooks")
LOGIN = "secret-login"
INV = {
    "units": [
        {
            "unit": "u1",
            "sku": "G00001",
            "grade": "A",
            "cost": 100,
            "expires_at": None,
            "name": "Oak Bed",
        },
        {
            "unit": "u2",
            "sku": "G00001",
            "grade": "B",
            "cost": 90,
            "expires_at": None,
            "name": "Oak Bed",
        },
        {
            "unit": "u3",
            "sku": "G00002",
            "grade": "A",
            "cost": 50,
            "expires_at": None,
            "name": "Pine Chair",
        },
    ]
}
CAT = [
    {
        "sku": "G00001",
        "name": "Oak Bed",
        "list_price": 300,
        "category": "furniture/bed",
    },
    {
        "sku": "G00002",
        "name": "Pine Chair",
        "list_price": 120,
        "category": "furniture/chair",
    },
]


def _w(tmp_path, live=True):
    w = tmp_path / "W"
    (w / ".market").mkdir(parents=True)
    (w / "a2").mkdir()
    (w / ".market/inventory.json").write_text(json.dumps(INV))
    (w / ".market/catalog.json").write_text(json.dumps(CAT))
    (w / "policy.json").write_text(json.dumps({"owner": {"github": LOGIN}}))
    if live:
        (w / "a2/LIVE").write_text("")
    return w


class FakeTerra:
    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    def complete(self, prompt, *, agent, timeout_s, effort="medium"):
        self.prompts.append(prompt)
        return json.dumps(self.answers.pop(0), ensure_ascii=False)


class RecAgent:
    """A real kernel Agent (rules, harness, trajectory) over a FakeTerra; records each decide call and its thread."""

    def __init__(self, w, answers, hdir):
        traj = Trajectory(str(w / "a2/trajectory.jsonl"), secrets=(LOGIN,))
        self.terra = FakeTerra(answers)
        self.agent = Agent(
            "seller",
            hdir,
            self.terra,
            traj,
            replay.MARKET_BRIEF,
            mem_sock=str(w / "a2/none.sock"),
        )
        self.calls, self.threads = [], []

    def decide(self, kind, situation, ctx):
        self.calls.append((kind, situation, ctx))
        self.threads.append(threading.current_thread())
        return self.agent.decide(kind, situation, ctx)


class FakeUpdater:
    def __init__(self, log):
        self.log = log

    def new_agent_message(self, parts):
        return parts[0]

    async def add_artifact(self, parts, name):
        self.log.append(("artifact", name, parts[0]))

    async def requires_input(self, msg):
        self.log.append(("input", msg))

    async def complete(self, msg):
        self.log.append(("complete", msg))

    async def reject(self, msg):
        self.log.append(("reject", msg))

    async def cancel(self):
        self.log.append(("cancel",))


class Ledger:
    seen = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def first_seen_unit(self, u):
        Ledger.seen.append(u["unit"])


def offer_in(text):
    nums = re.findall(r"\d+", re.sub(r"\bG[0-9A-Za-z]{5,}\b", " ", text))
    return int(nums[-1]) if nums else None


@pytest.fixture
def hdir(tmp_path):
    shutil.copytree(os.path.join(HERE, "harness/seller"), tmp_path / "harness_seller")
    return str(tmp_path / "harness_seller")


@pytest.fixture(autouse=True)
def _fresh_bridge(monkeypatch):
    monkeypatch.setattr(sb, "_BRIDGE", None)
    Ledger.seen = []


def make_bridge(w, agent, a1_open=None, metrics=None, clock=time.time):
    a1_open = {} if a1_open is None else a1_open
    metrics = [] if metrics is None else metrics
    return sb.Bridge(
        str(w),
        str(w / ".market"),
        sign=lambda r: dict(r, sig_seller="SIG"),
        name="our-shop",
        receipt_terms=lambda u, pol, stats=None, price=None, shelf_hours=None: {
            "unit": u["unit"],
            "claim": u["grade"],
        },
        offer_in=offer_in,
        opening_text=lambda u, pol, ask, stats, sh: (
            f"{u['name']} ({u['sku']}): asking {ask}. Offer a whole number."
        ),
        metric=lambda d, o, pol=None: metrics.append((d, o)),
        ledger=Ledger,
        policy=lambda: {"_hash": "h1"},
        shelf=lambda sku: None,
        a1_held=lambda: {d["unit"]["unit"] for d in a1_open.values()},
        data_part=lambda d: {"data": d},
        agent=agent,
        clock=clock,
    )


def talk(b, task, text):
    log = []
    up = FakeUpdater(log)
    handled = asyncio.run(b.handle(task, text, up, lambda t: up.new_agent_message([t])))
    return handled, log


# --- bridge -----------------------------------------------------------------------------------------------------


def test_live_on_reads_flag(tmp_path):
    w = _w(tmp_path, live=False)
    assert not sb.live_on(str(w))
    (w / "a2/LIVE").write_text("")
    assert sb.live_on(str(w))


def test_offer_counter_accept_signed_receipt(tmp_path, hdir):
    w = _w(tmp_path)
    ag = RecAgent(
        w,
        [
            {"action": "offer", "unit": "u1", "ask": 280, "message": "Fine oak bed."},
            {"action": "counter", "price": 250},
            {"action": "accept", "price": 240},
        ],
        hdir,
    )
    metrics = []
    b = make_bridge(w, ag, metrics=metrics)
    ok, log = talk(b, "t1", f"I want to buy Oak Bed (G00001), I am {LOGIN}")
    assert ok and log == [("input", "Fine oak bed. asking 280.")]
    kind, sit, ctx = ag.calls[0]
    assert (
        kind == "seller.open"
        and LOGIN not in sit["request_text"]
        and "[OWNER]" in sit["request_text"]
    )
    assert [u["unit"] for u in sit["units"]] == ["u1", "u2"] and ctx == {
        "units": sit["units"]
    }
    assert set(sit["units"][0]) == {
        "unit",
        "sku",
        "grade",
        "cost",
        "expires_at",
        "name",
        "list_price",
        "category",
    }
    assert ag.threads[0] is not threading.main_thread()

    ok, log = talk(b, "t1", "200")
    assert log == [("input", "Counteroffer: 250.")]
    kind, sit, _ = ag.calls[1]
    assert kind == "seller.respond"
    assert sit["unit"]["unit"] == "u1" and sit["ask"] == 280 and sit["round"] == 1
    assert (
        sit["buyer_offer"] == 200
        and sit["buyer_text"] == "200"
        and sit["previous_offers"] == []
    )

    ok, log = talk(b, "t1", "240")
    assert ag.calls[2][1]["previous_offers"] == [200] and ag.calls[2][1]["ask"] == 250
    assert log[0] == (
        "artifact",
        "receipt",
        {
            "data": {
                "shop": "our-shop",
                "item": "G00001",
                "price": 240,
                "task": "t1",
                "unit": "u1",
                "claim": "A",
                "sig_seller": "SIG",
            }
        },
    )
    assert log[1] == ("complete", "Deal: Oak Bed at 240.")
    assert b.deals == {} and Ledger.seen == ["u1"]
    names = [m[0] for m in metrics]
    assert names == ["open", "accept"] and all(m[1]["a2"] for m in metrics)
    acc = metrics[1][1]
    assert (
        acc["price"] == 240
        and acc["rounds"] == 2
        and acc["below_cost"] is False
        and acc["claim"] == "A"
    )


def test_reject_and_decline(tmp_path, hdir):
    w = _w(tmp_path)
    ag = RecAgent(
        w,
        [
            {"action": "offer", "unit": "u3", "ask": 110},
            {"action": "reject"},
            {"action": "decline", "message": "Not today."},
            {"action": "decline"},
        ],
        hdir,
    )
    metrics = []
    b = make_bridge(w, ag, metrics=metrics)
    _, log = talk(b, "t1", "Pine Chair please")
    assert log == [("input", "Pine Chair (G00002): asking 110. Offer a whole number.")]
    _, log = talk(b, "t1", "5")
    assert log == [("reject", "No deal.")] and b.deals == {}
    _, log = talk(b, "t2", "Oak Bed")
    assert log == [("reject", "Not today.")]
    _, log = talk(b, "t3", "Oak Bed")
    assert log == [("reject", "Sorry, we can't take this order.")]
    assert [m[0] for m in metrics] == ["open", "reject", "declined", "declined"]


def test_invalid_model_unit_is_rule_decline(tmp_path, hdir):
    w = _w(tmp_path)
    ag = RecAgent(w, [{"action": "offer", "unit": "u9", "ask": 10}], hdir)
    b = make_bridge(w, ag)
    _, log = talk(b, "t1", "Oak Bed")
    assert log == [("reject", "Sorry, we can't take this order.")] and b.deals == {}


def test_units_held_by_open_deals_are_excluded(tmp_path, hdir):
    w = _w(tmp_path)
    a1_open = {"t-a1": {"unit": {"unit": "u1"}}}
    ag = RecAgent(
        w, [{"action": "offer", "unit": "u2", "ask": 200}, {"action": "decline"}], hdir
    )
    b = make_bridge(w, ag, a1_open=a1_open)
    talk(b, "t1", "Oak Bed")
    assert [u["unit"] for u in ag.calls[0][1]["units"]] == ["u2"]
    metrics = []
    b.metric = lambda d, o, pol=None: metrics.append((d, o))
    _, log = talk(
        b, "t2", "Oak Bed"
    )  # u1 held by attempt 1, u2 by attempt 2 -> only the chair is left
    assert [u["unit"] for u in ag.calls[1][1]["units"]] == ["u3"]
    a1_open["t-a1x"] = {"unit": {"unit": "u3"}}
    _, log = talk(b, "t3", "Oak Bed")  # nothing left: no model call
    assert len(ag.calls) == 2 and log == [
        ("reject", "Sorry, we have none of that in stock.")
    ]
    assert metrics[-1][0] == "no_stock"


def test_unit_taken_while_model_thinks(tmp_path, hdir):
    w = _w(tmp_path)
    a1_open = {}
    ag = RecAgent(w, [{"action": "offer", "unit": "u1", "ask": 200}], hdir)
    real = ag.decide

    def decide(*a):
        out = real(*a)
        a1_open["t-a1"] = {"unit": {"unit": "u1"}}
        return out

    ag.decide = decide
    b = make_bridge(w, ag, a1_open=a1_open)
    _, log = talk(b, "t1", "Oak Bed")
    assert log == [("reject", "Sorry, we can't take this order.")] and b.deals == {}


def test_eight_round_cap(tmp_path, hdir):
    w = _w(tmp_path)
    ag = RecAgent(
        w,
        [{"action": "offer", "unit": "u1", "ask": 300}]
        + [{"action": "counter", "price": 300 - i} for i in range(1, 9)],
        hdir,
    )
    metrics = []
    b = make_bridge(w, ag, metrics=metrics)
    talk(b, "t1", "Oak Bed")
    for i in range(1, 8):
        _, log = talk(b, "t1", str(100 + i))
        assert log == [("input", f"Counteroffer: {300 - i}.")]
    _, log = talk(b, "t1", "108")
    assert log == [("reject", "No deal.")] and b.deals == {}
    assert [m[0] for m in metrics][-2:] == ["a2_round_cap", "reject"]


def test_counter_message_always_ends_with_price():
    assert sb.with_number("", "Counteroffer:", 250) == "Counteroffer: 250."
    assert sb.with_number("Best I can do is 250, round 3", "Counteroffer:", 250) == (
        "Best I can do is 250, round 3 Counteroffer: 250."
    )
    assert (
        sb.with_number("Counteroffer: 250. Thanks!", "Counteroffer:", 250)
        == "Counteroffer: 250. Thanks!"
    )


def test_open_exception_returns_false_for_attempt1(tmp_path):
    w = _w(tmp_path)

    class Boom:
        def decide(self, *a):
            raise RuntimeError("model died")

    metrics = []
    b = make_bridge(w, Boom(), metrics=metrics)
    ok, log = talk(b, "t1", "Oak Bed")
    assert ok is False and log == [] and b.deals == {}
    assert metrics[0][0] == "a2_bridge_error" and metrics[0][1]["stage"] == "open"


def test_respond_exception_ends_our_deal(tmp_path, hdir):
    w = _w(tmp_path)
    ag = RecAgent(w, [{"action": "offer", "unit": "u1", "ask": 300}], hdir)
    b = make_bridge(w, ag)
    talk(b, "t1", "Oak Bed")
    b.offer_in = lambda t: 1 / 0
    ok, log = talk(b, "t1", "100")
    assert ok and log == [("reject", "No deal.")] and b.deals == {}


def test_wants_flag_and_ownership(tmp_path, hdir):
    w = _w(tmp_path, live=False)
    ag = RecAgent(
        w,
        [
            {"action": "offer", "unit": "u1", "ask": 300},
            {"action": "accept", "price": 300},
        ],
        hdir,
    )
    b = make_bridge(w, ag)
    assert not b.wants("t1", a1_open=False)  # flag off: attempt 1
    (w / "a2/LIVE").write_text("")
    assert b.wants("t1", a1_open=False)  # flag on, new task: attempt 2
    assert not b.wants("t-a1", a1_open=True)  # opened by attempt 1: stays there
    talk(b, "t1", "Oak Bed")
    (w / "a2/LIVE").unlink()
    assert b.wants(
        "t1", a1_open=False
    )  # our own deal finishes with us after the flag goes
    assert not b.wants("t2", a1_open=False)
    _, log = talk(b, "t1", "300")
    assert log[-1] == ("complete", "Deal: Oak Bed at 300.")


def test_hold_expiry_and_cancel_free_units(tmp_path, hdir):
    w = _w(tmp_path)
    now = [1000.0]
    ag = RecAgent(
        w,
        [
            {"action": "offer", "unit": "u1", "ask": 300},
            {"action": "offer", "unit": "u2", "ask": 300},
            {"action": "decline"},
        ],
        hdir,
    )
    b = make_bridge(w, ag, clock=lambda: now[0])
    talk(b, "t1", "Oak Bed")
    now[0] += sb.HOLD_S + 1
    talk(b, "t2", "Oak Bed")
    assert "t1" not in b.deals and [u["unit"] for u in ag.calls[1][1]["units"]] == [
        "u1",
        "u2",
    ]
    sb._BRIDGE = b
    sb.cancel("t2")
    assert b.deals == {}


def test_decide_does_not_block_the_loop(tmp_path):
    w = _w(tmp_path)

    class Slow:
        def decide(self, kind, situation, ctx):
            time.sleep(0.3)
            return {"action": "decline"}, {"route": "model"}

    b = make_bridge(w, Slow())

    async def both():
        logs = [[], []]
        ups = [FakeUpdater(lg) for lg in logs]
        await asyncio.gather(
            *(b.handle(f"t{i}", "Oak Bed", ups[i], lambda t: t) for i in range(2))
        )
        return logs

    t0 = time.monotonic()
    logs = asyncio.run(both())
    assert time.monotonic() - t0 < 0.55 and all(
        lg == [("reject", "Sorry, we can't take this order.")] for lg in logs
    )


def test_bridge_singleton_builds_agent_lazily(tmp_path):
    w = _w(tmp_path)
    b1 = sb.bridge(
        str(w),
        str(w / ".market"),
        sign=None,
        name="x",
        receipt_terms=None,
        offer_in=None,
        opening_text=None,
        metric=None,
        ledger=None,
        policy=None,
        shelf=None,
        a1_held=None,
        data_part=None,
    )
    b2 = sb.bridge(
        "other",
        "other",
        sign=None,
        name="y",
        receipt_terms=None,
        offer_in=None,
        opening_text=None,
        metric=None,
        ledger=None,
        policy=None,
        shelf=None,
        a1_held=None,
        data_part=None,
    )
    assert b1 is b2 and b1._agent is None and b1.secrets == (LOGIN,)
    a = b1.agent()
    assert (
        a is b1.agent()
        and a.name == "seller"
        and a.harness_dir == os.path.join(replay.HARNESS, "seller")
    )
    assert (
        a.traj.path == os.path.join(str(w), "a2/trajectory.jsonl")
        and a.terra.cli == replay.CLI
    )


# --- patched attempt-1 seller (third-party imports stubbed) ---------------------------------------------------------


def _stub(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


class _Any:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, n):
        return _Any()

    def __call__(self, *a, **k):
        return _Any()


def _a2a_stubs(log):
    class TaskUpdater(FakeUpdater):
        def __init__(self, event_queue, task_id, context_id):
            super().__init__(log)

    class Task:
        def __init__(self, tid):
            self.id, self.context_id = tid, "c-" + tid

    class Recorder:
        rows = []

        def __init__(self, hook, policy_hash=None):
            pass

        def write(self, decision, seq, outcome=None):
            Recorder.rows.append((decision, outcome))

    class LedgerStub(Ledger):
        def last_seq(self):
            return 0

    P = types.SimpleNamespace(
        receipt_terms=lambda u, pol, stats=None, price=None, now=None, shelf_hours=None: {
            "unit": u["unit"]
        },
        respond=lambda *a, **k: ("counter", 150),
        guard_price=lambda u, p: p,
        floor_price=lambda *a, **k: 1,
        ask_price=lambda *a, **k: 999,
        hours_left=lambda u, now=None: None,
    )
    mods = {
        "common": _stub(
            "common",
            volume_level=lambda *a: (0.0, 0),
            open_resident=lambda *a: (None, "x"),
            P=P,
            Recorder=Recorder,
            Ledger=LedgerStub,
            hook_event=None,
            read_json=lambda p, d=None: (
                json.loads(open(p).read()) if os.path.exists(p) else d
            ),
        ),
        "buyer_log": _stub(
            "buyer_log",
            write=lambda *a, **k: None,
            buyer_ref=lambda c: ("b", "s"),
            clean=lambda t, n=80: t[:n],
            offer_fields=lambda t: {},
        ),
        "uvicorn": _stub("uvicorn"),
        "cryptography": _stub("cryptography"),
        "cryptography.hazmat": _stub("cryptography.hazmat"),
        "cryptography.hazmat.primitives": _stub(
            "cryptography.hazmat.primitives", serialization=_Any()
        ),
        "starlette": _stub("starlette"),
        "starlette.applications": _stub("starlette.applications", Starlette=_Any),
        "a2a": _stub("a2a"),
        "a2a.helpers": _stub(
            "a2a.helpers",
            get_message_text=lambda m: m,
            new_data_part=lambda d: {"data": d},
            new_task_from_user_message=lambda m: Task("new"),
            new_text_part=lambda t: t,
        ),
        "a2a.server": _stub("a2a.server"),
        "a2a.server.agent_execution": _stub(
            "a2a.server.agent_execution", AgentExecutor=object
        ),
        "a2a.server.request_handlers": _stub(
            "a2a.server.request_handlers", DefaultRequestHandler=_Any
        ),
        "a2a.server.routes": _stub(
            "a2a.server.routes",
            create_agent_card_routes=_Any,
            create_jsonrpc_routes=_Any,
        ),
        "a2a.server.tasks": _stub(
            "a2a.server.tasks", InMemoryTaskStore=_Any, TaskUpdater=TaskUpdater
        ),
        "a2a.types": _stub(
            "a2a.types",
            AgentCapabilities=_Any,
            AgentCard=_Any,
            AgentInterface=_Any,
            AgentSkill=_Any,
        ),
        "llm": None,  # attempt 1's LLM assist import fails -> LIVE = None
    }
    return mods, Task, Recorder


@pytest.fixture
def seller(tmp_path, monkeypatch):
    """Attempt 1's seller.py copied into W/hooks, patched by live/seller_patch.py, imported with stubs, cwd = W."""
    from live import seller_patch

    w = _w(tmp_path)
    (w / "hooks").mkdir()
    path = w / "hooks/seller.py"
    shutil.copy(os.path.join(R3_HOOKS, "seller.py"), path)
    assert seller_patch.main([str(path), "--code", HERE]) == 0
    log = []
    mods, Task, Recorder = _a2a_stubs(log)
    Recorder.rows = []
    for k, v in mods.items():
        monkeypatch.setitem(sys.modules, k, v)
    monkeypatch.chdir(w)
    monkeypatch.delenv("MARKET_DIR", raising=False)
    spec = importlib.util.spec_from_file_location("a1_seller_patched", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.A2 is sb
    monkeypatch.setattr(mod, "sign", lambda r: dict(r, sig_seller="SIG"))
    a1_finds = []

    def find_unit(*a, **k):
        a1_finds.append(a[0])
        return None

    monkeypatch.setattr(mod, "find_unit", find_unit)
    s = mod.Seller(pol={"_hash": "h1", "shop": {}})

    def send(text, tid=None):
        log.clear()
        ctx = types.SimpleNamespace(
            current_task=Task(tid) if tid else None, message=text
        )
        q = types.SimpleNamespace(enqueue_event=lambda e: asyncio.sleep(0))
        asyncio.run(s._execute(ctx, q))
        return list(log)

    return types.SimpleNamespace(
        w=w,
        mod=mod,
        s=s,
        send=send,
        a1_finds=a1_finds,
        rows=Recorder.rows,
        cancel=lambda tid: asyncio.run(
            s.cancel(types.SimpleNamespace(current_task=Task(tid)), None)
        ),
    )


def _fake_agent(monkeypatch, answers):
    calls = []

    class FA:
        def decide(self, kind, situation, ctx):
            calls.append((kind, situation))
            a = answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a, {"route": "model"}

    fa = FA()
    monkeypatch.setattr(sb.Bridge, "agent", lambda self: fa)
    return calls


def test_patched_seller_switch_off_is_attempt1(seller, monkeypatch):
    (seller.w / "a2/LIVE").unlink()
    calls = _fake_agent(monkeypatch, [])
    log = seller.send("I want to buy Oak Bed (G00001)")
    assert log == [("reject", "Sorry, we have none of that in stock.")]
    assert seller.a1_finds == ["I want to buy Oak Bed (G00001)"] and calls == []
    assert [r[0] for r in seller.rows] == ["no_stock"]


def test_patched_seller_switch_on_full_deal(seller, monkeypatch):
    calls = _fake_agent(
        monkeypatch,
        [
            {"action": "offer", "unit": "u1", "ask": 280},
            {"action": "accept", "price": 260},
        ],
    )
    log = seller.send("I want to buy Oak Bed (G00001)")
    assert log == [
        (
            "input",
            "Oak Bed (G00001), grade not stated: asking 280. Offer a whole number.",
        )
    ]
    assert seller.a1_finds == [] and "new" in sb._BRIDGE.deals and seller.s.open == {}
    log = seller.send("260", "new")
    assert log[0] == (
        "artifact",
        "receipt",
        {
            "data": {
                "shop": seller.mod.NAME,
                "item": "G00001",
                "price": 260,
                "task": "new",
                "unit": "u1",
                "sig_seller": "SIG",
            }
        },
    )
    assert log[1] == ("complete", "Deal: Oak Bed at 260.")
    assert [k for k, _ in calls] == ["seller.open", "seller.respond"]
    assert [r[0] for r in seller.rows] == ["open", "accept"] and all(
        r[1]["a2"] for r in seller.rows
    )


def test_patched_seller_attempt1_deal_stays_on_attempt1(seller, monkeypatch):
    calls = _fake_agent(monkeypatch, [])
    u = INV["units"][0]
    seller.s.open["t-a1"] = {
        "unit": u,
        "ask": 280,
        "rounds": 0,
        "t": time.time(),
        "inv": INV,
        "sh": None,
        "relax": 0.0,
        "stats": None,
        "pol": seller.s.pol,
    }
    log = seller.send("200", "t-a1")
    assert (
        log == [("input", "Counteroffer: 150.")]
        and calls == []
        and seller.s.open["t-a1"]["rounds"] == 1
    )


def test_patched_seller_bridge_error_falls_back(seller, monkeypatch):
    calls = _fake_agent(monkeypatch, [RuntimeError("model died")])
    log = seller.send("I want to buy Oak Bed (G00001)")
    assert len(calls) == 1 and seller.a1_finds == ["I want to buy Oak Bed (G00001)"]
    assert log == [("reject", "Sorry, we have none of that in stock.")]
    assert [r[0] for r in seller.rows] == ["a2_bridge_error", "no_stock"]


def test_patched_seller_cancel_frees_a2_deal(seller, monkeypatch):
    _fake_agent(monkeypatch, [{"action": "offer", "unit": "u1", "ask": 280}])
    seller.send("I want to buy Oak Bed (G00001)")
    assert "new" in sb._BRIDGE.deals
    seller.cancel("new")
    assert sb._BRIDGE.deals == {}


# --- patch scripts --------------------------------------------------------------------------------------------------


def test_seller_patch_idempotent_check_and_backup(tmp_path, capsys):
    from live import seller_patch

    p = tmp_path / "seller.py"
    shutil.copy(os.path.join(R3_HOOKS, "seller.py"), p)
    orig = p.read_text()
    assert seller_patch.main([str(p), "--code", "~/r3a2-code", "--check"]) == 0
    assert "would apply" in capsys.readouterr().out and p.read_text() == orig
    assert seller_patch.main([str(p), "--code", "~/r3a2-code"]) == 0
    new = p.read_text()
    assert (tmp_path / "seller.py.bak-a2").read_text() == orig
    assert new.count("# r3a2") == 3 and os.path.expanduser("~/r3a2-code") in new
    py_compile.compile(str(p), doraise=True)
    capsys.readouterr()
    assert seller_patch.main([str(p), "--code", "~/r3a2-code"]) == 0
    assert capsys.readouterr().out.strip() == "already patched" and p.read_text() == new
    # the delegation sits after attempt 1's expiry loop and before it reads its own deal
    i = new.index("_a2.wants(")
    assert (
        new.index("for tid in [k for k, d in self.open.items()")
        < i
        < new.index("deal = self.open.get(task.id)")
    )
    with pytest.raises(AssertionError):
        seller_patch.patched(
            orig.replace("        _had = self.open.pop(task.id, None)\n", ""), "/x"
        )


def _hooks_w(tmp_path, live):
    """W with the two hooks patched and attempt 1's own modules stubbed: their main() prints {"a1": true}."""
    from live import hook_patch

    w = _w(tmp_path, live=live)
    (w / "hooks").mkdir()
    for f in ("market_buyer.py", "restock.py"):
        shutil.copy(os.path.join(R3_HOOKS, f), w / "hooks" / f)
        assert hook_patch.main([str(w / "hooks" / f), "--code", HERE]) == 0
        py_compile.compile(str(w / "hooks" / f), doraise=True)
    anyattr = "def __getattr__(n):\n    return None\n"
    (w / "hooks/common.py").write_text(
        "import json\n"
        + anyattr
        + "class P:\n    @staticmethod\n    def load():\n        print(json.dumps({'a1': True}))\n"
        "        raise SystemExit(0)\n"
    )
    for m in ("ledger", "reputation", "owner", "httpx"):
        (w / "hooks" / f"{m}.py").write_text(anyattr)
    return w


def _run(w, *args):
    env = dict(os.environ, R3A2_MEM_SOCK=str(w / "a2/none.sock"))
    r = subprocess.run(
        [sys.executable, *args],
        cwd=w,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return r.returncode, json.loads(r.stdout.strip().splitlines()[-1]), r.stderr


def test_hook_patch_idempotent_and_refuses_other_files(tmp_path, capsys):
    from live import hook_patch

    for f in ("market_buyer.py", "restock.py"):
        p = tmp_path / f
        shutil.copy(os.path.join(R3_HOOKS, f), p)
        orig = p.read_text()
        assert (
            hook_patch.main([str(p), "--code", "/c", "--check"]) == 0
            and p.read_text() == orig
        )
        assert hook_patch.main([str(p), "--code", "/c"]) == 0
        new = p.read_text()
        assert (tmp_path / (f + ".bak-a2")).read_text() == orig and new.count(
            "# r3a2"
        ) == 1
        capsys.readouterr()
        assert hook_patch.main([str(p), "--code", "/c"]) == 0
        assert (
            capsys.readouterr().out.strip() == "already patched"
            and p.read_text() == new
        )
    (tmp_path / "seller.py").write_text("x = 1\n")
    assert hook_patch.main([str(tmp_path / "seller.py"), "--code", "/c"]) == 2


def test_restock_hook_switch(tmp_path):
    w = _hooks_w(tmp_path, live=False)
    rc, last, _ = _run(w, "hooks/restock.py", "--state", ".market")
    assert rc == 0 and last == {"a1": True}
    (w / "a2/LIVE").write_text("")
    inv = dict(INV, balance=1000, bankrupt_line=0, restock={"open": True})
    (w / ".market/inventory.json").write_text(json.dumps(inv))
    (w / "a2/restock_plan.json").write_text(
        json.dumps(
            {"buy": [{"sku": "G00002", "qty": 3}], "valid_until": time.time() + 600}
        )
    )
    rc, last, _ = _run(w, "hooks/restock.py", "--state", ".market")
    assert rc == 0 and last == {"buy": [{"sku": "G00002", "qty": 3}]}
    rows = [json.loads(x) for x in open(w / "a2/trajectory.jsonl")]
    assert rows[-1]["kind"] == "restock" and rows[-1]["why"] == "guarded"


@pytest.mark.skipif(
    os.path.exists(replay.CLI),
    reason="the real model CLI is here; this test must not call it",
)
def test_market_buyer_hook_switch(tmp_path):
    w = _hooks_w(tmp_path, live=False)
    (w / ".market/shops.json").write_text("[]")
    (w / ".market/order.json").write_text(
        json.dumps({"order": "o1", "item": "G00001", "budget": 100})
    )
    args = (
        "hooks/market_buyer.py",
        "--shops",
        ".market/shops.json",
        "--item",
        "Oak Bed",
        "--budget",
        "100",
        "--order",
        ".market/order.json",
    )
    rc, last, _ = _run(w, *args)
    assert rc == 0 and last == {"a1": True}
    (w / "a2/LIVE").write_text("")
    rc, last, _ = _run(
        w, *args
    )  # no CLI here -> model error -> SAFE empty plan -> the kit's no-deal line
    assert rc == 0 and last == {
        "shop": "",
        "receipt": None,
        "stars": 3,
        "review": "no deal",
    }
    kinds = [json.loads(x)["kind"] for x in open(w / "a2/trajectory.jsonl")]
    assert "decision" in kinds and kinds[-1] == "buyer_exec"


def test_expired_units_are_not_candidates(tmp_path):
    from kernel import seller_bridge as sb

    w = _w(tmp_path)
    inv = json.loads((w / ".market/inventory.json").read_text())
    inv["units"][0]["grade"] = "expired"
    inv["units"][1]["expires_at"] = 1.0
    (w / ".market/inventory.json").write_text(json.dumps(inv))
    units, _ = sb.candidate_units(str(w / ".market"), "Oak Bed")
    got = {u["unit"] for u in units}
    assert inv["units"][0]["unit"] not in got and inv["units"][1]["unit"] not in got


def test_accept_above_buyer_offer_becomes_counter(tmp_path, hdir):
    w = _w(tmp_path)
    ag = RecAgent(
        w,
        [{"action": "offer", "unit": "u1", "ask": 280}, {"action": "accept", "price": 270}],
        hdir,
    )
    b = make_bridge(w, ag, metrics=[])
    talk(b, "t1", "I want to buy Oak Bed (G00001)")
    ok, log = talk(b, "t1", "200")
    assert log == [("input", "Counteroffer: 270.")]
