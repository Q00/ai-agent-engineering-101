"""icrl loop on a synthetic workspace with a fake clock and fake LLMs (no network): apply / revert bytes, versions,
external edits, STOP, freeze, loss cap, approver isolation, PII masking, idempotent ticks, the deploy check and the
real-data state build."""

import functools
import json
import os
import time
from pathlib import Path

import pytest
from icrl import loop
from icrl import state as ST

R3 = Path(__file__).resolve().parents[1]
DATA = Path(
    "/private/tmp/claude-501/-Users-sungsoolee-orca-workspaces-ai-agent-engineering-101-Agents-market/"
    "d5f40484-eeeb-4ce5-a60a-536b9c26ca16/scratchpad/data_20261104"
)
NOW0 = 1793800000.0 - (loop.BOUNDARY_LEAD_S - 600)  # same grid phase whatever the lead
ARM = "defaults.food.price_mult|up"
WHY = "WHY-SECRET reach me at kim@example.com"
CATS = {
    "F1": ("식품>김치/반찬/즉석요리>김치", "food", 30000),
    "G1": ("가구/침구>가구>침대/매트리스", "goods", 279000),
}


class Fake:
    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def complete(self, prompt, max_tokens=800):
        self.prompts.append(prompt)
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return {"text": r(prompt) if callable(r) else r}


def pick(prompt):
    arms = json.loads([x for x in prompt.splitlines() if x.startswith("ARMS ")][0][5:])
    a = arms.get(ARM) or next(
        x
        for x in arms.values()
        if "suggest" in x and not isinstance(x["suggest"], list)
    )
    return json.dumps(
        {
            "action": "change",
            "edits": [{"path": a["path"], "value": a["suggest"]}],
            "why": WHY,
        }
    )


HOLD = '{"action": "hold", "why": "nothing"}'
APPROVE = '{"decision": "approve", "reason": "fine"}'


def make_work(root, own_per_h=1, gp=10000, balance=5000000):
    w = root / "w"
    (w / ".r3").mkdir(parents=True)
    (w / ".market").mkdir()
    (w / "logs").mkdir()
    cat = {
        s: {"sku": s, "category": c, "kind": k, "list_price": lp}
        for s, (c, k, lp) in CATS.items()
    }
    (w / ".market" / "catalog.json").write_text(
        json.dumps(cat, ensure_ascii=False), encoding="utf-8"
    )
    (w / ".market" / "demand.json").write_text(
        json.dumps(
            [{"category": c, "kind": k} for c, k, _ in CATS.values()],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (w / ".r3" / "own_shops.json").write_text(
        json.dumps({"urls": ["https://ours.example"]}), encoding="utf-8"
    )
    (w / "logs" / "market-kit.log").write_text(
        f"10:00:00 [supply] 3 x thing for 30000, balance {balance}\n", encoding="utf-8"
    )
    ev, seq, t = [], 0, NOW0 - 26 * 3600
    n_own = 0
    while t < NOW0:
        for sku, (c, k, lp) in CATS.items():
            seq += 1
            ev.append(
                {
                    "seq": seq,
                    "ts": t,
                    "type": "settle",
                    "src": "market",
                    "data": {
                        "shop": "https://other.example",
                        "unit": f"x{seq}",
                        "sku": sku,
                        "amount": int(lp * (0.8 + 0.01 * (seq % 20))),
                    },
                }
            )
        if (t - NOW0) % int(3600 / own_per_h) < 300:
            n_own += 1
            u = f"u{n_own}"
            ev.append(
                {
                    "seq": seq + 1,
                    "ts": t - 60,
                    "type": "restock",
                    "src": "inventory",
                    "data": {
                        "unit": u,
                        "sku": "F1",
                        "category": CATS["F1"][0],
                        "kind": "food",
                        "unit_cost": 15000,
                        "supplied_at": t - 60,
                        "expires_at": None,
                    },
                }
            )
            ev.append(
                {
                    "seq": seq + 2,
                    "ts": t,
                    "type": "settle",
                    "src": "market",
                    "data": {
                        "shop": "https://ours.example",
                        "unit": u,
                        "sku": "F1",
                        "amount": 15000 + gp,
                        "unit_cost": 15000,
                    },
                }
            )
            ev.append(
                {
                    "seq": seq + 3,
                    "ts": t,
                    "type": "settle",
                    "src": "deal",
                    "data": {"sku": "F1", "amount": 20000, "value": 30000},
                }
            )
            seq += 3
        t += 300
    (w / ".r3" / "events.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in ev), encoding="utf-8"
    )
    return w


@pytest.fixture
def env(tmp_path, request):
    kw = getattr(request, "param", {})
    w = make_work(tmp_path, **kw)
    pol = tmp_path / "policy.json"
    pol.write_bytes((R3 / "policy.json").read_bytes())
    sdir = tmp_path / "st"
    sdir.mkdir()
    (sdir / "arms.json").write_text(
        json.dumps({ARM: [50.0, 1e6]})
    )  # make the price_mult arm the top draw
    prop, appr = Fake([pick, HOLD]), Fake([APPROVE])
    ctx = loop.Ctx(
        work=w,
        sdir=sdir,
        policy=pol,
        llms={"prop": prop, "appr": appr},
        fetch=lambda u: (500, b""),
        audit=tmp_path / "audit.jsonl",
    )
    return {
        "ctx": ctx,
        "pol": pol,
        "orig": pol.read_bytes(),
        "prop": prop,
        "appr": appr,
        "sdir": sdir,
    }


def rows(p):
    return (
        [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines()]
        if Path(p).exists()
        else []
    )


def start(env, first_arm=None, monkeypatch=None):
    if first_arm:
        monkeypatch.setattr(
            loop.X, "block_arm", lambda seed, i: first_arm if i == 0 else "A"
        )
    r = loop.step(NOW0, ctx=env["ctx"])
    assert r["status"] == "ok" and r["cycle"]["started"] == "e0001", r
    L = json.loads((env["sdir"] / "loop.json").read_text())
    t0 = L["active"]["t_start"]
    assert env["pol"].read_bytes() == env["orig"]  # scheduled, not yet written
    assert loop.step(t0 + 1, ctx=env["ctx"])["exp"] == "started"
    return t0


def test_first_cycle_applies_without_a_mode_file_and_ticks_are_idempotent(env):
    loop.step(NOW0, ctx=env["ctx"])
    loop.step(NOW0 + 30, ctx=env["ctx"])
    assert len(env["prop"].prompts) == 1 and not (env["sdir"] / "mode.json").exists()
    assert (
        json.loads((env["sdir"] / "loop.json").read_text())["active"]["status"]
        == "scheduled"
    )


def test_switchback_runs_to_keep_a_and_restores_identical_bytes(env):
    t0 = start(env)
    raw_b, seen_b = None, 0
    t = t0 + 1
    for _ in range(200):
        t += 600
        r = loop.step(t, ctx=env["ctx"])
        cur = env["pol"].read_bytes()
        if cur != env["orig"]:
            seen_b += 1
            raw_b = raw_b or cur
            assert cur == raw_b  # B bytes are deterministic
        if "active" not in r:
            break
    end = [x for x in rows(env["sdir"] / "experiments.jsonl") if x["event"] == "end"]
    assert end[0]["verdict"] == "keep_a" and end[0]["stats"]["pairs"] == 12
    assert env["pol"].read_bytes() == env["orig"] and seen_b > 0
    new, old = json.loads(raw_b), json.loads(env["orig"])
    assert new["defaults"]["food"]["price_mult"] == pytest.approx(
        old["defaults"]["food"]["price_mult"] + 0.03
    )
    runs = env["ctx"].runs
    acts = [runs.meta(v)["action"] for v in runs.versions()]
    assert acts[0] == "baseline" and "change" in acts and "rollback" in acts
    assert runs.policy_bytes(runs.latest()) == env["orig"]
    assert len(rows(env["sdir"] / "lessons.jsonl")) == 1
    audit = rows(env["ctx"].audit)
    assert audit and all(a["writer"] == "rlpo" and a["allowed"] for a in audit)


@pytest.mark.parametrize(
    "env", [{"own_per_h": 12, "gp": 20000, "balance": 1000000}], indirect=True
)
def test_loss_cap_reverts_and_rejects(env, monkeypatch):
    t0 = start(env, "B", monkeypatch)
    assert env["pol"].read_bytes() != env["orig"]
    r = loop.step(t0 + 1800, ctx=env["ctx"])
    assert r["exp"] == "loss_cap" and env["pol"].read_bytes() == env["orig"]
    end = [x for x in rows(env["sdir"] / "experiments.jsonl") if x["event"] == "end"][0]
    assert end["verdict"] == "reject" and end["reason"].startswith("loss_cap")


def test_stop_file_reverts_and_stops_proposing(env, monkeypatch):
    t0 = start(env, "B", monkeypatch)
    assert env["pol"].read_bytes() != env["orig"]
    (env["sdir"] / "STOP").write_text("")
    assert loop.step(t0 + 600, ctx=env["ctx"])["status"] == "stopped"
    assert env["pol"].read_bytes() == env["orig"]
    n = len(env["prop"].prompts)
    assert (
        loop.step(t0 + 7200, ctx=env["ctx"])["status"] == "stopped"
        and len(env["prop"].prompts) == n
    )


def test_freeze_reverts_before_round_end(env, monkeypatch):
    start(env, "B", monkeypatch)
    r = loop.step(ST.ROUND_END - ST.FREEZE_S + 1, ctx=env["ctx"])
    assert r["status"] == "frozen" and env["pol"].read_bytes() == env["orig"]
    assert [x for x in rows(env["sdir"] / "experiments.jsonl") if x["event"] == "end"][
        0
    ]["verdict"] == "frozen"


def test_external_edit_aborts_the_experiment_and_is_never_overwritten(env, monkeypatch):
    t0 = start(env, "B", monkeypatch)
    edited = env["pol"].read_bytes().replace(b'"version": 10', b'"version": 11')
    assert edited != env["pol"].read_bytes()
    env["pol"].write_bytes(edited)
    r = loop.step(t0 + 600, ctx=env["ctx"])
    assert r["live"] == "external" and env["pol"].read_bytes() == edited
    end = [x for x in rows(env["sdir"] / "experiments.jsonl") if x["event"] == "end"][0]
    assert end["verdict"] == "aborted_external_edit"
    runs = env["ctx"].runs
    assert (
        runs.meta(runs.latest())["note"] == "external"
        and runs.policy_bytes(runs.latest()) == edited
    )
    loop.step(t0 + 1200, ctx=env["ctx"])  # proposer holds now: nothing written
    assert env["pol"].read_bytes() == edited


def test_external_edit_without_experiment_becomes_the_baseline(env):
    env["prop"].replies = [HOLD]
    loop.step(NOW0, ctx=env["ctx"])
    edited = env["orig"].replace(b'"version": 10', b'"version": 12')
    env["pol"].write_bytes(edited)
    r = loop.step(NOW0 + 600, ctx=env["ctx"])
    assert (
        r["status"] == "ok"
        and r["live"] == "external"
        and env["pol"].read_bytes() == edited
    )


def test_approver_never_sees_the_why_and_logs_are_masked(env):
    loop.step(NOW0, ctx=env["ctx"])
    assert env["appr"].prompts and all(
        "WHY-SECRET" not in p for p in env["appr"].prompts
    )
    start_row = rows(env["sdir"] / "experiments.jsonl")[0]
    assert "[EMAIL]" in start_row["why"] and "kim@example.com" not in json.dumps(
        start_row
    )
    loop.ask(
        env["ctx"],
        "appr",
        "contact lee@example.org or 010-1234-5678 " + "x" * 200,
        NOW0,
    )
    log = (env["sdir"] / "llm.jsonl").read_text(encoding="utf-8")
    assert (
        "lee@example.org" not in log and "010-1234-5678" not in log and "[EMAIL]" in log
    )
    assert all(
        len(r["head"]) <= 80 and len(r["sha256"]) == 64
        for r in rows(env["sdir"] / "llm.jsonl")
    )


def test_bad_json_is_a_hold_and_retries_next_step(env):
    env["prop"].replies = ["not json {"]
    r = loop.step(NOW0, ctx=env["ctx"])
    assert (
        r["status"] == "ok"
        and r["cycle"]["proposer"] is None
        and env["pol"].read_bytes() == env["orig"]
    )
    loop.step(NOW0 + 60, ctx=env["ctx"])
    assert len(env["prop"].prompts) == 1
    loop.step(NOW0 + loop.STEP_S, ctx=env["ctx"])
    assert len(env["prop"].prompts) == 2


def _listing(p):
    return sorted(str(x.relative_to(p)) for x in p.rglob("*")) if p.exists() else None


def test_deploy_check_writes_only_the_copy(env, tmp_path, monkeypatch, capsys):
    env["prop"].replies = [HOLD]
    before = {
        p: _listing(p)
        for p in (R3 / "experiments" / "runs", R3 / "icrl" / "state", env["sdir"])
    }
    ctx_kw = {
        "work": env["ctx"].work,
        "policy": env["pol"],
        "llms": {"prop": env["prop"], "appr": env["appr"]},
        "fetch": lambda u: (200, b'{"shops": [], "buyers": []}'),
    }
    monkeypatch.setattr(loop, "Ctx", functools.partial(loop.Ctx, **ctx_kw))
    out = tmp_path / "copy.json"
    code = loop.main(["--check", "--policy-out", str(out)])
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0, summary
    assert summary["synthetic"] and summary["wrote_copy"] and summary["validate_ok"]
    assert env["pol"].read_bytes() == env["orig"] and out.read_bytes() != env["orig"]
    assert {p: _listing(p) for p in before} == before


@pytest.mark.skipif(
    not (DATA / ".r3" / "events.jsonl").exists(), reason="real data copy missing"
)
def test_state_builder_on_real_data(tmp_path):
    pol = tmp_path / "policy.json"
    orig = (R3 / "policy.json").read_bytes()
    pol.write_bytes(orig)
    ctx = loop.Ctx(
        work=DATA,
        sdir=tmp_path / "st",
        policy=pol,
        fetch=lambda u: (500, b""),
        llms={"prop": Fake([HOLD]), "appr": Fake([APPROVE])},
        audit=tmp_path / "audit.jsonl",
    )
    t = time.monotonic()
    r = loop.step(1793788878.46 + 60, ctx=ctx)
    took = time.monotonic() - t
    store = ST.load_store(tmp_path / "st" / "store.json")
    print(
        f"real-data step {took:.2f} s; events {os.path.getsize(DATA / '.r3' / 'events.jsonl')} B; market rows "
        f"{len(store['m'])}, own units {len(store['units'])}, sold {len(store['sold'])}, deals {len(store['deals'])}, "
        f"store {os.path.getsize(tmp_path / 'st' / 'store.json')} B, balance {store['balance']}"
    )
    assert r["status"] == "ok" and r["cycle"]["proposer"] == "hold" and took < 60
    assert (
        store["off"] == os.path.getsize(DATA / ".r3" / "events.jsonl")
        and store["balance"] is not None
    )
    assert pol.read_bytes() == orig
