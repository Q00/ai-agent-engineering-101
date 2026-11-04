"""icrl lessons as memo-graph evidence (memory.llm_step.lessons_step through memory.run), icrl.loop.recall over the
graph + not-yet-ingested lessons, the LongMemEval-style memcheck on a synthetic history, and icrl never writing
under the memory dir. Stubbed memory LLM, no network."""

import hashlib
import json
import re
import types

import pytest
from harness.runs import PolicyRuns
from icrl import loop
from icrl import space as SP
from memory import run as MR

from tests.test_icrl_loop import APPROVE, ARM, NOW0, R3, Fake, make_work, pick

A1 = "defaults.food.price_mult|up"
P1 = "defaults.food.price_mult"
A2 = "shop.max_spend_per_run|up"
P2 = "shop.max_spend_per_run"
T1, T2, T3 = NOW0, NOW0 + 90000, NOW0 + 100000


def jl(p, rows):
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def end_row(i, arm, verdict, t_end, cur, val):
    path = arm.removeprefix("retest|").split("|")[0]
    return {
        "event": "end",
        "id": f"e{i:04d}",
        "arm": arm,
        "verdict": verdict,
        "t_end": t_end,
        "edits": [{"path": path, "current": cur, "value": val, "dir": "up"}],
    }


def lesson(i, arm, verdict, ts):
    path = arm.removeprefix("retest|").split("|")[0]
    return {
        "ts": ts,
        "exp": f"e{i:04d}",
        "arm": arm,
        "path": path,
        "dir": "up",
        "verdict": verdict,
        "text": f"{arm}: {verdict}",
    }


HISTORY = [  # (end row, lesson row): adopt, then the retest flips it to reject; a second arm keep_a
    (end_row(1, A1, "adopt", T1, 1.0, 1.03), lesson(1, A1, "adopt", T1)),
    (
        end_row(2, "retest|" + A1, "reject", T2, 1.03, 1.0),
        lesson(2, "retest|" + A1, "reject", T2),
    ),
    (end_row(3, A2, "keep_a", T3, 1100000, 1200000), lesson(3, A2, "keep_a", T3)),
]


def add_all(prompt):
    """The stub memory LLM: ADD every lesson row it is shown (verdict + value_after)."""
    rows = json.loads(re.search(r"<<<DATA\n(.*)\nDATA>>>", prompt, re.S).group(1))[
        "lessons"
    ]
    out = []
    for r in rows:
        out.append(
            {
                "subject": r["exp"],
                "predicate": "verdict",
                "value": r["verdict"],
                "evidence": [r["seq"]],
                "op": "ADD",
            }
        )
        out.append(
            {
                "subject": r["policy"],
                "predicate": "value",
                "value": r["value_after"],
                "evidence": [r["seq"]],
                "op": "ADD",
            }
        )
    return json.dumps(out)


class Role:
    calls = []

    def __init__(self, live, role):
        pass

    def available(self):
        return True

    def complete(self, prompt, max_tokens=None):
        Role.calls.append(prompt)
        return {"text": add_all(prompt) if "policy experiments" in prompt else "[]"}


@pytest.fixture
def mem(tmp_path, monkeypatch):
    root, md, isd = tmp_path / "state", tmp_path / "market", tmp_path / "icrl"
    for d in (root, md, isd):
        d.mkdir()
    (md / "catalog.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("R3_STATE", str(root))
    monkeypatch.setenv("ICRL_STATE", str(isd))
    monkeypatch.setenv("R3_MEMORY_LLM", "1")
    import llm.ops_llm
    from memory import llm_step

    Role.calls = []
    monkeypatch.setattr(llm.ops_llm, "RoleLLM", Role)
    monkeypatch.setattr(llm_step, "_live", lambda: object())
    monkeypatch.setattr(MR, "POST_INGEST", [llm_step.step, llm_step.lessons_step])
    return types.SimpleNamespace(root=root, md=md, isd=isd)


def write_history(isd, k):
    jl(isd / "experiments.jsonl", [e for e, _ in HISTORY[:k]])
    jl(isd / "lessons.jsonl", [x for _, x in HISTORY[:k]])


def memo(root, subject, pred):
    g = json.loads((root / "memory" / "graph.json").read_text())
    return g["edges"]["memo"].get(f"{subject}|{pred}")


def ops(root):
    p = root / "memory" / "graph_ops.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_a_lesson_row_becomes_an_evidence_memo(mem):
    write_history(mem.isd, 1)
    MR.run_once(mem.md, now=T1 + 600)
    e = memo(mem.root, "exp:" + A1, "verdict")
    assert e["v"] == [{"from": T1, "to": None, "attrs": {"value": "adopt"}, "ev": [-1]}]
    assert memo(mem.root, "policy:" + P1, "value")["v"][-1]["attrs"] == {
        "value": 1.03
    }  # adopt -> B's value
    assert [r["op"] for r in ops(mem.root)] == ["ADD", "ADD"]
    assert all(
        r["reason"].startswith("proposal:") and not r["overridden"]
        for r in ops(mem.root)
    )
    cur = json.loads((mem.root / "memory" / "cursor.json").read_text())["lessons"]
    assert cur["n"] == 1 and cur["path"] == str((mem.isd / "lessons.jsonl").resolve())
    MR.run_once(mem.md, now=T1 + 1200)  # nothing new: no second lessons call
    assert sum("policy experiments" in p for p in Role.calls) == 1


def test_llm_off_leaves_the_cursor(mem, monkeypatch):
    monkeypatch.delenv("R3_MEMORY_LLM")
    write_history(mem.isd, 1)
    MR.run_once(mem.md, now=T1 + 600)
    assert (
        json.loads((mem.root / "memory" / "cursor.json").read_text())["lessons"] is None
    )
    assert memo(mem.root, "exp:" + A1, "verdict") is None


def test_a_retest_that_flips_the_verdict_is_an_update(mem):
    write_history(mem.isd, 1)
    MR.run_once(mem.md, now=T1 + 600)
    write_history(mem.isd, 2)
    MR.run_once(mem.md, now=T2 + 600)
    e = memo(mem.root, "exp:" + A1, "verdict")
    assert [(v["from"], v["to"], v["attrs"]["value"]) for v in e["v"]] == [
        (T1, T2, "adopt"),
        (T2, None, "reject"),
    ]
    last = [r for r in ops(mem.root) if r["key"] == f"exp:{A1}|verdict"][-1]
    assert (
        last["op"] == "UPDATE" and last["overridden"] and last["evidence"] == [-2]
    )  # the stub said ADD
    pol = memo(mem.root, "policy:" + P1, "value")["v"]
    assert (
        len(pol) == 1 and pol[0]["to"] is None
    )  # reject on retest keeps 1.03: same value, NOOP


def test_ledger_items_cannot_write_lesson_subjects(mem):
    from memory.graph import Graph

    G = Graph()
    r = G.propose(
        {
            "subject": "exp:" + A1,
            "predicate": "verdict",
            "value": "adopt",
            "evidence": [5],
            "op": "ADD",
        },
        lambda s: {"data": {"x": "exp:" + A1}},
        T1,
    )
    assert r["op"] == "NOOP" and "lesson ids" in r["reason"]


def ctx_for(tmp_path, sdir, graph_dir):
    return types.SimpleNamespace(
        P={
            "graph": graph_dir / "graph.json",
            "catalog": tmp_path / "none.json",
            "demand": tmp_path / "none.json",
        },
        f=lambda n: sdir / n,
        space=SP.load(),
        runs=PolicyRuns(tmp_path / "runs", tmp_path / "runs"),
    )


def test_recall_prefers_graph_edges_and_adds_only_uningested_lessons(mem, tmp_path):
    write_history(mem.isd, 2)
    MR.run_once(mem.md, now=T2 + 600)  # both lessons ingested (MAX_LESSONS = 2)
    rows = [x for _, x in HISTORY]
    rows[0] = dict(rows[0], verdict="SHOULD-NOT-SHOW")  # graph wins for ingested rows
    jl(mem.isd / "lessons.jsonl", rows)
    jl(mem.isd / "experiments.jsonl", [e for e, _ in HISTORY])
    ctx = ctx_for(tmp_path, mem.isd, mem.root / "memory")
    rec = loop.recall(ctx, [], [P2])
    assert rec["exp"] == {
        A1: "reject",
        A2: "keep_a",
    }  # A1 from the graph, A2 from the un-ingested lesson
    assert rec["lessons"] == [f"{A2}: keep_a"] and rec["lesson_rows"] == [rows[2]]
    assert {"exp": A1, "verdict": "reject"} in rec[
        "edges"
    ] and "SHOULD-NOT-SHOW" not in json.dumps(rec)
    assert (
        loop.recall(ctx, [], [], at=T1 + 1)["exp"][A1] == "adopt"
    )  # the closed version, at a past time
    assert loop.recall(ctx, [], [], at=T1 + 1)["policy"] == {P1: 1.03}


def test_memcheck_types_on_a_synthetic_history(tmp_path):
    sdir = tmp_path / "icrl"
    sdir.mkdir()
    pol = json.loads((R3 / "policy.json").read_bytes())
    base_p2 = SP.get(pol, P2)[0]
    hist = [(e, x) for e, x in HISTORY]
    hist[2] = (
        dict(
            hist[2][0],
            edits=[
                dict(hist[2][0]["edits"][0], current=base_p2, value=base_p2 + 100000)
            ],
        ),
        hist[2][1],
    )
    hist.append(
        (
            {
                "event": "end",
                "id": "e0004",
                "arm": "defaults.goods.price_mult|up",
                "verdict": "aborted_external_edit",
                "t_end": T3 + 10,
                "edits": [],
            },
            None,
        )
    )
    jl(sdir / "experiments.jsonl", [e for e, _ in hist])
    jl(sdir / "lessons.jsonl", [x for _, x in hist if x])
    ctx = ctx_for(tmp_path, sdir, tmp_path / "nomem")
    SP.put(pol, P1, 1.0)
    ctx.runs.register(json.dumps(pol).encode(), T1 - 3600, "baseline")
    SP.put(pol, P1, 1.03)
    ctx.runs.register(json.dumps(pol).encode(), T1, "change")
    row = loop.memcheck(ctx, {}, T3 + 60)
    assert row["style"] == "LongMemEval-style (Wu et al., ICLR 2025 abilities)"
    t = row["types"]
    assert (
        t["information_extraction"]["n"],
        t["information_extraction"]["correct"],
    ) == (2, 2)
    assert (t["knowledge_update"]["n"], t["knowledge_update"]["correct"]) == (1, 1)
    assert (t["temporal"]["n"], t["temporal"]["correct"]) == (3, 3)
    assert (t["abstention"]["n"], t["abstention"]["correct"]) == (5, 5)
    assert row["n"] == 11 and row["acc"] == 1.0 and row["unread_lessons"] == 3
    assert loop.memcheck(ctx, {"last_memcheck": T3 + 60}, T3 + 120) is None  # hourly
    # the memory loses the retest lesson: extraction and update miss it, the temporal answers still hold
    jl(sdir / "lessons.jsonl", [hist[0][1], hist[2][1]])
    row = loop.memcheck(ctx, {}, T3 + 3700)
    t = row["types"]
    assert t["information_extraction"]["correct"] == 1 and t["knowledge_update"] == {
        "n": 1,
        "correct": 0,
        "acc": 0.0,
    }
    assert (
        t["temporal"]["correct"] == 3
        and ["knowledge_update", f"verdict of {A1} after its retest?"] in row["misses"]
    )
    rows = [json.loads(x) for x in (sdir / "memcheck.jsonl").read_text().splitlines()]
    assert len(rows) == 2


def tree_hash(d):
    h = hashlib.sha256()
    for p in sorted(d.rglob("*")):
        h.update(str(p.relative_to(d)).encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def test_icrl_never_writes_under_the_memory_dir(tmp_path):
    w = make_work(tmp_path)
    md = w / ".r3" / "memory"
    md.mkdir()
    sdir = tmp_path / "st"
    sdir.mkdir()
    (md / "graph.json").write_text(
        json.dumps({"edges": {"memo": {}}}), encoding="utf-8"
    )
    (md / "cursor.json").write_text(
        json.dumps(
            {
                "lessons": {
                    "path": str((sdir / "lessons.jsonl").resolve()),
                    "off": 0,
                    "n": 0,
                }
            }
        ),
        encoding="utf-8",
    )
    jl(sdir / "lessons.jsonl", [HISTORY[0][1]])
    (sdir / "arms.json").write_text(json.dumps({ARM: [50.0, 1e6]}))
    pol = tmp_path / "policy.json"
    pol.write_bytes((R3 / "policy.json").read_bytes())
    ctx = loop.Ctx(
        work=w,
        sdir=sdir,
        policy=pol,
        llms={"prop": Fake([pick]), "appr": Fake([APPROVE])},
        fetch=lambda u: (500, b""),
        audit=tmp_path / "audit.jsonl",
    )
    before = tree_hash(md)
    for k in range(4):
        r = loop.step(NOW0 + k * 1800, ctx=ctx)
        assert r["status"] == "ok", r
    assert tree_hash(md) == before
    assert json.loads((sdir / "memcheck.jsonl").read_text().splitlines()[0])["types"]
