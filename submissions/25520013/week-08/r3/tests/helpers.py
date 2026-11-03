"""Shared synthetic fixtures for the v1.17 LLM tests (no network, tmp dirs only)."""
import json
import shutil
from pathlib import Path

from cards import LINE_W, LINES
from harness.runs import PolicyRuns

R3 = Path(__file__).resolve().parents[1]
BASE_POLICY = R3 / "experiments" / "state" / "policy_base.json"
T0 = 1_800_000_000.0
SHOP = "mock-shop-a"


def card(extra=""):
    lines = [f"H seq=40 events=40 cats=3 skus=3 shops=1 {extra}"] + [f"line {i}" for i in range(LINES - 1)]
    return "\n".join(x[:LINE_W].ljust(LINE_W) for x in lines)


def ledger_events():
    """seq 1..40: 10 restock+settle pairs, then reject / fail rows (seqs 21..40)."""
    ev, seq, t = [], 0, T0
    for i in range(10):
        for typ, data, src in (("restock", {"unit": f"u{i}", "unit_cost": 1000, "kind": "food"}, "seller"),
                               ("settle", {"shop": SHOP, "buyer": "x", "unit": f"u{i}", "amount": 1500}, "market")):
            seq += 1
            t += 10
            ev.append({"seq": seq, "ts": t, "type": typ, "src": src, "key": f"{src}:{seq}", "data": data})
    for i in range(20):
        seq += 1
        t += 10
        typ, data = (("reject", {"reason_code": "price"}) if i % 2 else ("fail", {"stage": "checkout"}))
        ev.append({"seq": seq, "ts": t, "type": typ, "src": "market", "key": f"m:{seq}", "data": data})
    return ev


def write_ledger(path, events):
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path


LESSONS = [
    {"exp_id": "m8-goods-p084", "domain": "goods", "verdict": "rollback",
     "change": "categories.<B arm goods>.price_mult: 0.92 -> 0.84 (3 B / 4 A categories)",
     "condition": "mock seed 705", "result": {"delta": -1155.0, "ci": [-2156.0, -439.0], "n": {"A": 20, "B": 15}},
     "evidence_seq": [2, 4, 6], "ts": T0 - 100},
    {"exp_id": "m8-food-p100", "domain": "food", "verdict": "keep",
     "change": "categories.<B arm food>.price_mult: 0.92 -> 1.0", "condition": "mock seed 705",
     "result": {"delta": 620.5, "ci": [-448.0, 1689.0], "n": {"A": 7, "B": 15}}, "evidence_seq": [8, 10],
     "ts": T0 - 100},
]


def sandbox(tmp_path):
    """-> dict(runs, live, ledger, audit, state) with v1 = baseline policy_base.json bytes."""
    st = tmp_path / "state"
    st.mkdir()
    live = tmp_path / "live_policy.json"
    shutil.copyfile(BASE_POLICY, live)
    runs = PolicyRuns(root=st / "policy_runs", state=st)
    runs.register(live.read_bytes(), T0, "baseline", note="test baseline", code_hash="test")
    ev = ledger_events()
    led = write_ledger(tmp_path / "events.jsonl", ev)
    return {"runs": runs, "live": live, "ledger": led, "events": ev, "audit": tmp_path / "audit.jsonl", "state": st}
