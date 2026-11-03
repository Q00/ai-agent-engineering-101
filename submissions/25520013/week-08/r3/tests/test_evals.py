"""v1.18 evals/: memory_eval order check (seed 7), a broken layer failing it, context_eff, forum_check on a
synthetic state dir with a post written by the real hooks/forum.py. Tmp dirs only, no network."""

import ast
import json
import sys
from pathlib import Path

from evals import ABILITIES, context_eff, forum_check, memory_eval, synth
from evals.answer import VARIANTS
from evals.truth import Truth

R3 = Path(__file__).resolve().parents[1]
SEED = 7
T0 = 1_800_000_000.0
CAT = {
    "k1": {"category": "식품>김치", "name": "포기김치", "list_price": 10000},
    "k2": {"category": "식품>김치", "name": "총각김치", "list_price": 12000},
    "f1": {"category": "과일", "name": "사과", "list_price": 8000},
}


def test_memory_eval_assert_order_seed7(capsys):
    assert memory_eval.main(["--synthetic", "--seed", str(SEED), "--assert-order"]) == 0
    out = capsys.readouterr().out
    assert "assert-order: OK" in out and "full wrong" not in out
    scores, info = memory_eval.evaluate(SEED)
    assert all(scores["full"][a] == 1.0 for a in ABILITIES)
    assert info["shifted"] >= 4  # the planted taste shift shows up in the trajectory questions
    for v, target in VARIANTS.items():
        assert scores[v][target] < 1.0, v


def test_broken_layer_as_full_fails_the_order_check():
    """Scoring a layer that ignores refunds as `full`: it no longer beats the no_refund variant on KU."""
    scores, _ = memory_eval.evaluate(SEED, full="no_refund")
    bad = memory_eval.order_problems(scores)
    assert any("no_refund does not lose on KU" in b for b in bad)
    scores, _ = memory_eval.evaluate(SEED, full="no_at")
    assert memory_eval.order_problems(scores)


def test_synthetic_ledger_is_seeded():
    a, b = synth.make(SEED), synth.make(SEED)
    assert a[0] == b[0] and synth.make(SEED + 1)[0] != a[0]
    events = a[0]
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert all(x["ts"] < y["ts"] for x, y in zip(events, events[1:]))
    kinds = {(e["type"], (e["data"] or {}).get("stage")) for e in events}
    assert {("settle", None), ("fail", "refund"), ("fail", "result"), ("broken", None), ("reject", None)} <= kinds


def test_truth_and_synth_import_nothing_from_memory():
    for f in ("truth.py", "synth.py"):
        tree = ast.parse((R3 / "evals" / f).read_text(encoding="utf-8"))
        mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        mods |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not any(m.split(".")[0] == "memory" for m in mods), (f, mods)


def test_truth_answers_by_hand():
    """Truth on a tiny ledger: refund visibility, trajectory end, ABSTAIN before the record."""

    def settle(seq, ts, buyer, sku, amount):
        return {"seq": seq, "ts": ts, "type": "settle", "src": "market", "key": f"m:{seq}",
                "data": {"buyer": buyer, "shop": "s1", "sku": sku, "amount": amount, "ledger": seq, "order": f"o{seq}"}}

    ev = [settle(1, T0, "A", "k1", 9000), settle(2, T0 + 10, "A", "f1", 7000), settle(3, T0 + 20, "A", "k1", 6000),
          {"seq": 4, "ts": T0 + 30, "type": "fail", "src": "market", "key": "m:4",
           "data": {"stage": "refund", "detail": {"order": "o2", "shop": "s1", "sku": "f1"}}}]
    T = Truth(ev, CAT)
    assert T.taste_at("A", T0 + 30) == {"식품>김치": 2, "과일": 1}
    assert T.taste_at("A", T0 + 31) == {"식품>김치": 2}
    assert T.taste_at("A", T0 + 20 + 86400 + 1) == {}
    assert T.taste_at("A", T0) == "__abstain__" and T.price_last("s1", "k1", T0) == "__abstain__"
    assert T.ref_price("s1", "k1", T0 + 25) == 6000 and T.price_last("s1", "k1", T0 + 15) == 9000


def test_context_eff_runs(capsys):
    assert context_eff.main(["--synthetic", "--seed", str(SEED)]) == 0
    assert "total" in capsys.readouterr().out
    m = context_eff.measure(SEED)
    assert m["total"]["n"] == sum(m[a]["n"] for a in ABILITIES)
    assert m["total"]["mem_b"] < m["total"]["grep_b"] < m["total"]["raw_b"]


# ---------------- forum_check
def forum_state(tmp_path, monkeypatch):
    """A state dir whose forum_posts.jsonl was written by hooks/forum.py run_once over synthetic settles."""
    sys.path.insert(0, str(R3 / "hooks"))
    import forum

    st, md = tmp_path / "state", tmp_path / "market"
    st.mkdir()
    md.mkdir()
    (md / "catalog.json").write_text(json.dumps(CAT, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("R3_STATE", str(st))
    rows, now = [], T0 + 4 * 3600
    plan = [(-5 * 3600, "k1", 5000)]  # outside the 3 h window
    plan += [(-170 * 60 + i * 900, "k1" if i % 3 else "k2", 8000 + 250 * i) for i in range(11)]
    plan += [(-3000, "f1", 7000), (-2000, "f1", 7500)]
    for i, (dt, sku, amt) in enumerate(plan, 1):
        rows.append({"seq": i, "ts": now + dt, "type": "settle", "src": "market", "key": f"market:{900 + i}",
                     "data": {"buyer": f"b{i % 4}", "shop": "s1", "sku": sku, "amount": amt, "ledger": 900 + i}})
    rows.append({"seq": 99, "ts": now - 100, "type": "refund", "src": "market", "key": "x", "data": {"sku": "k1"}})
    (st / "events.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    why = forum.run_once(md, now=now, pol={"forum": {"interval_s": 0}})
    assert why.startswith("queued: 식품>김치"), why
    return st, md


def test_forum_check_passes_on_a_consistent_post(tmp_path, monkeypatch, capsys):
    st, md = forum_state(tmp_path, monkeypatch)
    assert forum_check.main(["--state", str(st), "--market", str(md)]) == 0
    out = capsys.readouterr().out
    assert "PASS post 0" in out and "1/1 posts PASS" in out
    post = json.loads((st / "forum_posts.jsonl").read_text(encoding="utf-8"))
    assert "11건" in post["post"] and len(post["refs"]) == 8  # recomputed from 11 settles, only 8 refs


def test_forum_check_fails_on_tampered_posts(tmp_path, monkeypatch, capsys):
    st, md = forum_state(tmp_path, monkeypatch)
    p = json.loads((st / "forum_posts.jsonl").read_text(encoding="utf-8"))
    bad_n = dict(p, post=p["post"].replace("11건", "12건", 1))
    bad_ref = dict(p, refs=p["refs"][:-1] + [901])  # the settle outside the window
    bad_med = dict(p, post=p["post"].replace("체결가 중앙 ", "체결가 중앙 1", 1))
    (st / "forum_posts.jsonl").write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in (p, bad_n, bad_ref, bad_med)), encoding="utf-8")
    assert forum_check.main(["--state", str(st), "--market", str(md)]) == 1
    out = capsys.readouterr().out
    assert "PASS post 0" in out and "FAIL post 1" in out and "FAIL post 2" in out and "FAIL post 3" in out
    assert "n: post 12 != ledger 11" in out and "ref 901 is not" in out and "amt: post 1" in out
