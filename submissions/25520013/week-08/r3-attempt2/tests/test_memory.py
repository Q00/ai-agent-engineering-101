"""Step 3: mem0 client and daemon protocol, memory in agent prompts, memfeed, memcheck, briefing section.

No mem0 here: the daemon's MemService runs on a fake memory with the same add/search shape.
"""

import json
import os
import shutil
import socket
import stat
import tempfile
import threading
import time

import pytest
from kernel import agent as ag
from kernel import brief, memcheck, memfeed, memory
from kernel.trajectory import Trajectory
from memd import server as md

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGIN = "secret-login"


class FakeMem:
    def __init__(self, results=()):
        self.results, self.adds, self.searches = list(results), [], []

    def search(self, query, filters=None, top_k=5):
        self.searches.append((query, filters, top_k))
        return {"results": [{"memory": m, "score": 0.9} for m in self.results][:top_k]}

    def add(self, text, agent_id=None, infer=True, metadata=None):
        self.adds.append((text, agent_id, infer, metadata))
        return {"results": [{"memory": f"fact from {LOGIN}", "event": "ADD"}]}


@pytest.fixture
def short_dir():
    d = tempfile.mkdtemp(
        prefix="r3m", dir="/tmp"
    )  # AF_UNIX paths are capped near 104 bytes on macOS
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def daemon(short_dir, tmp_path):
    def start(results=()):
        mem = FakeMem(results)
        traj = Trajectory(str(tmp_path / "traj.jsonl"), secrets=(LOGIN,))
        svc = md.MemService(mem, traj, (LOGIN,))
        sock = os.path.join(short_dir, "m.sock")
        srv = md.Server(sock, svc)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        started.append(srv)
        return sock, svc, mem, traj

    started = []
    yield start
    for s in started:
        s.shutdown()
        s.server_close()


# ---------------------------------------------------------------- client and daemon
def test_client_missing_socket_is_fast_and_quiet(short_dir):
    sock = os.path.join(short_dir, "none.sock")
    t0 = time.monotonic()
    assert memory.search(sock, "seller", "q", 5) == []
    assert memory.add(sock, "seller", "x") is False
    assert memory.stats(sock) is None
    assert memory.search(None, "seller", "q", 5) == []
    assert time.monotonic() - t0 < 0.5
    assert memory.bound_top_k(99) == 20 and memory.bound_top_k(True) == 0
    assert memory.bound_top_k("5") == 0 and memory.bound_top_k(-3) == 0


def test_client_times_out_on_silent_server(short_dir):
    sock = os.path.join(short_dir, "silent.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock)
    srv.listen(1)
    try:
        t0 = time.monotonic()
        assert memory.search(sock, "seller", "q", 5, timeout=0.3) == []
        assert time.monotonic() - t0 < 1.0
    finally:
        srv.close()


def test_round_trip_through_daemon(daemon):
    sock, svc, mem, traj = daemon(["우리 가게 BED1 정산 900000", f"mail {LOGIN}"])
    assert stat.S_IMODE(os.stat(sock).st_mode) == 0o600
    got = memory.search(sock, "seller", "seller.open BED1 beds", 1)
    assert got == ["우리 가게 BED1 정산 900000"]
    assert mem.searches[-1] == ("seller.open BED1 beds", {"agent_id": "seller"}, 1)
    assert LOGIN not in json.dumps(
        memory.search(sock, "seller", "q", 5), ensure_ascii=False
    )
    assert memory.search(sock, "intruder", "q", 5) == []

    assert memory.add(
        sock, "buyer", f"구매 결과 {LOGIN}", infer=True, metadata={"n": 1}
    )
    assert mem.adds == []  # queued, not done on the request path
    st = memory.stats(sock)
    assert st["queue"] == 1 and st["counts"]["add"] == 1 and st["counts"]["search"] == 2
    assert svc.work_one(block=False)
    assert not svc.work_one(block=False)
    text, agent_id, infer, meta = mem.adds[0]
    assert (
        agent_id == "buyer" and infer is True and meta == {"n": 1} and LOGIN not in text
    )

    rows = [r for r in traj.since(0) if r["kind"] == "memory"]
    assert {r["op"] for r in rows} == {"search", "add"}
    dump = json.dumps(rows, ensure_ascii=False)
    assert (
        "seller.open BED1" not in dump and "구매 결과" not in dump and LOGIN not in dump
    )
    assert all("latency_s" in r for r in rows)
    add_row = next(r for r in rows if r["op"] == "add")
    assert add_row["n"] == 1 and add_row["agent"] == "buyer"


def test_daemon_rejects_bad_requests():
    svc = md.MemService(FakeMem(), Trajectory(os.devnull))
    assert not svc.handle([1])["ok"]
    assert not svc.handle({"op": "nope", "agent": "seller"})["ok"]
    assert not svc.handle({"op": "search", "agent": "seller", "query": ""})["ok"]
    assert not svc.handle({"op": "add", "agent": "seller", "text": "  "})["ok"]


# ---------------------------------------------------------------- agent
UNITS = [{"unit": "u1", "sku": "BED1", "cost": 600, "category": "beds"}]
SITUATION = {"request_text": "I want a bed, cheap please", "units": UNITS}


def _agent(tmp_path, sock, top_k=5):
    h = tmp_path / "h"
    if not h.exists():
        shutil.copytree(os.path.join(HERE, "harness/seller"), h)
    (h / "memory.json").write_text(json.dumps({"store": ["decision"], "top_k": top_k}))
    traj = Trajectory(str(tmp_path / "t.jsonl"), secrets=(LOGIN,))
    seen = {}

    class Terra:
        def complete(self, prompt, *, agent, timeout_s, effort="medium"):
            seen["input"] = prompt
            return '{"action": "offer", "unit": "u1", "ask": 900}'

    a = ag.Agent(
        "seller", str(h), Terra(), traj, "/nonexistent", isolate=False, mem_sock=sock
    )
    return a, traj, seen


def test_agent_prompt_has_memories_when_top_k(tmp_path, daemon):
    sock, _, mem, _ = daemon(["BED1 지난 정산 900000", "BED1 요청 거절 1번"])
    a, traj, seen = _agent(tmp_path, sock)
    d, meta = a.decide("seller.open", SITUATION, {"units": UNITS})
    assert d["ask"] == 900 and meta["route"] == "model"
    assert (
        "## 기억 (mem0, 관련도순)\n\n- BED1 지난 정산 900000\n- BED1 요청 거절 1번"
        in seen["input"]
    )
    assert seen["input"].index("## 기억") < seen["input"].index("## 지금 할 결정")
    query, filters, k = mem.searches[-1]
    assert (
        query == "seller.open BED1 beds"
        and filters == {"agent_id": "seller"}
        and k == 5
    )
    row = [r for r in traj.since(0) if r["kind"] == "decision"][-1]
    assert row["n_memories"] == 2 and row["memory_keys"] == ["BED1", "beds"]


def test_agent_top_k_zero_skips_memory(tmp_path, daemon):
    sock, _, mem, _ = daemon(["x"])
    a, traj, seen = _agent(tmp_path, sock, top_k=0)
    a.decide("seller.open", SITUATION, {"units": UNITS})
    assert mem.searches == [] and "## 기억" not in seen["input"]


def test_agent_decides_when_memory_down(tmp_path, short_dir):
    a, traj, seen = _agent(tmp_path, os.path.join(short_dir, "dead.sock"))
    t0 = time.monotonic()
    d, meta = a.decide("seller.open", SITUATION, {"units": UNITS})
    assert time.monotonic() - t0 < 1.5
    assert d["ask"] == 900 and meta["errors"] == [] and "## 기억" not in seen["input"]
    row = [r for r in traj.since(0) if r["kind"] == "decision"][-1]
    assert row["n_memories"] == 0


def test_memory_keys_buyer_hashes_shop_urls():
    ctx = {"shop_ids": {"s1": "https://a.example/a2a"}}
    sit = {
        "order": {"item": "EGG1", "category": "food"},
        "shops": [{"offers": [{"sku": "EGG1"}]}],
    }
    keys = ag.memory_keys(sit, ctx)
    assert keys == ["food", "EGG1", ag.c.hid("https://a.example/a2a")]


# ---------------------------------------------------------------- memfeed
def _feed_root(tmp_path):
    r = tmp_path / "root"
    (r / "a2").mkdir(parents=True)
    (r / ".r3").mkdir()
    (r / "policy.json").write_text(json.dumps({"owner": {"github": LOGIN}}))
    traj = [
        {
            "ts": 1_794_000_100,
            "agent": "buyer",
            "kind": "buyer_exec",
            "event": "attempt",
            "order": "o1",
            "shop": "h1a2b3c",
            "sku": "BED1",
            "max_pay": 900,
            "offers_sent": [600, 800],
            "final_ask": 800,
            "result": "accepted",
        },
        {
            "ts": 1_794_000_101,
            "agent": "buyer",
            "kind": "buyer_exec",
            "event": "outcome",
            "order": "o1",
            "deal": True,
            "shop": "h1a2b3c",
            "price": 800,
            "candidates": 2,
            "tried": 1,
            "stop": None,
        },
        {
            "ts": 1_794_000_102,
            "agent": "seller",
            "kind": "decision",
            "decision_kind": "seller.open",
            "route_used": "model",
            "decision": {"action": "offer", "unit": "u1", "ask": 900, "message": "hi"},
            "errors": [],
            "memory_keys": ["BED1", "beds"],
        },
        {"ts": 1_794_000_103, "agent": "memory", "kind": "llm", "status": "ok"},
    ]
    ev = [
        {
            "ts": 1_794_000_104,
            "type": "settle",
            "src": "market",
            "data": {
                "sku": "BED1",
                "amount": 900000,
                "own_owner": True,
                "owner": LOGIN,
                "buyer": "rival-x",
            },
        },
        {
            "ts": 1_794_000_105,
            "type": "settle",
            "src": "market",
            "data": {
                "sku": "SOFA1",
                "amount": 1,
                "own_owner": False,
                "owner": "rival-a",
            },
        },
    ]
    (r / "a2/trajectory.jsonl").write_text("".join(json.dumps(x) + "\n" for x in traj))
    (r / ".r3/events.jsonl").write_text("".join(json.dumps(x) + "\n" for x in ev))
    return r


def test_memfeed_sentences():
    a, k, s = memfeed.sentence(
        {
            "ts": 1_794_000_100,
            "kind": "buyer_exec",
            "event": "attempt",
            "order": "o2",
            "shop": "h4d5e6f",
            "sku": "EGG1",
            "max_pay": 40,
            "offers_sent": [],
            "result": "error",
            "why": "no endpoint",
        }
    )
    assert (a, k) == ("buyer", "buyer_exec")
    assert (
        "가게 h4d5e6f" in s
        and "SKU EGG1" in s
        and "사유 no endpoint" in s
        and "결과 error" in s
    )
    a, k, s = memfeed.sentence(
        {
            "ts": 1,
            "type": "settle",
            "data": {"sku": "BED1", "amount": 9, "own_owner": True, "buyer": "rival"},
        },
        LOGIN,
    )
    assert a == "seller" and "SKU BED1" in s and "금액 9" in s and "rival" not in s
    assert (
        memfeed.sentence({"type": "settle", "data": {"sku": "X", "own_owner": False}})
        is None
    )
    _, _, s = memfeed.sentence(
        {
            "agent": "buyer",
            "kind": "decision",
            "decision_kind": "buyer.plan",
            "route_used": "safe",
            "decision": {"candidates": []},
            "errors": ["model: ModelRefused: subscription limit"],
        }
    )
    assert (
        "후보 0개" in s and "오류 model ModelRefused" in s and "subscription" not in s
    )
    for bad in ("—", "`"):
        assert bad not in s


def test_memfeed_batches_cursor_and_store_list(tmp_path):
    r = _feed_root(tmp_path)
    h = tmp_path / "h"
    for agent, store in (("seller", ["decision", "settle"]), ("buyer", ["buyer_exec"])):
        (h / agent).mkdir(parents=True)
        (h / agent / "memory.json").write_text(json.dumps({"store": store, "top_k": 5}))
    calls = []

    def add(sock, agent, text, infer=True, metadata=None):
        calls.append((agent, text, infer, metadata))
        return True

    out = memfeed.run_once(str(r), "/x.sock", str(h), now=1_794_000_200, add=add)
    assert out == {"sent": 4, "batches": 2, "ok": True}
    by = {a: t for a, t, _, _ in calls}
    assert all(i is True for _, _, i, _ in calls)
    assert by["buyer"].count("\n") == 1 and "가게 h1a2b3c" in by["buyer"]
    assert (
        "SKU BED1" in by["seller"]
        and "금액 900000" in by["seller"]
        and "요구가 900" in by["seller"]
    )
    dump = json.dumps(calls, ensure_ascii=False)
    assert (
        LOGIN not in dump
        and "rival-x" not in dump
        and "SOFA1" not in dump
        and "hi" not in by["seller"]
    )

    calls.clear()
    assert (
        memfeed.run_once(str(r), "/x.sock", str(h), now=1_794_000_300, add=add)["sent"]
        == 0
    )
    assert calls == []

    with open(r / "a2/trajectory.jsonl", "a") as f:
        f.write(
            json.dumps(
                {
                    "ts": 1_794_000_400,
                    "agent": "buyer",
                    "kind": "buyer_exec",
                    "event": "outcome",
                    "order": "o3",
                    "deal": False,
                    "candidates": 1,
                    "tried": 1,
                }
            )
            + "\n"
        )
        f.write(
            '{"ts": 1, "kind": "buyer_exec", "par'
        )  # half-written line waits for the next run
    fail = memfeed.run_once(str(r), "/x.sock", str(h), add=lambda *a, **k: False)
    assert fail["ok"] is False
    out = memfeed.run_once(str(r), "/x.sock", str(h), add=add)
    assert out["sent"] == 1 and "거래 없음" in calls[0][1]


def test_memfeed_first_run_skips_old_rows(tmp_path):
    r = _feed_root(tmp_path)
    out = memfeed.run_once(
        str(r), "/x.sock", now=1_794_000_103 + 3600 * 30, add=lambda *a, **k: True
    )
    assert out["sent"] == 0 and out["ok"]


# ---------------------------------------------------------------- memcheck
def _oracle(q):
    return {
        "static": "700000",
        "dynamic": "950000",
        "workflow": "3",
        "gotcha": "no endpoint",
        "premise": "모름",
    }[q["ability"]]


def test_memcheck_questions_cover_five_abilities():
    qs = memcheck.build_questions_for_test()
    assert {q["ability"] for q in qs} == set(memcheck.ABILITIES)
    assert all(q["ability"] in memcheck.ABILITIES and q["question"] for q in qs)
    by = {q["ability"]: q for q in qs}
    assert by["static"]["truth"] == 700000 and "SOFA1" in by["static"]["question"]
    assert by["dynamic"]["truth"] == 950000 and "BED1" in by["dynamic"]["question"]
    assert by["workflow"]["truth"] == 3 and by["gotcha"]["truth"] == "no endpoint"
    assert "LAMP1" not in json.dumps(qs, ensure_ascii=False)
    fake = by["premise"]["question"].split("SKU ")[1].split("를")[0]
    assert fake not in json.dumps([memcheck.FIXTURE_TRAJ, memcheck.FIXTURE_SETTLE])


def test_memcheck_scoring_with_fakes(tmp_path):
    qs = memcheck.build_questions_for_test()
    asked = []

    def ask_memory(agent, query, k):
        asked.append((agent, query, k))
        return ["기억 하나"]

    current = {}

    def ask_model(prompt):
        q = next(q for q in qs if q["question"] in prompt)
        current["q"] = q
        assert "기억 하나" in prompt
        return json.dumps({"answer": _oracle(q)}, ensure_ascii=False)

    res = memcheck.run(qs, ask_memory, ask_model, top_k=7)
    assert all(res[a]["correct"] == res[a]["n"] >= 1 for a in memcheck.ABILITIES)
    assert all(k == 7 for _, _, k in asked)

    wrong = memcheck.run(qs, ask_memory, lambda p: '{"answer": "123"}')
    assert all(wrong[a]["correct"] == 0 for a in memcheck.ABILITIES)
    q = {"check": "number", "truth": 950000}
    assert (
        memcheck.score(q, "950,000원")
        and memcheck.score(q, "약 951000")
        and not memcheck.score(q, "모름")
    )
    assert not memcheck.score({"check": "abstain", "truth": "모름"}, "700000")

    root = tmp_path / "root"
    path = memcheck.write(str(root), res, 3, 7, now=1_794_000_000)
    doc = json.loads(open(path, encoding="utf-8").read())
    assert doc["abilities"]["premise"]["correct"] == 3 and doc["top_k"] == 7


# ---------------------------------------------------------------- briefing
def test_section_memory(tmp_path, monkeypatch, daemon):
    monkeypatch.delenv("R3A2_MEM_SOCK", raising=False)
    root = tmp_path / "root"
    (root / "a2").mkdir(parents=True)
    assert brief.section_memory(str(root)) == [
        "[기억] 점검 결과 없음, 기억 서버 응답 없음"
    ]

    res = {a: {"n": 3, "correct": 2, "examples": []} for a in memcheck.ABILITIES}
    memcheck.write(str(root), res, 3, 10, now=1_794_000_000)
    lines = brief.section_memory(str(root))
    assert lines[0].startswith("[기억 점검 LME-V2 ") and "premise 2/3" in lines[0]
    assert lines[1] == "[기억 서버] 응답 없음"

    sock, *_ = daemon()
    monkeypatch.setenv("R3A2_MEM_SOCK", sock)
    lines = brief.section_memory(str(root))
    assert lines[1].startswith("[기억 서버] 검색 0번")
    assert "[기억 점검 LME-V2" in brief.build(str(root), now=1_794_000_000)


def test_settle_side_splits_our_sales_from_our_purchases():
    from kernel import common as c

    assert c.settle_side({"own_owner": True, "buyer": "rival"}, "me") == "shop"
    assert c.settle_side({"own_owner": True, "buyer": "Me"}, "me") == "buyer"
    assert c.settle_side({"own_owner": False, "buyer": "me"}, "me") is None
    a, k, s = memfeed.sentence({"type": "settle", "ts": 0, "data": {"sku": "X", "amount": 5, "own_owner": True, "buyer": "me", "shop": "rival-shop"}}, "me")
    assert a == "buyer" and "구매" in s and "rival-shop" not in s
    a, k, s = memfeed.sentence({"type": "settle", "ts": 0, "data": {"sku": "X", "amount": 5, "own_owner": True, "buyer": "rival"}}, "me")
    assert a == "seller" and "판매" in s


def test_client_survives_deeply_nested_reply():
    d = tempfile.mkdtemp(dir="/tmp")
    path = os.path.join(d, "s.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        conn.recv(65536)
        conn.sendall(b"[" * 100000 + b"]" * 100000 + b"\n")
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    try:
        assert memory.search(path, "seller", "q", 3) == []
    finally:
        srv.close()
        shutil.rmtree(d, ignore_errors=True)
