"""v1.20 buyer_log: did-not-buy logging is LOGGING ONLY.
- unit tests of hooks/buyer_log.py (schema, append-only, visits, never raises)
- the seller writes request/quote/offer/outcome rows (accept, reject, no_stock, no_response, cancel)
- decisions are identical with the log on, off (R3_BUYER_LOG=0) and broken (unwritable path), fixed seeds
No network, no kit: the A2A updater and signing are replaced by recorders."""
import asyncio
import importlib.util
import json
import random
import sys
import types
from pathlib import Path

import pytest

R3 = Path(__file__).resolve().parents[1]
for p in (str(R3 / "hooks"), str(R3)):
    if p not in sys.path:
        sys.path.insert(0, p)

import buyer_log as BL  # noqa: E402

INV = {"units": [
    {"unit": f"u{i}", "sku": f"G0000{i % 4 + 1}", "name": f"item{i % 4 + 1}", "category": "a/b/goods",
     "kind": "goods", "grade": "AB"[i % 2], "cost": 1000 + 250 * (i % 4), "supplied_at": i}
    for i in range(10)]}


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def time(self):
        return self.t

    def sleep(self, s):
        pass


def load_seller(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def patch(m, trace, clock):
    class Up:
        def __init__(self, event_queue, task_id, context_id):
            self.tid = task_id

        def new_agent_message(self, parts):
            return parts

        async def requires_input(self, msg):
            trace.append(("requires_input", self.tid, msg))

        async def reject(self, msg):
            trace.append(("reject", self.tid, msg))

        async def complete(self, msg):
            trace.append(("complete", self.tid, msg))

        async def add_artifact(self, parts, name):
            trace.append(("artifact", self.tid, json.dumps(parts, sort_keys=True, default=str)))

        async def cancel(self):
            trace.append(("cancel", self.tid))

    class FakeLedger:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def first_seen_unit(self, u):
            pass

        def last_seq(self):
            return 0

    m.TaskUpdater = Up
    m.get_message_text = lambda msg: msg.text
    m.new_text_part = lambda t: t
    m.new_data_part = lambda d: d
    m.new_task_from_user_message = lambda msg: types.SimpleNamespace(id=msg.tid, context_id=msg.cid)
    m.sign = lambda r: dict(r, sig_seller="x")
    m.Ledger = FakeLedger
    m.open_resident = lambda cat: (None, "stub")
    m.volume_level = lambda side, pol: (0, 0)
    m.read_json = lambda p, d=None: json.loads(json.dumps(INV)) if str(p).endswith("inventory.json") else d
    m.time = clock
    m.TICK_S = 0
    m.Seller._metric = lambda self, decision, outcome, pol=None: trace.append(
        ("metric", decision, json.dumps(outcome, sort_keys=True, default=str)))


def scenario(seed, n=60):
    rnd = random.Random(seed)
    ev, live, k = [], [], 0
    for _ in range(n):
        r = rnd.random()
        if r < 0.3 or not live:
            k += 1
            tid = f"t{seed}_{k}"
            live.append(tid)
            sku = rnd.choice(["G00001", "G00002", "G00003", "G00004", "G09999", "item2", "nothing"])
            ev.append(("msg", tid, f"buyer{rnd.randint(0, 3)}", f"I want {sku}"))
        elif r < 0.85:
            ev.append(("msg", rnd.choice(live), None, str(rnd.randint(500, 4000))))
        elif r < 0.92:
            ev.append(("cancel", rnd.choice(live), None, None))
        else:
            ev.append(("sleep", None, None, rnd.choice([5, 60, 130])))
    return ev


def run(m, seed, pol):
    trace, clock = [], Clock()
    patch(m, trace, clock)
    s = m.Seller(pol=pol)
    cids = {}

    class Q:
        async def enqueue_event(self, e):
            trace.append(("task", e.id))
    q = Q()

    async def go():
        for kind, tid, buyer, x in scenario(seed):
            clock.t += 1
            if kind == "sleep":
                clock.t += x
                continue
            if buyer:
                cids[tid] = buyer
            msg = types.SimpleNamespace(text=x or "", tid=tid, cid=cids.get(tid, "c"), metadata={"buyer": cids.get(tid)})
            cur = types.SimpleNamespace(id=tid, context_id=cids.get(tid, "c")) if tid in s.open else None
            ctx = types.SimpleNamespace(current_task=cur, message=msg, context_id=cids.get(tid, "c"),
                                        call_context=None)
            if kind == "cancel":
                ctx.current_task = types.SimpleNamespace(id=tid, context_id="c")
                await s.cancel(ctx, q)
            else:
                await s._execute(ctx, q)
    asyncio.run(go())
    return trace


@pytest.fixture
def pol():
    import policy as P
    return P.load(R3 / "policy.json")


@pytest.fixture
def seller_mod():
    return load_seller(R3 / "hooks" / "seller.py", "seller_v120log_under_test")


def rows(p):
    return [json.loads(x) for x in Path(p).read_text().splitlines()] if Path(p).exists() else []


# ---- buyer_log unit tests
def test_write_schema_and_append_only(tmp_path, monkeypatch):
    p = tmp_path / "logs" / "b.jsonl"
    monkeypatch.setenv("R3_BUYER_LOG", str(p))
    BL._VISITS.clear()
    BL.write("request", "b1", "metadata.buyer", "t1", text="hi")
    first = p.read_bytes()
    BL.write("outcome", "b1", "metadata.buyer", "t1", outcome="no_stock")
    assert p.read_bytes().startswith(first)                  # append only
    r = rows(p)
    assert [x["event"] for x in r] == ["request", "outcome"]
    for x in r:
        assert {"v", "ts", "event", "buyer", "buyer_src", "task"} <= set(x) and x["v"] == BL.SCHEMA


def test_repeat_visits_counted_per_buyer(tmp_path, monkeypatch):
    p = tmp_path / "b.jsonl"
    monkeypatch.setenv("R3_BUYER_LOG", str(p))
    BL._VISITS.clear()
    for b in ("x", "y", "x", "x"):
        BL.write("request", b, "s", "t")
    r = rows(p)
    assert [(x["buyer"], x["visit_n"], x["repeat"]) for x in r] == [("x", 1, False), ("y", 1, False),
                                                                    ("x", 2, True), ("x", 3, True)]


def test_off_and_broken_never_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("R3_BUYER_LOG", "0")
    assert BL.write("request", "b", "s", "t") is None
    (tmp_path / "f").write_text("file")
    monkeypatch.setenv("R3_BUYER_LOG", str(tmp_path / "f" / "under_a_file.jsonl"))
    assert BL.write("request", "b", "s", "t") is None


def test_buyer_ref_order():
    md = types.SimpleNamespace(message=types.SimpleNamespace(metadata={"buyer": "B7"}), context_id="c1")
    assert BL.buyer_ref(md) == ("B7", "metadata.buyer")
    cc = types.SimpleNamespace(message=types.SimpleNamespace(metadata=None), context_id="c1",
                               call_context=types.SimpleNamespace(user=types.SimpleNamespace(user_name="u9",
                                                                                            is_authenticated=True)))
    assert BL.buyer_ref(cc) == ("u9", "call_context.user")
    assert BL.buyer_ref(types.SimpleNamespace(message=None, context_id="c2")) == ("ctx:c2", "context_id")
    assert BL.buyer_ref(object()) == ("unknown", "none")


# ---- seller wiring
def test_seller_logs_every_outcome_kind(tmp_path, monkeypatch, seller_mod, pol):
    p = tmp_path / "b.jsonl"
    monkeypatch.setenv("R3_BUYER_LOG", str(p))
    BL._VISITS.clear()
    for seed in range(30):
        run(seller_mod, seed, pol)
    r = rows(p)
    ev = {x["event"] for x in r}
    outs = {x.get("outcome") for x in r if x["event"] == "outcome"}
    assert {"request", "quote", "offer", "outcome"} <= ev
    assert {"accept", "no_stock", "no_response", "cancel"} <= outs, outs
    assert any(x.get("repeat") for x in r if x["event"] == "request")
    assert all(x["buyer"].startswith("buyer") for x in r if x["event"] != "outcome" or x.get("outcome") != "cancel")
    q = [x for x in r if x["event"] == "quote"]
    assert {x["kind"] for x in q} <= {"open", "counter"} and all(isinstance(x["ask"], int) for x in q)


@pytest.mark.parametrize("seed", range(40))
def test_decisions_identical_log_on_off_broken(tmp_path, monkeypatch, pol, seed):
    traces = []
    for mode in ("on", "off", "broken"):
        if mode == "on":
            monkeypatch.setenv("R3_BUYER_LOG", str(tmp_path / f"on{seed}.jsonl"))
        elif mode == "off":
            monkeypatch.setenv("R3_BUYER_LOG", "0")
        else:
            (tmp_path / "blk").write_text("x")
            monkeypatch.setenv("R3_BUYER_LOG", str(tmp_path / "blk" / "x.jsonl"))
        m = load_seller(R3 / "hooks" / "seller.py", f"seller_eq_{mode}_{seed}")
        traces.append(run(m, seed, pol))
    assert traces[0] == traces[1] == traces[2]
    assert any(t[0] in ("complete", "reject", "requires_input") for t in traces[0])


# ---- v1.20 CR round 2 (N2): against the v1.20a+b seller (byte copy tests/_seller_v120ab_ref.py, sha 37d142f3) and
# with the log functions raising: kills "log call deletes the deal" and "exception handling removed" mutants.
OLD = R3 / "tests" / "_seller_v120ab_ref.py"


def _boom(*a, **k):
    raise RuntimeError("injected log failure")


def _v230_off(m):
    """v2.3 skips a unit just sold and units refused at checkout; this stub inventory never drops a sold unit,
    so the v1.20ab equivalence is checked with both off (v2.3 has its own tests in test_v230_held_units.py)."""
    m.ACCEPT_HOLD_S = -1
    m.KIT_LOG = R3 / "tests" / "no-such-kit.log"
    return m


@pytest.mark.parametrize("seed", range(40))
def test_decisions_identical_to_v120ab_seller(tmp_path, monkeypatch, pol, seed):
    monkeypatch.setenv("R3_BUYER_LOG", str(tmp_path / "b.jsonl"))
    old = run(load_seller(OLD, f"seller_ref_{seed}"), seed, pol)
    new = run(_v230_off(load_seller(R3 / "hooks" / "seller.py", f"seller_new_{seed}")), seed, pol)
    assert old == new
    assert rows(tmp_path / "b.jsonl")


@pytest.mark.parametrize("what", ["write", "buyer_ref", "both"])
@pytest.mark.parametrize("seed", range(12))
def test_decisions_identical_when_log_raises(tmp_path, monkeypatch, pol, seed, what):
    monkeypatch.setenv("R3_BUYER_LOG", str(tmp_path / "b.jsonl"))
    old = run(load_seller(OLD, f"seller_ref_x_{seed}_{what}"), seed, pol)
    if what in ("write", "both"):
        monkeypatch.setattr(BL, "write", _boom)
    if what in ("buyer_ref", "both"):
        monkeypatch.setattr(BL, "buyer_ref", _boom)
    new = run(_v230_off(load_seller(R3 / "hooks" / "seller.py", f"seller_new_x_{seed}_{what}")), seed, pol)
    assert old == new


def test_size_cap_stops_writing(tmp_path, monkeypatch):
    p = tmp_path / "b.jsonl"
    monkeypatch.setenv("R3_BUYER_LOG", str(p))
    monkeypatch.setattr(BL, "MAX_BYTES", 300)
    BL._VISITS.clear()
    n = sum(BL.write("request", "b", "s", "t", text="x" * 50) is not None for _ in range(20))
    size = p.stat().st_size
    assert 0 < n < 20 and size < 300 + 400                      # stops after crossing the cap, never truncates
    assert BL.write("request", "b", "s", "t") is None and p.stat().st_size == size
    rows = [json.loads(x) for x in p.read_text().splitlines()]
    assert [r["event"] for r in rows].count("cap_reached") == 1 and rows[-1]["event"] == "cap_reached"


@pytest.mark.parametrize("raw", ["abc", "", "-5", "0", "1e9", "50MB", " "])
def test_bad_max_bytes_env_falls_back(monkeypatch, raw):
    monkeypatch.setenv("R3_BUYER_LOG_MAX_BYTES", raw)
    assert BL._max_bytes() == BL.DEFAULT_MAX_BYTES


def test_bad_max_bytes_env_import_does_not_raise(monkeypatch):
    import importlib.util
    monkeypatch.setenv("R3_BUYER_LOG_MAX_BYTES", "not-a-number")
    spec = importlib.util.spec_from_file_location("_bl_badenv", BL.__file__)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.MAX_BYTES == m.DEFAULT_MAX_BYTES
    monkeypatch.setenv("R3_BUYER_LOG_MAX_BYTES", "1234")
    assert m._max_bytes() == 1234


def test_visit_table_is_capped(tmp_path, monkeypatch):
    monkeypatch.setenv("R3_BUYER_LOG", str(tmp_path / "b.jsonl"))
    monkeypatch.setattr(BL, "MAX_VISIT_KEYS", 5)
    BL._VISITS.clear()
    for i in range(12):
        BL.write("request", f"b{i}", "s", "t")
    assert len(BL._VISITS) <= 5


class _Raising:
    def __getattr__(self, k):
        raise RuntimeError("hostile context")


def test_buyer_ref_survives_hostile_context_and_needs_auth():
    assert BL.buyer_ref(_Raising()) == ("unknown", "none")
    un = types.SimpleNamespace(message=types.SimpleNamespace(metadata=None), context_id="c3",
                               call_context=types.SimpleNamespace(user=types.SimpleNamespace(user_name="u9",
                                                                                            is_authenticated=False)))
    assert BL.buyer_ref(un) == ("ctx:c3", "context_id")


def test_seller_row_contents_and_no_leak(tmp_path, monkeypatch, seller_mod, pol):
    p = tmp_path / "b.jsonl"
    monkeypatch.setenv("R3_BUYER_LOG", str(p))
    trace, clock = [], Clock()
    patch(seller_mod, trace, clock)
    s = seller_mod.Seller(pol=pol)

    class Q:
        async def enqueue_event(self, e):
            pass

    def ctx(tid, text, new):
        msg = types.SimpleNamespace(text=text, tid=tid, cid="c", metadata={"buyer": "B1"})
        cur = None if new else types.SimpleNamespace(id=tid, context_id="c")
        return types.SimpleNamespace(current_task=cur, message=msg, context_id="c", call_context=None)

    async def go():
        await s._execute(ctx("t1", "I want G00001 " + "x" * 400, True), Q())
        await s._execute(ctx("t1", "99999", False), Q())          # high offer -> accept
    asyncio.run(go())
    r = rows(p)
    req = [x for x in r if x["event"] == "request"][0]
    assert len(req["text"]) == 200
    q = [x for x in r if x["event"] == "quote" and x["kind"] == "open"][0]
    assert q["policy_hash"] == pol["_hash"]
    assert [x["outcome"] for x in r if x["event"] == "outcome"] == ["accept"]
    assert s._blog == {} and s.open == {}                       # no per-task leak after a closed deal
