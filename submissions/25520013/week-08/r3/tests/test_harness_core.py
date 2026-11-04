"""Meta-harness core (harness/, experiments/): DiD + stratified bootstrap on synthetic data with a known effect,
split determinism, prereg tamper detection, loss cap -> rollback, judge / checker tamper detection, lessons evidence
check, decision-path isolation. Synthetic data only, fixed seeds, tmp dirs."""

import hashlib
import json
import random
import re
import shutil
from pathlib import Path

import pytest
from experiments import decide, lessons, prereg, split
from harness import bounds, judge, lock
from harness import rollback as RB
from harness.runs import PolicyRuns, RunsError

from tests import helpers as H

ROOT = Path(__file__).resolve().parents[1]
DATA_SEED = 7


def rows(effect, n=60, seed=DATA_SEED, trend=100.0, sd=200.0):
    """A: before ~ 1000, after ~ 1000 + trend; B: before ~ 1000, after ~ 1000 + trend + effect."""
    rng = random.Random(seed)
    out = []
    for g, extra in (("A", 0.0), ("B", effect)):
        out += [
            {"group": g, "period": "before", "value": rng.gauss(1000, sd)}
            for _ in range(n)
        ]
        out += [
            {
                "group": g,
                "period": "after",
                "value": rng.gauss(1000 + trend + extra, sd),
            }
            for _ in range(n)
        ]
    return out


# ---------------- decide
def test_did_known_effect_ci_covers_it_and_composes():
    r = decide.evaluate(rows(500.0), min_n=10, seed=11, n_boot=2000)
    assert r["lo"] <= 500.0 <= r["hi"] and r["lo"] > 0
    assert abs(r["point"] - 500.0) < 150 and r["verdict"] == "compose"
    assert r["n_A"] == r["n_B"] == 60 and r["seed"] == 11 and r["n_boot"] == 2000


def test_did_null_effect_ci_contains_zero_and_keeps():
    r = decide.evaluate(rows(0.0), min_n=10, seed=11)
    assert r["lo"] <= 0.0 <= r["hi"] and r["verdict"] == "keep"


def test_did_negative_effect_rolls_back():
    r = decide.evaluate(rows(-500.0), min_n=10, seed=11)
    assert r["hi"] < 0 and r["verdict"] == "rollback"


def test_bootstrap_is_seeded():
    a = decide.bootstrap_ci(rows(300.0), seed=5, n_boot=500)
    assert a == decide.bootstrap_ci(rows(300.0), seed=5, n_boot=500)
    assert a != decide.bootstrap_ci(rows(300.0), seed=6, n_boot=500)


def test_too_few_samples_is_no_adoption():
    r = decide.evaluate(rows(500.0, n=5), min_n=10)
    assert r["n_A"] == 5 and r["verdict"] == "keep"
    assert (
        decide.evaluate([r_ for r_ in rows(500.0) if r_["group"] == "B"], min_n=1)[
            "verdict"
        ]
        == "keep"
    )
    assert decide.verdict(900, 120, 1600, 30, 30, 10, cap_hit=True) == "rollback"
    assert decide.verdict(None, None, None, 30, 30, 10) == "keep"


# ---------------- split
def test_split_is_deterministic_and_salted():
    cats = list(split.catalog_kinds())
    a, b = split.split(cats, "salt-1")
    assert (a, b) == split.split(list(reversed(cats)), "salt-1") and sorted(
        a + b
    ) == sorted(cats)
    assert not set(a) & set(b)
    c = cats[0]
    first = hashlib.sha256(f"salt-1\x00{c}".encode()).digest()[0]
    assert (c in b) == bool(first & 1)  # stable sha256, not hash()
    assert any(split.split(cats, f"s{i}") != (a, b) for i in range(5))


def test_catalog_kinds_reads_market_shape(tmp_path):
    p = tmp_path / "catalog.json"
    p.write_text(
        json.dumps(
            {
                "_note": "x",
                "S1": {"category": "c1", "kind": "food"},
                "S2": {"category": "c2", "kind": "goods"},
                "S3": {"category": "c1", "kind": "food"},
            }
        )
    )
    assert split.catalog_kinds(p) == {"c1": "food", "c2": "goods"}


# ---------------- prereg
def _rec(**kw):
    r = {
        "exp_id": "e1",
        "hypothesis": "food price_mult 0.95 raises profit",
        "criterion": "lo > 0",
        "metric": "judge primary per category-hour",
        "min_n": 10,
        "seed": 20261103,
        "judge_hash": lock.file_hash(ROOT / "harness" / "judge.py"),
    }
    r.update(kw)
    return r


def test_prereg_register_load_and_tamper(tmp_path):
    p = tmp_path / "prereg.jsonl"
    rec = prereg.register(p, _rec())
    assert len(rec["hash"]) == 64 and prereg.load(p, "e1") == rec
    assert prereg.verify(p, "e1", judge_hash=rec["judge_hash"])
    with pytest.raises(prereg.PreregError):
        prereg.check(dict(rec, criterion="hi > 0"))  # changed after registration
    with pytest.raises(prereg.PreregError):
        prereg.register(p, _rec(criterion="hi > 0"))  # no re-registration
    with pytest.raises(prereg.PreregError):
        prereg.verify(p, "e1", judge_hash="0" * 64)  # judge changed since registration
    p.write_text(p.read_text().replace("lo > 0", "lo > -100"))  # file edited in place
    with pytest.raises(prereg.PreregError):
        prereg.load(p, "e1")
    with pytest.raises(prereg.PreregError):
        prereg.register(tmp_path / "x.jsonl", _rec(seed=None))


def test_prereg_caps():
    assert prereg.CAP_PER_EXP == 0.02 and prereg.CAP_TOTAL == 0.05
    assert prereg.over_cap(19_999, 1_000_000) is None
    assert "per experiment" in prereg.over_cap(20_000, 1_000_000)
    assert "all experiments" in prereg.over_cap(
        15_000, 1_000_000, concurrent=(15_000, 20_000)
    )


# ---------------- loss cap -> rollback
@pytest.fixture
def sb(tmp_path):
    return H.sandbox(tmp_path)


def _good(value=0.95):
    return {
        "edits": [{"path": "defaults.food.price_mult", "value": value}],
        "rationale": "t",
        "evidence_seq": [40],
        "kind_hint": "food",
    }


def test_loss_cap_rolls_back_to_parent(sb):
    base = sb["runs"].policy_bytes(1)
    v, new = RB.apply_proposal(sb["runs"], _good(), sb["live"], H.T0 + 10)
    assert v == 2 and Path(sb["live"]).read_bytes() == new != base
    ok = RB.enforce_cap(sb["runs"], 2, sb["live"], 19_999, 1_000_000, H.T0 + 20)
    assert ok["status"] == "ok" and Path(sb["live"]).read_bytes() == new
    out = RB.enforce_cap(sb["runs"], 2, sb["live"], 20_000, 1_000_000, H.T0 + 30)
    assert out["status"] == "rolled_back" and out["rollback_version"] == 3
    assert Path(sb["live"]).read_bytes() == base
    m = sb["runs"].meta(3)
    assert m["action"] == "rollback" and m["parent"] == 1 and "loss cap" in m["note"]


def test_total_cap_with_concurrent_experiments_rolls_back(sb):
    RB.apply_proposal(sb["runs"], _good(), sb["live"], H.T0 + 10)
    out = RB.enforce_cap(
        sb["runs"],
        2,
        sb["live"],
        10_000,
        1_000_000,
        H.T0 + 30,
        concurrent=(20_000, 20_000),
    )
    assert out["status"] == "rolled_back" and Path(sb["live"]).read_bytes() == sb[
        "runs"
    ].policy_bytes(1)


def test_live_edited_outside_harness_is_refused(sb):
    Path(sb["live"]).write_bytes(Path(sb["live"]).read_bytes() + b" ")
    with pytest.raises(RunsError):
        RB.apply_proposal(sb["runs"], _good(), sb["live"], H.T0 + 10)
    assert sb["runs"].latest() == 1


def test_runs_keep_policy_score_trace(sb):
    row = sb["runs"].close(1, sb["events"], H.T0 + 1000, shop_names=(H.SHOP,))
    assert row["score"]["primary"] == 5000 and sb["runs"].score(1) == row
    assert sb["runs"].trace(1) == list(
        range(1, 21)
    )  # 10 restock + 10 settle rows counted
    d = sb["runs"].root / "v0001"
    assert {p.name for p in d.iterdir()} >= {
        "policy.json",
        "meta.json",
        "score.json",
        "trace.json",
    }


# ---------------- bounds
def test_bounds_step_and_whitelist():
    base = json.loads(H.BASE_POLICY.read_bytes())
    assert bounds.check(base, _good(0.92 + 0.25)) == []  # exactly 0.25 x range
    assert bounds.check(base, _good(0.92 + 0.26))  # more than 0.25 x range
    bad = dict(_good(), edits=[{"path": "shop.balance_floor", "value": 0}])
    assert bounds.check(base, bad)
    many = dict(
        _good(),
        edits=[
            {"path": f"categories.c{i}.price_mult", "value": 0.95}
            for i in range(bounds.MAX_EDITS + 1)
        ],
    )
    assert bounds.check(base, many)
    with pytest.raises(bounds.EditRejected):
        bounds.apply(base, _good(2.0))


# ---------------- lock
def _lock_copy(tmp_path):
    for rel in lock.PINNED:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, tmp_path / rel)
    lock.pin(root=tmp_path)
    return tmp_path / "harness" / "lock.json"


def test_real_lock_verifies():
    assert lock.verify() is True


@pytest.mark.parametrize(
    "rel", ["harness/judge.py", "harness/bounds.py", "experiments/decide.py"]
)
def test_judge_or_checker_tamper_is_caught(tmp_path, rel):
    lf = _lock_copy(tmp_path)
    assert lock.verify(lock_file=lf)
    with open(tmp_path / rel, "a") as f:
        f.write("\n# edited\n")
    with pytest.raises(lock.JudgeTampered):
        lock.verify(lock_file=lf)


def test_single_file_verify_and_missing_lock(tmp_path):
    p = ROOT / "harness" / "judge.py"
    assert lock.verify(p, lock.file_hash(p))
    with pytest.raises(lock.JudgeTampered):
        lock.verify(p, "0" * 64)
    with pytest.raises(lock.JudgeTampered):
        lock.verify(lock_file=tmp_path / "nope.json")


def test_judge_is_pure_and_windowed():
    ev = H.ledger_events()
    a = judge.score(ev, shop_names=(H.SHOP,), t_from=H.T0, t_to=H.T0 + 1000)
    assert a == judge.score(ev, shop_names=(H.SHOP,), t_from=H.T0, t_to=H.T0 + 1000)
    assert a["primary"] == 5000 and a["settles"] == 10 and a["restocks"] == 10
    assert judge.score(ev, shop_names=("other",))["primary"] == -10000  # costs only
    assert lock.score_locked(ev, shop_names=(H.SHOP,))["primary"] == 5000


# ---------------- lessons
def test_lessons_require_known_evidence(tmp_path, sb):
    p = tmp_path / "lessons.jsonl"
    kw = dict(
        domain="food",
        claim="price_mult 1.0 kept",
        n={"A": 7, "B": 15},
        ci=[-448.0, 1689.0],
    )
    with pytest.raises(lessons.LessonError):
        lessons.add(p, evidence_seqs=[2, 9999], known_seqs=sb["ledger"], **kw)
    with pytest.raises(lessons.LessonError):
        lessons.add(p, evidence_seqs=[], known_seqs=sb["ledger"], **kw)
    with pytest.raises(lessons.LessonError):
        lessons.add(
            p, evidence_seqs=[2], known_seqs=sb["ledger"], **dict(kw, ci=[5, 1])
        )
    assert not p.exists()
    r = lessons.add(
        p,
        evidence_seqs=[2, 4],
        known_seqs=lessons._seqs(sb["ledger"]),
        ts=H.T0,
        verdict="keep",
        **kw,
    )
    lessons.add(
        p,
        domain="goods",
        claim="0.84 rolled back",
        evidence_seqs=[6],
        n=35,
        ci=[-2156, -439],
        known_seqs=sb["ledger"],
        ts=H.T0 + 5,
        verdict="rollback",
    )
    assert lessons.read(p, "food") == [r] and len(lessons.read(p)) == 2
    assert r["result"] == {
        "delta": None,
        "ci": [-448.0, 1689.0],
        "n": {"A": 7, "B": 15},
    }
    assert lessons.for_editor(p, "goods", before_ts=H.T0 + 5) == []
    assert (
        lessons._seqs(sb["ledger"]) == set(range(1, 41))
        and lessons._seqs(tmp_path / "none") == set()
    )


# ---------------- isolation
def test_decision_path_imports_no_shadow_modules():
    files = list((ROOT / "hooks").glob("*.py")) + [
        ROOT / f
        for f in (
            "aggregates.py",
            "cards.py",
            "ledger.py",
            "metrics.py",
            "owner.py",
            "policy.py",
            "reputation.py",
            "volume.py",
        )
    ]
    for f in files:
        s = f.read_text(encoding="utf-8")
        if f.name == "seller.py":   # 2026-11-04: the one allowed LLM edge, see tests/test_isolation.py ALLOWED
            s = re.sub(r"^\s*from llm\.live_v120c import .*$", "", s, flags=re.M)
        assert not re.search(
            r"^\s*(from|import)\s+(llm|harness|experiments|memory)\b", s, re.M
        ), f
