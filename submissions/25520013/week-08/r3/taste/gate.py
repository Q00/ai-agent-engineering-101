"""v1.19 taste gate: preregistered, forward-only shadow test of forecast A against restock's baseline B.

  r = tick(G, catalog, root, now)   # prereg (once) -> freeze this hour -> score closed hours -> decide -> gate file
  step(ctx)                         # memory.run POST_INGEST step; a note only when the state changes

Files under root (= <R3_STATE>): prereg.jsonl (experiments.prereg, exp_id EXP_ID), taste/snapshots.jsonl (one row
per hour t: A and B from data < t, frozen_at), taste/scores.jsonl (one row per closed hour, scored by the pinned
taste/judge.py), lessons.jsonl (experiments.lessons, on adopt and rollback), taste_gate.json (tmp + replace).
Forward-only: an hour counts (evaluable) only when its snapshot was frozen at or after the prereg ts and it had at
least one market settle. An hour is scored GRACE_S after it closed (ledger ingest lag).
Decision, replayed from the scores each tick: looks at LOOKS evaluable hours; at a look n, hour-level bootstrap of
mean d over the first n hours (random.Random(decide.SEED), decide.N_BOOT draws, percentile CI at 1 - ALPHA_LOOK,
ALPHA_LOOK = 0.05 / len(LOOKS), Bonferroni); lower bound > 0 -> "adopted". After adoption, at each further
evaluable hour, mean d of the last ROLLBACK_N evaluable hours < 0 -> "rolled_back" (final). Past the last look
without adoption the gate stays "shadow".
"""
import json
import os
import random
from pathlib import Path

from experiments import decide as D
from experiments import lessons as LS
from experiments import prereg as PR
from harness import lock
from memory import source
from taste import forecast as F
from taste import judge as J

EXP_ID = "taste-v1"
JUDGE_REL = "taste/judge.py"
LOOKS = (6, 12, 24, 48, 96, 192)
ALPHA_LOOK = 0.05 / len(LOOKS)
ROLLBACK_N = 24
HOUR_S = 3600
GRACE_S = 600


def _rows(path):
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _append(path, row):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_json(path, obj):
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def judge_hash(lock_file=None):
    """The pinned judge hash, after every pinned file verified (JudgeTampered otherwise)."""
    lf = Path(lock_file or lock.LOCK_FILE)
    lock.verify(lock_file=lf)
    files = json.loads(lf.read_text(encoding="utf-8"))["files"]
    if JUDGE_REL not in files:
        raise lock.JudgeTampered(f"{JUDGE_REL} is not pinned in {lf.name}")
    return files[JUDGE_REL]


def record(jh, now):
    return {"exp_id": EXP_ID, "ts": now, "seed": D.SEED, "judge_hash": jh, "min_n": LOOKS[0],
            "hypothesis": "A next-hour category forecast from buyer trajectories (taste graph, Dirichlet-smoothed "
                          "to market share) predicts realized market settles better than restock's velocity "
                          "ranking B.",
            "metric": "d = L_B - L_A per evaluable hour, multinomial log loss of realized market settles per "
                      "category in [t, t+1h) (taste/judge.py, pinned); forecasts frozen at the hour start from "
                      "data < t, after this registration only",
            "criterion": f"adopt at the first look n in {list(LOOKS)} evaluable hours where the hour-bootstrap "
                         f"{1 - ALPHA_LOOK:.4f} CI lower bound of mean d > 0 (Bonferroni 0.05/{len(LOOKS)}, seed "
                         f"{D.SEED}, {D.N_BOOT} draws); after adoption roll back when mean d over the last "
                         f"{ROLLBACK_N} evaluable hours < 0",
            "params": {"alpha": F.ALPHA, "eps": F.EPS, "window_h": F.WINDOW_H, "tau_h": F.TAU_H,
                       "grace_s": GRACE_S, "looks": list(LOOKS), "alpha_look": ALPHA_LOOK, "n_boot": D.N_BOOT}}


def ensure_prereg(root, jh, now):
    """Register EXP_ID once (ts = now); afterwards the registered record, checked against the judge hash."""
    p = Path(root) / "prereg.jsonl"
    if not any(r.get("exp_id") == EXP_ID for r in _rows(p)):
        PR.register(p, record(jh, now))
    return PR.verify(p, EXP_ID, jh)


def freeze(G, catalog, root, now):
    """Append this hour's snapshot (forecasts from data < hour start) once; None when frozen or no data."""
    td = Path(root) / "taste"
    td.mkdir(parents=True, exist_ok=True)
    t = float(int(now // HOUR_S) * HOUR_S)
    if any(r.get("t") == t for r in _rows(td / "snapshots.jsonl")):
        return None
    f = F.forecast(G, t, catalog)
    if f is None:
        return None
    row = dict(f, t=t, frozen_at=now)
    _append(td / "snapshots.jsonl", row)
    return row


def boot_ci(ds, alpha, seed=D.SEED, n_boot=D.N_BOOT):
    rng, n = random.Random(seed), len(ds)
    stats = sorted(sum(rng.choices(ds, k=n)) / n for _ in range(n_boot))
    return [D._pct(stats, alpha / 2), D._pct(stats, 1 - alpha / 2)]


def decide(rows):
    """Replay over evaluable score rows (time order) -> {state, look, ci, mean_d, at, recent_mean_d, hours}."""
    out = {"state": "shadow", "look": None, "ci": None, "mean_d": None, "at": None, "recent_mean_d": None,
           "hours": []}
    ds = [r["d"] for r in rows]
    for i in range(1, len(ds) + 1):
        if out["state"] == "shadow" and i in LOOKS:
            lo, hi = boot_ci(ds[:i], ALPHA_LOOK)
            out.update(look=i, ci=[lo, hi], mean_d=sum(ds[:i]) / i, hours=rows[:i])
            if lo > 0:
                out.update(state="adopted", at=rows[i - 1]["t"])
        elif out["state"] == "adopted":
            last = ds[max(0, i - ROLLBACK_N):i]
            out["recent_mean_d"] = sum(last) / len(last)
            if out["recent_mean_d"] < 0:
                lo, hi = boot_ci(last, 0.05)
                out.update(state="rolled_back", at=rows[i - 1]["t"], ci=[lo, hi], mean_d=out["recent_mean_d"],
                           hours=rows[max(0, i - ROLLBACK_N):i])
                break
    return out


def evaluate(catalog, root, now, lock_file=None):
    """Score closed hours with the pinned judge, then decide; refuses (PreregError) before a prereg or with a judge
    hash other than the registered one, and (JudgeTampered) when a pinned file changed."""
    root = Path(root)
    jh = judge_hash(lock_file)
    rec = PR.verify(root / "prereg.jsonl", EXP_ID, jh)
    td = root / "taste"
    td.mkdir(parents=True, exist_ok=True)
    done = {r["t"] for r in _rows(td / "scores.jsonl")}
    todo = [s for s in _rows(td / "snapshots.jsonl") if s["t"] not in done and now >= s["t"] + HOUR_S + GRACE_S]
    if todo:
        events = [e for _, e in source.read_new(root / "events.jsonl", 0)[0]]
        for s in sorted(todo, key=lambda s: s["t"]):
            r = J.hour_loss(events, catalog, s["t"], s["A"], s["B"], HOUR_S)
            fwd = s["frozen_at"] >= rec["ts"]
            _append(td / "scores.jsonl", dict(r, t=s["t"], frozen_at=s["frozen_at"], scored_at=now, judge=jh,
                                              evaluable=bool(fwd and r["n"] > 0),
                                              why=None if fwd else "frozen before prereg"))
    rows = sorted((r for r in _rows(td / "scores.jsonl") if r.get("evaluable") and r.get("judge") == jh),
                  key=lambda r: r["t"])
    return rec, jh, rows, decide(rows)


def _lesson(root, dec, now):
    seqs = sorted({s for r in dec["hours"] for s in r["seqs"]})
    n = len(dec["hours"])
    if dec["state"] == "adopted":
        claim = (f"taste forecast A beat restock velocity B at look {dec['look']}: mean d {dec['mean_d']:.3f}, "
                 f"CI [{dec['ci'][0]:.3f}, {dec['ci'][1]:.3f}] > 0 -> restock ranks categories by A")
        change = "restock_plan taste= (category ranking only)"
    else:
        claim = (f"taste forecast A worse than B after adoption: mean d {dec['mean_d']:.3f} over the last {n} "
                 f"evaluable hours < 0 -> back to velocity ranking")
        change = "restock_plan taste=None"
    return LS.add(Path(root) / "lessons.jsonl", domain="restock", claim=claim, evidence_seqs=seqs, n=n,
                  ci=dec["ci"], known_seqs=Path(root) / "events.jsonl", exp_id=EXP_ID, verdict=dec["state"],
                  change=change, delta=dec["mean_d"], ts=now)


def tick(G, catalog, root, now, lock_file=None):
    root = Path(root)
    prev = source.read_json(root / "taste_gate.json", {}) or {}
    jh = judge_hash(lock_file)
    rec = ensure_prereg(root, jh, now)
    freeze(G, catalog, root, now)
    rec, jh, rows, dec = evaluate(catalog, root, now, lock_file)
    note = None
    if dec["state"] != (prev.get("state") or "shadow"):
        _lesson(root, dec, now)
        note = f"taste gate {prev.get('state') or 'shadow'} -> {dec['state']} (n {len(rows)}, ci {dec['ci']})"
    snaps = _rows(root / "taste" / "snapshots.jsonl")
    last = max(snaps, key=lambda s: s["t"]) if snaps else None
    gate = {"exp_id": EXP_ID, "state": dec["state"], "forecast": last and last["A"], "asof": last and last["t"],
            "evaluable_hours": len(rows), "look": dec["look"], "ci": dec["ci"], "mean_d": dec["mean_d"],
            "recent_mean_d": dec["recent_mean_d"], "changed_at": dec["at"], "prereg": rec["hash"], "judge": jh,
            "updated": now}
    _write_json(root / "taste_gate.json", gate)
    return dict(gate, note=note)


def step(ctx):
    G = (ctx.get("layers") or {}).get("graph")
    if G is None:
        return "taste: no graph layer"
    return tick(G, ctx["catalog"], ctx["root"], ctx["now"])["note"]
