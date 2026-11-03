"""memory/run.py and memory/source.py: tmp R3_STATE with synthetic events + catalog, no network."""

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
from memory import run as MR
from memory import source

HERE = Path(__file__).resolve().parents[1]
T0 = 1_800_000_000.0
CAT = {
    "k1": {"category": "김치", "name": "김치 1kg", "list_price": 10000},
    "b1": {"category": "침대/매트리스", "name": "침대", "list_price": 500000},
}


def ev(seq, ts, buyer="A", sku="k1", type_="settle", **data):
    d = {
        "buyer": buyer,
        "shop": "s1",
        "sku": sku,
        "amount": 9000,
        "ledger": 1000 + seq,
        "order": f"o{seq}",
    }
    return {
        "seq": seq,
        "ts": ts,
        "type": type_,
        "src": "market",
        "key": f"market:{1000 + seq}",
        "role": "market",
        "mine": False,
        "data": dict(d, **data) if type_ == "settle" else data,
    }


def line(e):
    return json.dumps(e, ensure_ascii=False) + "\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    root, md = tmp_path / "state", tmp_path / "market"
    root.mkdir()
    md.mkdir()
    (md / "catalog.json").write_text(
        json.dumps(CAT, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setenv("R3_STATE", str(root))
    monkeypatch.setenv("R3_BUYER_NAME", "me")
    return root, md


def test_source_partial_last_line(tmp_path):
    p = tmp_path / "events.jsonl"
    p.write_text(
        line(ev(1, T0)) + "not json\n" + line(ev(2, T0 + 1))[:20], encoding="utf-8"
    )
    rows, off = source.read_new(p, 0)
    assert [e["seq"] for _, e in rows] == [1] and off == len(
        (line(ev(1, T0)) + "not json\n").encode()
    )
    assert source.read_new(tmp_path / "missing.jsonl", 0) == ([], 0)
    assert source.events_by_seq(p, [1, 7]) == {1: ev(1, T0)}


def test_two_runs_resume_the_cursor_and_finish_a_partial_line(env):
    root, md = env
    full = line(ev(3, T0 + 600, sku="b1"))
    (root / "events.jsonl").write_text(
        line(ev(1, T0)) + line(ev(2, T0 + 300)) + full[:25], encoding="utf-8"
    )
    out = MR.run_once(md, now=T0 + 1000)
    assert out.startswith("+2 events seq=2 (graph 2 gotchas 0 prices 2)")
    mem = root / "memory"
    cur = json.loads((mem / "cursor.json").read_text())
    assert set(cur["layers"]) == {"graph", "gotchas", "prices"} and cur["seq"] == 2
    assert all(
        o == len((line(ev(1, T0)) + line(ev(2, T0 + 300))).encode())
        for o in cur["layers"].values()
    )
    with open(root / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(
            full[25:]
            + line(
                ev(4, T0 + 700, type_="fail", stage="refund", detail={"order": "o3"})
            )
        )
    out = MR.run_once(md, now=T0 + 1100)
    assert out.startswith("+2 events seq=4")
    g = json.loads((mem / "graph.json").read_text())
    assert [o["seq"] for o in g["obs"]] == [1, 2, 3] and g["obs"][2]["to"] == T0 + 700
    assert len(json.loads((mem / "prices.json").read_text())["pairs"]) == 2
    assert (
        json.loads((mem / "gotchas.json").read_text())["records"][0]["stage"]
        == "refund"
    )
    assert MR.run_once(md, now=T0 + 1200).startswith(
        "+0 events seq=4 (graph 0 gotchas 0 prices 0) graph ops 0"
    )
    log = [json.loads(x) for x in (mem / "graph_ops.jsonl").read_text().splitlines()]
    assert [(r["op"], r["kind"]) for r in log] == [
        ("ADD", "shop_sku"),
        ("ADD", "buyer_cat"),
        ("ADD", "shop_sku"),
        ("ADD", "buyer_cat"),
        ("DELETE", "settle_obs"),
    ]
    assert all(
        set(r) == {"ts", "op", "kind", "key", "evidence", "overridden", "reason"}
        for r in log
    )


def test_sweep_deletes_an_inactive_buyer_at_last_plus_24h(env):
    root, md = env
    (root / "events.jsonl").write_text(line(ev(1, T0)), encoding="utf-8")
    MR.run_once(md, now=T0 + 10)
    MR.run_once(md, now=T0 + 25 * 3600)
    log = [
        json.loads(x)
        for x in (root / "memory" / "graph_ops.jsonl").read_text().splitlines()
    ]
    assert [(r["op"], r["ts"]) for r in log if r["op"] == "DELETE"] == [
        ("DELETE", T0 + 24 * 3600)
    ] * 2


def test_missing_layer_is_skipped_and_a_late_layer_starts_from_zero(env, monkeypatch):
    root, md = env
    (root / "events.jsonl").write_text(
        line(ev(1, T0)) + line(ev(2, T0 + 1)), encoding="utf-8"
    )
    monkeypatch.setattr(
        MR, "LAYERS", MR.LAYERS[:1] + (("stub", "memory_stub_mod", "Stub"),)
    )
    out = MR.run_once(md, now=T0 + 5)
    assert "stub skipped (ModuleNotFoundError" in out

    class Stub:
        def __init__(self, catalog=None):
            self.catalog, self.seqs = catalog, []

        def ingest(self, e):
            self.seqs.append(e["seq"])
            return True

        def to_json(self):
            return {"seqs": self.seqs}

        @classmethod
        def from_json(cls, d, catalog=None):
            s = cls(catalog)
            s.seqs = d["seqs"]
            return s

    monkeypatch.setitem(
        sys.modules, "memory_stub_mod", types.SimpleNamespace(Stub=Stub)
    )
    out = MR.run_once(md, now=T0 + 6)
    assert out.startswith(
        "+2 events seq=2 (graph 0 stub 2)"
    )  # graph already had them, stub from 0
    assert json.loads((root / "memory" / "stub.json").read_text()) == {"seqs": [1, 2]}
    assert MR.run_once(md, now=T0 + 7).startswith("+0 events")


def test_post_ingest_steps_run_and_a_failing_step_is_noted(env, monkeypatch):
    root, md = env
    (root / "events.jsonl").write_text(line(ev(1, T0)), encoding="utf-8")
    seen = []

    def forecast(ctx):
        seen.append(
            (
                ctx["now"],
                ctx["layers"]["graph"].buyer_taste("A"),
                len(ctx["events"]),
                ctx["catalog"] == CAT,
            )
        )
        return "forecast ok"

    def broken(ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(MR, "POST_INGEST", [forecast, broken])
    out = MR.run_once(md, now=T0 + 5)
    assert seen == [(T0 + 5, {"김치": 1}, 1, True)]
    assert "; forecast ok; broken error RuntimeError: boom" in out
    assert (root / "memory" / "graph.json").exists()


def test_loop_survives_exceptions(env, monkeypatch, capsys):
    root, md = env
    calls = []

    def flaky(md_, now=None):
        calls.append(now)
        if len(calls) == 1:
            raise ValueError("bad run")
        return "fine"

    def sleep(s):
        if len(calls) >= 2:
            raise SystemExit(0)

    monkeypatch.setattr(MR, "run_once", flaky)
    monkeypatch.setattr(MR.time, "sleep", sleep)
    monkeypatch.setattr(
        sys,
        "argv",
        ["memory.run", "--state", str(md), "--loop", "60", "--now", str(T0)],
    )
    with pytest.raises(SystemExit):
        MR.main()
    out = capsys.readouterr().out.splitlines()
    assert [x.split(" ", 1)[1] for x in out] == [
        "[memory] error ValueError: bad run",
        "[memory] fine",
    ]


def test_cli_module_run_prints_one_line(env):
    root, md = env
    (root / "events.jsonl").write_text(line(ev(1, T0)), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "memory.run", "--state", str(md), "--now", str(T0 + 5)],
        cwd=HERE,
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert (
        len(lines) == 1
        and lines[0][8:]
        == " [memory] +1 events seq=1 (graph 1 gotchas 0 prices 1) graph ops 2"
    )
