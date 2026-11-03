"""v1.19 taste gate: synthetic ledgers with planted buyer tastes, tmp R3 state, pinned judge, fixed seeds."""
import json
import math
import random
import shutil
import sys
from pathlib import Path

import pytest

R3 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(R3))
from experiments import lessons as LS  # noqa: E402
from experiments import prereg as PR  # noqa: E402
from harness import lock  # noqa: E402
from memory import run as MR  # noqa: E402
from memory.graph import Graph  # noqa: E402
from taste import forecast as TF  # noqa: E402
from taste import gate as TG  # noqa: E402
from taste import judge as TJ  # noqa: E402

H = 3600
T0 = 1_800_000_000.0  # an hour boundary
CATS = ("c1", "c2", "c3", "c4")
CAT = {f"s{c}": {"category": c, "name": c, "list_price": 1000} for c in CATS}


class Sim:
    """A ledger written hour by hour; the graph ingests what is older than `now`, like memory.run."""

    def __init__(self, root, seed):
        self.root, self.rng, self.rows, self.fed = Path(root), random.Random(seed), [], 0
        self.root.mkdir(parents=True, exist_ok=True)
        self.G = Graph(catalog=CAT)

    def hour(self, h, n, buyers, pick):
        for i in range(n):
            b = self.rng.choice(buyers)
            ts = T0 + h * H + (i + 0.5) * H / n
            s = len(self.rows) + 1
            self.rows.append({"seq": s, "ts": ts, "type": "settle", "src": "market", "key": f"market:{s}",
                              "role": "market", "mine": False,
                              "data": {"buyer": b, "shop": "shop1", "sku": f"s{pick(b)}", "amount": 900,
                                       "ledger": 10_000 + s, "order": f"o{s}"}})

    def tick(self, now, **kw):
        new = [e for e in self.rows[self.fed:] if e["ts"] < now]
        with open(self.root / "events.jsonl", "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(e) + "\n" for e in new))
        for e in new:
            self.G.ingest(e)
        self.fed += len(new)
        self.G.sweep(now)
        return TG.tick(self.G, CAT, self.root, now, **kw)


def fav(rng, p):
    """Buyer b_i buys CATS[1 + i % 3] with probability p, else any category."""
    return lambda b: CATS[1 + int(b[1:]) % 3] if rng.random() < p else rng.choice(CATS)


def taste_market(root, seed, hours, reversal_at=None):
    sim = Sim(root, seed)
    old = [f"o{i}" for i in range(10)]
    cur = [f"b{i}" for i in range(8)]
    for h in range(-96, -30):                       # a gone cohort that bought only c1
        sim.hour(h, 6, old, lambda b: "c1")
    taste = fav(sim.rng, 0.9)
    for h in range(-24, hours):
        rev = reversal_at is not None and h >= reversal_at
        sim.hour(h, 8, cur, (lambda b: "c1" if sim.rng.random() < 0.9 else sim.rng.choice(CATS)) if rev else taste)
    return sim


def flat_market(root, seed, hours):
    sim = Sim(root, seed)
    buyers = [f"z{i}" for i in range(20)]
    for h in range(-48, hours):
        sim.hour(h, 8, buyers, lambda b: sim.rng.choices(CATS, weights=(4, 3, 2, 1))[0])
    return sim


def run(sim, ticks):
    out = []
    for k in range(ticks):
        out.append(sim.tick(T0 + k * H + 700))
    return out


def test_forecast_is_a_distribution_over_the_catalog_from_past_data_only(tmp_path):
    sim = taste_market(tmp_path, 1, 2)
    sim.tick(T0 + 2 * H)
    f = TF.forecast(sim.G, T0, CAT)
    for k in ("A", "B"):
        assert set(f[k]) == set(CATS) and abs(sum(f[k].values()) - 1) < 1e-9 and min(f[k].values()) > 0
    assert f["A"]["c1"] < f["B"]["c1"] and f["buyers"] == 8   # the gone c1 cohort is in B, not in A
    g = Graph(catalog=CAT)                                     # same answer from data < T0 only
    for e in sim.rows:
        if e["ts"] < T0:
            g.ingest(e)
    assert TF.forecast(g, T0, CAT) == f
    assert TF.forecast(Graph(catalog=CAT), T0, CAT) is None


def test_planted_tastes_adopt_at_a_look_and_record_a_lesson(tmp_path):
    sim = taste_market(tmp_path, 7, 14)
    gates = run(sim, 14)
    states = [g["state"] for g in gates]
    k = states.index("adopted")
    g = gates[k]
    assert g["look"] in TG.LOOKS and g["evaluable_hours"] == g["look"] == k and g["ci"][0] > 0 and g["mean_d"] > 0
    assert states[:k] == ["shadow"] * k and g["note"].startswith("taste gate shadow -> adopted")
    on_disk = json.loads((tmp_path / "taste_gate.json").read_text())
    assert on_disk["state"] == "adopted" and on_disk["asof"] == T0 + 13 * H and set(on_disk["forecast"]) == set(CATS)
    assert on_disk["prereg"] == PR.load(tmp_path / "prereg.jsonl", TG.EXP_ID)["hash"]
    assert on_disk["judge"] == lock.file_hash(R3 / "taste" / "judge.py")
    (les,) = LS.read(tmp_path / "lessons.jsonl")
    assert les["verdict"] == "adopted" and les["exp_id"] == TG.EXP_ID and les["result"]["n"] == g["look"]
    assert set(les["evidence_seq"]) <= {e["seq"] for e in sim.rows} and len(les["evidence_seq"]) == 8 * g["look"]


def test_same_seed_same_decision(tmp_path):
    a = run(taste_market(tmp_path / "a", 7, 8), 8)
    b = run(taste_market(tmp_path / "b", 7, 8), 8)
    assert [(g["state"], g["ci"]) for g in a] == [(g["state"], g["ci"]) for g in b]


def test_no_taste_signal_does_not_adopt(tmp_path):
    gates = run(flat_market(tmp_path, 11, 50), 50)
    assert {g["state"] for g in gates} == {"shadow"} and gates[-1]["evaluable_hours"] == 49
    assert gates[-1]["look"] == 48 and gates[-1]["ci"][0] <= 0
    assert not (tmp_path / "lessons.jsonl").exists()


def test_reversal_after_adoption_rolls_back(tmp_path):
    sim = taste_market(tmp_path, 7, 20, reversal_at=8)
    gates = run(sim, 20)
    states = [g["state"] for g in gates]
    assert "adopted" in states and "rolled_back" in states
    k = states.index("rolled_back")
    assert states.index("adopted") < 8 < k and set(states[k:]) == {"rolled_back"}
    assert gates[k]["recent_mean_d"] < 0
    les = LS.read(tmp_path / "lessons.jsonl")
    assert [x["verdict"] for x in les] == ["adopted", "rolled_back"] and les[1]["result"]["delta"] < 0


def test_evaluation_before_prereg_is_refused(tmp_path):
    with pytest.raises(PR.PreregError, match="not registered"):
        TG.evaluate(CAT, tmp_path, T0)


def test_judge_hash_mismatch_is_refused(tmp_path):
    PR.register(tmp_path / "prereg.jsonl", TG.record("0" * 64, T0))
    with pytest.raises(PR.PreregError, match="judge hash"):
        TG.evaluate(CAT, tmp_path, T0 + H)
    sim = taste_market(tmp_path, 7, 2)
    with pytest.raises(PR.PreregError, match="judge hash"):
        sim.tick(T0 + 700)


def test_tampered_judge_is_refused(tmp_path):
    for rel in lock.PINNED:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(R3 / rel, tmp_path / rel)
    lf = lock.pin(root=tmp_path) and tmp_path / "harness" / "lock.json"
    assert TG.judge_hash(lf) == lock.file_hash(R3 / "taste" / "judge.py")
    with open(tmp_path / "taste" / "judge.py", "a") as f:
        f.write("\n# edited\n")
    with pytest.raises(lock.JudgeTampered):
        TG.judge_hash(lf)


def test_hours_frozen_before_the_prereg_do_not_count(tmp_path):
    sim = taste_market(tmp_path, 7, 4)
    pre = [e for e in sim.rows if e["ts"] < T0 + 5]
    for e in pre:
        sim.G.ingest(e)
    assert TG.freeze(sim.G, CAT, tmp_path, T0 + 5)["frozen_at"] == T0 + 5   # no prereg yet
    assert not (tmp_path / "prereg.jsonl").exists()
    g1 = sim.tick(T0 + H + 700)                                          # registers now, scores hour T0
    rows = [json.loads(x) for x in (tmp_path / "taste" / "scores.jsonl").read_text().splitlines()]
    assert [(r["t"], r["evaluable"], r["why"]) for r in rows] == [(T0, False, "frozen before prereg")]
    assert rows[0]["n"] == 8 and g1["evaluable_hours"] == 0
    g2 = sim.tick(T0 + 2 * H + 700)
    assert g2["evaluable_hours"] == 1


def test_judge_scores_one_hour():
    ev = [{"seq": 1, "ts": T0 + 10, "type": "settle", "src": "market", "data": {"shop": "s", "sku": "sc2"}},
          {"seq": 2, "ts": T0 + H, "type": "settle", "src": "market", "data": {"shop": "s", "sku": "sc2"}},
          {"seq": 3, "ts": T0 + 20, "type": "settle", "src": "deal", "data": {"shop": "s", "sku": "sc2"}},
          {"seq": 4, "ts": T0 + 30, "type": "settle", "src": "market", "data": {"shop": "s", "sku": "x", "category": "c9"}}]
    A, B = {"c1": 0.1, "c2": 0.9}, {"c1": 0.5, "c2": 0.5}
    r = TJ.hour_loss(ev, CAT, T0, A, B)
    assert r["counts"] == {"c2": 1, "c9": 1} and r["seqs"] == [1, 4] and r["n"] == 2
    # c9 is in neither forecast: each forecast's smallest probability (A 0.1, B 0.5)
    assert abs(r["L_A"] + math.log(0.9) + math.log(0.1)) < 1e-12 and abs(r["L_B"] - 2 * math.log(2)) < 1e-12
    assert abs(r["d"] - (r["L_B"] - r["L_A"])) < 1e-12 and r["d"] < 0


def test_decide_looks_and_rollback_rule():
    rows = [{"t": i, "d": 1.0 + 0.1 * (i % 3), "seqs": [i]} for i in range(5)]
    assert TG.decide(rows)["state"] == "shadow" and TG.decide(rows)["look"] is None
    rows.append({"t": 5, "d": 1.0, "seqs": [5]})
    d = TG.decide(rows)
    assert d["state"] == "adopted" and d["look"] == 6 and d["ci"][0] > 0
    rows += [{"t": 6 + i, "d": -3.0, "seqs": [6 + i]} for i in range(3)]
    d = TG.decide(rows)
    assert d["state"] == "rolled_back" and d["at"] == 8 and abs(d["mean_d"] - sum(r["d"] for r in rows) / 9) < 1e-12


def test_gate_is_registered_in_the_memory_runner():
    assert TG.step in MR.POST_INGEST
