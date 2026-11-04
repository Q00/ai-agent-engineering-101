"""icrl loop entry: step(now) -> status dict, never raises (DESIGN.md sec 2, 6, 8, 9, 11).

Three experiment lanes run side by side (sec 6): price, stock (supply / structure) and buyer, each with its own
active experiment and next cycle step; price and stock are the shop side, buyer the buyer side. No lane offers the
forum family. Policy writes are path-level: B = the lane's edits on top of the current live policy, A = only the
lane's paths set back to their recorded A values.

Called by the runner every ~600 s. Acts on 20-min block boundaries by time (grid = policy volume.round_start_ts,
the first supply window opens_at, + k x 1200 s, shifted BOUNDARY_LEAD_S earlier so a switch lands before the supply
window it is meant for), idempotent across ticks and restarts: every bit of state is a file under the state dir.

Write path: policy_gate.check_writer("rlpo", live) -> PolicyRuns.register -> harness.rollback._atomic. Not
harness.rollback.apply_proposal: its harness.bounds whitelist refuses shop.*, buyer.max_shops, enums, lists and
category overrides, and its lock.verify / single-registry check do not fit an A/B switch every hour.
"""

import contextlib
import copy
import fcntl
import hashlib
import json
import math
import os
import time
import traceback
from pathlib import Path

from icrl import board as B
from icrl import experiment as X
from icrl import meta
from icrl import space as SP
from icrl import state as ST

HERE = Path(__file__).resolve().parent
WORK = HERE.parent
STEP_S = 1200
BOUNDARY_LEAD_S = 900
HOLD_STEPS = 3
LOSS_CAP_FRAC = 0.15  # v1.25c: single-block disaster stop (was 0.02: 10-20 % placebo trips with 20-40 min blocks)
LOSS_T = 4.0  # v1.25c: harm stop, one-sided t of the paired won differences (placebo trips 2-4 %, 30 h replay)
LOSS_MIN_PAIRS = 3
LOSS_REF_S = 3600  # v1.24b: the loss cap's A reference covers at least 1 h even when blocks are 20 min
RETEST_AFTER_S = 86400
MEMCHECK_S = 3600  # 성수님 2026-11-04: memory recall check every hour (was daily)
SEED = 20261104
N_LESSONS = 20
N_CATS = 8
PRIOR_M = 0.3
LANES = {
    "price": ("price",),
    "stock": ("supply", "structure"),
    "buyer": ("buyer",),
}
LANE_SIDE = {"price": "shop", "stock": "shop", "buyer": "buyer"}
# v1.26: the official terms each lane's verdict metric moves (price gp_h, stock value_h, buyer surplus_h)
LANE_TERMS = {"price": ("profit", "revenue"), "stock": ("profit", "revenue"), "buyer": ("surplus", "deals")}
FAMILY_LANE = {f: ln for ln, fs in LANES.items() for f in fs}
# harness.json (sec 7): per-side proposer settings written by the meta-harness; defaults = the prompt without it
HARNESS_DEFAULT = {
    "n_lessons": N_LESSONS,
    "recall": "graph_first",
    "prompt_notes": "",
    "features": None,
}
RECALL_ORDERS = ("graph_first", "recent_first")
N_LESSONS_RANGE = (5, 40)
NOTES_MAX = 600
FEATURES = ("time", "shop", "buyer", "board", "market", "budget", "stars_guard", "lanes")
_LIVE = {}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class Ctx:
    def __init__(
        self,
        work=None,
        sdir=None,
        policy=None,
        llms=None,
        fetch=None,
        runs=None,
        audit=None,
    ):
        from harness.runs import PolicyRuns
        from llm.ops_llm import policy_gate

        self.work = Path(work or os.getcwd())
        self.sdir = Path(sdir or os.environ.get("ICRL_STATE") or HERE / "state")
        self.sdir.mkdir(parents=True, exist_ok=True)
        self.P = ST.paths(self.work)
        self.policy = Path(policy or policy_gate.live_policy())
        self.llms = llms
        self.fetch = fetch or B.http_get
        self.runs = runs or PolicyRuns(self.sdir / "runs", self.sdir / "runs")
        self.audit = audit
        self.space = SP.load()

    def f(self, name):
        return self.sdir / name

    def get_llms(self):
        if self.llms is None:
            from llm.live_v120c import Live
            from llm.ops_llm import RoleLLM

            live = _LIVE.setdefault("live", Live(base=WORK))
            live.refresh()
            self.llms = {"prop": RoleLLM(live, "rlpo_prop")}
        return self.llms


def _jload(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _jsave(p, obj):
    p = Path(p)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    os.replace(tmp, p)


def _append(p, row):
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _rows(p):
    """JSON-lines rows of p; a bad or non-object line is skipped, not the whole file."""
    try:
        lines = Path(p).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return []
    out = []
    for x in lines:
        try:
            r = json.loads(x) if x.strip() else None
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def log(ctx, now, ev, **kw):
    _append(ctx.f("log.jsonl"), dict(kw, ts=now, ev=ev))


# ---------------------------------------------------------------- time grid
def anchor(pol):
    a = (pol.get("volume") or {}).get("round_start_ts")
    return (int(a) if isinstance(a, (int, float)) else 0) - BOUNDARY_LEAD_S


def step_idx(pol, now):
    return int((now - anchor(pol)) // STEP_S)


def boundary(pol, k):
    return anchor(pol) + k * STEP_S


# ---------------------------------------------------------------- lanes
def lane_of_path(space, path):
    e = SP.entry(space, path) if isinstance(path, str) else None
    return FAMILY_LANE.get(e["family"]) if e else None


def lane_of_exp(space, exp):
    """exp["lane"] when it is a lane today; else (no lane, or an old "shop" / "forum" row) by its first edit."""
    ln = exp.get("lane")
    return ln if ln in LANES else lane_of_path(space, (exp.get("edits") or [{}])[0].get("path"))


def arm_lane(space, a):
    return lane_of_path(space, a.get("path") or (a.get("edits") or [{}])[0].get("path"))


def _home_lane(space, exp):
    """The lane a migrated experiment belongs to: its first edit's family lane; a family no lane offers (forum)
    keeps the family name as its lane, which _step then ends as lane_off."""
    path = (exp.get("edits") or [{}])[0].get("path")
    e = SP.entry(space, path) if isinstance(path, str) else None
    return (FAMILY_LANE.get(e["family"]) or e["family"]) if e else exp_lane(exp)


def migrate(ctx, L):
    """loop.json -> L["lanes"], idempotent, run on every load. Old single-lane format (top-level "active" /
    "next_cycle_step"): the running experiment goes to the lane of its family, every lane keeps the old
    next_cycle_step. Old "shop" lane (price, supply, structure): split into price and stock, both inherit its
    next_cycle_step, its experiment goes to the lane of its first edit's family. Schedules stay untouched. Lanes not
    in LANES (forum) stay until _step ends them (lane_off)."""
    lanes = L.setdefault("lanes", {})
    old, nxt = L.pop("active", None), L.pop("next_cycle_step", None)
    shop = lanes.pop("shop", None)
    if shop is not None:
        for ln in ("price", "stock"):
            lanes.setdefault(ln, {"active": None, "next_cycle_step": shop.get("next_cycle_step")})
    for ln in LANES:
        lanes.setdefault(ln, {"active": None, "next_cycle_step": nxt})
    for exp in (x for x in ((shop or {}).get("active"), old) if x):
        ln = _home_lane(ctx.space, exp)
        exp["lane"] = ln
        lanes.setdefault(ln, {"active": None, "next_cycle_step": nxt})["active"] = exp
    return lanes


def running(L):
    return [(ln, s["active"]) for ln, s in L["lanes"].items() if s.get("active")]


# ---------------------------------------------------------------- writes
def write_policy(ctx, L, raw, action, note, now):
    from harness.rollback import _atomic
    from llm.ops_llm import policy_gate

    policy_gate.check_writer(
        "rlpo",
        ctx.policy,
        live_policy=ctx.policy,
        audit=ctx.audit,
        icrl=True,
        action=action,
    )
    v = ctx.runs.register(raw, now, action, parent=L.get("live_version"), note=note)
    _atomic(ctx.policy, raw)
    L["live_version"], L["live_sha"] = v, sha(raw)
    return v


def _has(pol, parts):
    d = pol
    for p in parts:
        if not isinstance(d, dict) or p not in d:
            return False
        d = d[p]
    return True


def _restore(pol, a_pol, path):
    """Sets path in pol back to its value in a_pol; absent there: drops it and the parent dicts B created."""
    v, ok = SP.get(a_pol, path)
    if ok:
        SP.put(pol, path, copy.deepcopy(v))
        return
    parts, chain = SP.split(path), [pol]
    for p in parts[:-1]:
        if not isinstance(chain[-1].get(p), dict):
            return
        chain.append(chain[-1][p])
    chain[-1].pop(parts[-1], None)
    for i in range(len(parts) - 1, 0, -1):
        if chain[i] == {} and not _has(a_pol, parts[:i]):
            chain[i - 1].pop(parts[i - 1], None)


def arm_bytes(ctx, exp, arm):
    """Path-level arm bytes on top of the CURRENT live policy: B sets this experiment's paths to its values, A sets
    only those paths back to their values in A_version. Other lanes' paths stay as they are live. A restore that
    lands on A_version's content returns A_version's bytes, so a lane running alone restores byte-identically."""
    live_raw = ctx.policy.read_bytes()
    a_raw = ctx.runs.policy_bytes(exp["A_version"])
    a_pol, pol = json.loads(a_raw), json.loads(live_raw)
    for r in exp["edits"]:
        if arm == "B":
            SP.put(pol, r["path"], copy.deepcopy(r["value"]))
        else:
            _restore(pol, a_pol, r["path"])
    if arm == "A" and pol == a_pol:
        return a_raw
    return SP.dumps_like(live_raw, pol)


def put_arm(ctx, L, exp, arm, now):
    raw = arm_bytes(ctx, exp, arm)
    if sha(raw) == L["live_sha"]:
        return False
    v = write_policy(
        ctx,
        L,
        raw,
        "change" if arm == "B" else "rollback",
        f"icrl {exp['id']} {exp.get('lane')} arm {arm}",
        now,
    )
    if arm == "B" and exp.get("B_version") is None:
        exp["B_version"] = v
    return True


def _flat(d, pre=()):
    """Leaf values of a JSON tree by key tuple (an empty dict is a leaf)."""
    if isinstance(d, dict) and d:
        out = {}
        for k, v in d.items():
            out.update(_flat(v, pre + (str(k),)))
        return out
    return {pre: d}


def _pstr(t):
    """Key tuple -> action-space path; a category name stays whole (SP.split's rule)."""
    if len(t) >= 3 and t[0] == "categories":
        return "categories." + t[1] + "." + ".".join(t[2:])
    return ".".join(t)


def changed_paths(old_raw, new_raw):
    """v1.25: the leaf key tuples whose values differ between two policy files (added and removed keys included)."""
    a, b = _flat(json.loads(old_raw)), _flat(json.loads(new_raw))
    miss = object()
    return sorted(t for t in set(a) | set(b) if a.get(t, miss) != b.get(t, miss))


def edit_hits(space, exp, paths):
    """v1.25: the changed paths (key tuples) that touch a running experiment: one of its own edit paths, a parent or
    a child of one, or any action-space key of its lane's families (e.g. min_margin for a price_mult experiment)."""
    lane = exp_lane(exp)
    mine = [tuple(SP.split(r["path"])) for r in exp.get("edits") or []]
    return [
        t
        for t in paths
        if any(t[: len(m)] == m or m[: len(t)] == t for m in mine) or lane_of_path(space, _pstr(t)) == lane
    ]


def unset_b(ctx, L, exp, paths, now):
    """v1.26c (2026-11-05): an experiment aborted by an external edit puts its A values back on the paths the edit
    left alone, so an untested B value never becomes the new baseline (03:25 KST: e0004, e0006 and e0008 had left
    theirs live). A path the edit changed, or a parent or child of one, keeps the edit. -> the restored paths."""
    keep = [
        r
        for r in exp.get("edits") or []
        if not any(
            t[: len(m)] == m or m[: len(t)] == t
            for t in paths
            for m in [tuple(SP.split(r["path"]))]
        )
    ]
    if not keep or not put_arm(ctx, L, dict(exp, edits=keep), "A", now):
        return []
    L["base_version"] = L["live_version"]
    return [r["path"] for r in keep]


def check_live(ctx, L, now):
    """Registers the first / externally edited live bytes as a baseline. v1.25 (2026-11-05: all 9 experiments that
    ended before had been aborted by deploy-time edits, so no lesson ever formed): an edit aborts only the
    experiments it touches (edit_hits); the others keep running with the edit in place (arm_bytes works path-level
    on the live file) and the block open at the edit is marked ext_edit, so X.pair_diffs leaves its pair out. When
    the previous bytes cannot be read, every lane aborts as before."""
    raw = ctx.policy.read_bytes()
    h = sha(raw)
    if L.get("live_sha") == h:
        return None
    try:
        json.loads(raw)
    except ValueError:  # a half-written or broken live file: do not register it, alarm once per content
        if L.get("bad_live_sha") != h:
            L["bad_live_sha"] = h
            log(ctx, now, "alarm", what="live_policy_not_json")
        return "bad_live"
    first = L.get("live_sha") is None
    prev = L.get("live_version")
    v = ctx.runs.register(
        raw,
        now,
        "baseline",
        parent=prev,
        note="initial" if first else "external",
    )
    L.update(live_version=v, live_sha=h, base_version=v)
    if first:
        return None
    try:
        paths = changed_paths(ctx.runs.policy_bytes(prev), raw)
    except Exception:  # noqa: BLE001 - previous bytes unreadable: abort every lane
        paths = None
    aborted, kept, restored = [], [], []
    for ln, exp in running(L):
        if paths is None or edit_hits(ctx.space, exp, paths):
            exp.update(status="done", verdict="aborted_external_edit", t_end=now)
            _append(ctx.f("experiments.jsonl"), dict(event="end", **_exp_row(exp)))
            L["lanes"][ln]["active"] = None
            aborted.append(exp["id"])
            if paths is not None:
                restored += unset_b(ctx, L, exp, paths, now)
            continue
        if exp.get("blocks"):
            exp["blocks"][-1]["ext_edit"] = True
        exp.setdefault("ext_edits", []).append({"t": now, "version": v})
        kept.append(exp["id"])
    log(
        ctx,
        now,
        "alarm",
        what="external_edit",
        version=v,
        aborted=aborted,
        kept=kept,
        restored=restored,
        paths=None if paths is None else [_pstr(t) for t in paths][:20],
    )
    return "external"


# ---------------------------------------------------------------- metrics
def exp_lane(exp):
    """The lane of an experiment dict (rows built before lanes: by side, buyer side = the buyer lane)."""
    return exp.get("lane") or ("buyer" if exp.get("side") == "buyer" else "shop")


def proxy(m, exp):
    """Weighted per-hour proxy of the terms the lane can move (sec 5): shop = profit, revenue, stars; buyer = surplus,
    deals; forum = posts, plus the board forum score in long blocks only. Stars and posts are ledger terms. The long
    shop value change stands in for profit (and revenue), weighted like profit; a switchback supply / structure
    experiment (outcome "value") uses the whole-shop value change in place of profit, plus revenue and stars."""
    w = {k: v or 0.0 for k, v in exp["w"].items()}
    g = lambda k: w.get(k, 0.0) * (m.get(f"{k}_h") or 0.0)  # noqa: E731
    lane = exp_lane(exp)
    if lane == "forum":
        return g("posts") + (g("forum") if exp["protocol"] == "long" else 0.0)
    if lane == "buyer":
        return g("surplus") + g("deals")
    if exp["protocol"] == "long":
        return w.get("profit", 0.0) * m["value_h"] + g("stars_rank")
    if exp.get("outcome") == "value":
        return w["profit"] * m["value_h"] + w["revenue"] * m["rev_h"] + g("stars_rank")
    return w["profit"] * m["gp_h"] + w["revenue"] * m["rev_h"] + g("stars_rank")


def won(m, exp):
    if exp["side"] == "buyer":
        return m["surplus_h"]
    return m["value_h"] if _valued(exp) else m["gp_h"]


def stars_h(m, base):
    """Change of our running star average contributed by the block's own-shop reviews, per hour: (sum - k x avg) /
    (n + k) / hours with base = {n, avg} fixed per experiment (0 without a base)."""
    n, avg = (base or {}).get("n") or 0, (base or {}).get("avg")
    if avg is None or n + m["rv_k"] <= 0:
        return 0.0
    return (m["rv_sum"] - m["rv_k"] * avg) / (n + m["rv_k"]) / m["hours"]


def _valued(exp):
    return exp["protocol"] == "long" or exp.get("outcome") == "value"


def measure(store, stats, exp, t0, t1, sdir=None, login=None):
    """Ledger metrics over [t0, t1), stars and posts included. The board snapshots under sdir give only the forum
    score of a long forum block (missing: 0 and listed in board_missing) and the raw stars drop for the guard."""
    m = ST.block_metrics(store, t0, t1, stats if _valued(exp) else None)
    m["stars_rank_h"] = stars_h(m, exp.get("stars_base"))
    bt = B.block_terms(sdir, login, t0, t1)
    used = ["forum"] if exp_lane(exp) == "forum" and exp["protocol"] == "long" else []
    m.update({f"{t}_h": bt[f"{t}_h"] for t in used})
    return {
        "metric": proxy(m, exp),
        "won": won(m, exp),
        "hours": m["hours"],
        "ledger": {
            "stars_rank_h": round(m["stars_rank_h"], 6),
            "reviews": m["rv_k"],
            "posts_h": round(m["posts_h"], 4),
        },
        "board": {f"{t}_h": round(bt[f"{t}_h"], 6) for t in used},
        "board_missing": [t for t in used if t in bt["missing"]],
        "board_snaps": bt["snaps"],
        "stars_drop": bt["stars_drop"],
    }


def _login(pol):
    return (pol.get("owner") or {}).get("github")


def stars_guard(ctx, exp, blk, now):
    """Flags a closed block of a shop (price / supply / structure) experiment whose raw stars fell by >= STARS_GUARD
    against the block-start snapshot, and logs it to guards.jsonl (numbers for the shop-side lanes' proposer state)."""
    d = blk.get("stars_drop")
    blk["stars_guard"] = bool(
        exp["side"] == "shop" and d is not None and d >= B.STARS_GUARD - 1e-9
    )
    if blk["stars_guard"]:
        exp["stars_guard_trips"] = exp.get("stars_guard_trips", 0) + 1
        _append(
            ctx.f("guards.jsonl"),
            {
                "ts": now,
                "exp": exp["id"],
                "arm": blk["arm"],
                "block": blk["i"],
                "paths": [r["path"] for r in exp["edits"]],
                "stars_drop": d,
            },
        )


def guard_nums(ctx, paths, now):
    """Numbers only: stars-guard trips in the last 24 h and earlier trips of experiments touching these paths."""
    g = _rows(ctx.f("guards.jsonl"))
    recent = [r for r in g if r.get("ts", 0) >= now - 86400]
    return {
        "threshold": B.STARS_GUARD,
        "trips_24h": len(recent),
        "worst_drop_24h": max((r["stars_drop"] for r in recent), default=None),
        "trips_same_path": sum(bool(set(r.get("paths") or []) & set(paths)) for r in g),
    }


# ---------------------------------------------------------------- experiment engine
def _exp_row(exp):
    keep = (
        "id",
        "lane",
        "arm",
        "mode",
        "protocol",
        "block_steps",
        "seed",
        "side",
        "retest",
        "t_start",
        "t_end",
        "status",
        "verdict",
        "stats",
        "why",
        "A_version",
        "B_version",
        "reason",
        "stars_guard_trips",
        "reblocked_from",
        "ext_edits",
    )
    return dict(
        {k: exp.get(k) for k in keep},
        edits=[
            {k: r[k] for k in ("path", "current", "value", "dir")} for r in exp["edits"]
        ],
        blocks=len(exp["blocks"]),
    )


def end(ctx, L, exp, verdict, stats, now, reason=None):
    lane = exp_lane(exp)
    final = "B" if verdict == "adopt" else "A"
    put_arm(ctx, L, exp, final, now)
    exp.update(status="done", verdict=verdict, stats=stats, t_end=now, reason=reason)
    L["base_version"] = L["live_version"]
    post = _jload(ctx.f("arms.json"), {})
    if verdict in ("adopt", "reject", "keep_a") and stats:
        d, n = X.std_effect(
            stats, exp["protocol"], (exp.get("a_ref") or {}).get("metric")
        )
        X.update(post, exp["arm"], d, n, exp.get("prior", 0.0))
        _jsave(ctx.f("arms.json"), post)
    if verdict == "adopt" and not exp.get("retest"):
        L.setdefault("retests", []).append(
            {
                "id": "retest|" + exp["arm"],
                "of": exp["id"],
                "due": now + RETEST_AFTER_S,
                "edits": [
                    {"path": r["path"], "value": r["current"]} for r in exp["edits"]
                ],
            }
        )
    _append(ctx.f("experiments.jsonl"), dict(event="end", **_exp_row(exp)))
    r0 = exp["edits"][0]
    _append(
        ctx.f("lessons.jsonl"),
        {
            "ts": now,
            "exp": exp["id"],
            "lane": lane,
            "arm": exp["arm"],
            "path": r0["path"],
            "dir": r0["dir"],
            "verdict": verdict,
            "text": f"{exp['arm']} {r0['current']}->{r0['value']}: {verdict} ({exp['protocol']}, "
            f"stats {json.dumps(stats, default=str)[:160]}{', ' + reason if reason else ''})",
        },
    )
    L["lanes"][lane].update(active=None, next_cycle_step=step_idx(L["pol"], now))


def lane_block_steps(ctx, lane):
    """v1.24: the switchback block length of a lane = the largest block_steps among its switchback keys."""
    fams = LANES.get(lane, ())
    return max(
        (e["block_steps"] for e in ctx.space if e["family"] in fams and e.get("protocol") == "switchback"),
        default=None,
    )


def reblock(ctx, exp):
    """v1.24 (2026-11-05, 성수님: improve overnight): a switchback experiment takes its lane's current block length
    (the largest block_steps among the lane's switchback keys) until its first block closes, so a shorter family
    setting also reaches experiments already scheduled or still in block 0. Nothing has been measured for a pair
    yet, so the pair statistics are untouched. -> the old block_steps when changed, else None."""
    if exp.get("protocol") != "switchback" or any("t1" in b for b in exp.get("blocks") or []):
        return None
    want = lane_block_steps(ctx, exp_lane(exp))
    if not want or want == exp["block_steps"] or want <= exp.get("washout_steps", 0):
        return None
    old, exp["block_steps"] = exp["block_steps"], want
    exp.setdefault("reblocked_from", old)
    if exp.get("blocks"):  # block 0 is open: same nominal end rule as the start branch of advance
        cur, blk_s = exp["blocks"][0], want * STEP_S
        t1 = exp["t_start"] + blk_s
        while t1 <= cur["t0"]:
            t1 += blk_s
        cur["t1_nom"] = t1
    return old


def null_arm(ctx, exp):
    """v1.25c: True when every edit's B value already equals its value in A_version, i.e. both arms are the same
    policy (an experiment proposed from a stale snapshot). An unreadable A_version or an absent path -> False."""
    try:
        a_pol = json.loads(ctx.runs.policy_bytes(exp["A_version"]))
    except Exception:  # noqa: BLE001
        return False
    for r in exp.get("edits") or []:
        v, ok = SP.get(a_pol, r["path"])
        if not ok or v != r["value"]:
            return False
    return bool(exp.get("edits"))


def harm(exp):
    """v1.25c: one-sided harm stop on the money the lane moves ("won": value or gross profit per hour for the shop,
    surplus per hour for the buyer) over complete pairs. -> stats {pairs, mean, sd, t, metric} when the mean pair
    difference B - A is negative with t < -LOSS_T after LOSS_MIN_PAIRS pairs, else None."""
    d = X.pair_diffs(exp.get("blocks") or [], key="won")
    if len(d) < LOSS_MIN_PAIRS:
        return None
    m = sum(d) / len(d)
    if m >= 0:
        return None
    sd = math.sqrt(sum((x - m) ** 2 for x in d) / (len(d) - 1))
    t = -math.inf if sd == 0 else m / (sd / math.sqrt(len(d)))
    if t >= -LOSS_T:
        return None
    return {"pairs": len(d), "mean": m, "sd": sd, "t": round(max(t, -1e9), 2), "metric": "won"}


def advance(ctx, L, exp, store, stats, now):
    pol = L["pol"]
    reblock(ctx, exp)
    if null_arm(ctx, exp):
        end(ctx, L, exp, "stopped", None, now, reason="null_arm: B equals A_version")
        return "null_arm"
    if "stars_base" not in exp:  # an experiment started before the ledger stars term
        exp["stars_base"] = L.get("stars_base")
    blk_s = exp["block_steps"] * STEP_S
    bd = {"sdir": ctx.sdir, "login": _login(pol)}
    if exp["status"] == "scheduled":
        if now < exp["t_start"]:
            return "scheduled"
        why = retired(pol, exp, stats)
        if why:
            end(ctx, L, exp, "stopped", None, now, reason=why)
            return "retired"
        m = measure(store, stats, exp, now - max(blk_s, LOSS_REF_S), now, **bd)
        exp["a_ref"] = m
        arm = "B" if exp["protocol"] == "long" else X.block_arm(exp["seed"], 0)
        put_arm(ctx, L, exp, arm, now)
        t1 = exp["t_start"] + blk_s
        while t1 <= now:
            t1 += blk_s
        exp["blocks"] = [{"i": 0, "arm": arm, "t0": now, "t1_nom": t1}]
        exp["status"] = "running"
        return "started"
    cur = exp["blocks"][-1]
    if cur["arm"] == "B" and now > cur["t0"]:
        if exp["side"] == "shop":
            # whole-shop value_h from the store over every A window and the current B block, so concurrent
            # shop-side B arms (price and stock lanes) are capped on their combined effect; not the verdict metric
            t_a = [(b["t0"], b["t1"]) for b in exp["blocks"] if b["arm"] == "A" and "t1" in b]
            if sum(t1 - t0 for t0, t1 in t_a) < LOSS_REF_S:  # v1.24b: short A history -> add the 1 h before block 0
                t0_ = exp["blocks"][0]["t0"]
                t_a = [(t0_ - max(blk_s, LOSS_REF_S), t0_)] + t_a
            a_won = [ST.block_metrics(store, t0, t1, stats)["value_h"] for t0, t1 in t_a]
            b_m = ST.block_metrics(store, cur["t0"], now, stats)
            short = (sum(a_won) / len(a_won) - b_m["value_h"]) * b_m["hours"]
        else:
            a_bl = [b for b in exp["blocks"] if b["arm"] == "A" and "won" in b]
            a_won = [b["won"] for b in a_bl]
            if sum(b.get("hours") or 0 for b in a_bl) * 3600 < LOSS_REF_S:  # v1.24b: same 1 h floor as the shop side
                a_won = [exp["a_ref"]["won"]] + a_won
            b_m = measure(store, stats, exp, cur["t0"], now)
            short = (sum(a_won) / len(a_won) - b_m["won"]) * b_m["hours"]
        bal = store.get("balance") or (pol.get("shop") or {}).get("balance_floor") or 0
        if short > LOSS_CAP_FRAC * bal:
            end(
                ctx,
                L,
                exp,
                "reject",
                None,
                now,
                reason=f"loss_cap shortfall {round(short)} > {LOSS_CAP_FRAC} x {bal}",
            )
            return "loss_cap"
    if exp.get("supply_up") and now >= ST.SUPPLY_STOP - ST.SUPPLY_BAN_S:
        end(ctx, L, exp, "reject", None, now, reason="supply_ban")
        return "supply_ban"
    if now < cur["t1_nom"]:
        return "running"
    wo = cur["t0"] + exp.get("washout_steps", 0) * STEP_S  # washout: the block's first steps are not measured
    cur.update(measure(store, stats, exp, min(wo, now - 1), now, **bd), t1=now, t0_measured=min(wo, now - 1))
    stars_guard(ctx, exp, cur, now)
    if exp["protocol"] == "long":
        prev = None
        if ST.covered(store, cur["t0"] - 86400):
            prev = measure(store, stats, exp, cur["t0"] - 86400, now - 86400, **bd)[
                "metric"
            ]
        v, st = X.long_verdict(cur["metric"], exp["a_ref"]["metric"], prev)
        end(ctx, L, exp, v, st, now)
        return v
    if cur["i"] % 2 == 1:
        hv = harm(exp)
        if hv:
            end(ctx, L, exp, "reject", hv, now, reason=f"harm t={hv['t']} over {hv['pairs']} pairs")
            return "harm"
        v, st = X.switchback_verdict(X.pair_diffs(exp["blocks"]), exp.get("retest"))
        exp["stats"] = st
        if v:
            end(ctx, L, exp, v, st, now)
            return v
    i = cur["i"] + 1
    arm = X.block_arm(exp["seed"], i)
    put_arm(ctx, L, exp, arm, now)
    t1 = cur["t1_nom"] + blk_s
    while t1 <= now:
        t1 += blk_s
    exp["blocks"].append({"i": i, "arm": arm, "t0": now, "t1_nom": t1})
    return "switched"


# ---------------------------------------------------------------- arms, prompts, LLM
def kind_of(cat, demand):
    out = {}
    for d in demand or []:
        out[d.get("category")] = d.get("kind")
    for _, (c, k, _) in cat.items():
        out.setdefault(c, k)
    return out


def top_cats(stats, n=N_CATS, by="settles_h"):
    """The n top market categories: by settles per hour, by="value" by settles per hour times the mean price, or
    by="ours" by our own settles in the stats window times the mean price (where our sales value is)."""

    def size(v):
        if by == "ours":
            return (v.get("our_n") or 0) * (v.get("mean") or 0)
        return (v.get("settles_h") or 0) * ((v.get("mean") or 0) if by == "value" else 1)

    return [c for c, _ in sorted(stats["category"].items(), key=lambda kv: -size(kv[1])) if c != "?"][:n]


def arm_cats(pol, stats):
    """v1.26d (2026-11-05): the categories that get category arms: the policy's overrides plus the top 5 categories
    by our own sales value. e0017 (kimchi breadth_qty, proposed 03:28 KST) came from the top 5 by settle count, where
    kimchi is 3rd (4th by market value), yet kimchi is 1 % of our sales value (03:36 KST: mattresses 58 %, sofas 31 %),
    an effect far below the noise of one 40-minute block."""
    ov = [c for c in (pol.get("categories") or {}) if "." not in c]
    return list(dict.fromkeys(ov + top_cats(stats, 5, by="ours")))


def retired(pol, exp, stats):
    """v1.26d: a scheduled experiment whose edits all name categories outside arm_cats -> the reason it is retired
    before it starts, else None. Only experiments that have not started are retired, so no data is lost."""
    edits = exp.get("edits") or []
    if exp.get("retest") or not edits or not all(r["path"].startswith("categories.") for r in edits):
        return None  # a retest re-checks an adopted change, wherever its category ranks now
    ok = set(arm_cats(pol, stats))
    out = [SP.split(r["path"])[1] for r in edits]
    return None if any(c in ok for c in out) else f"retired: {', '.join(out)} not among the arm categories"


def priors(ctx):
    d = _jload(WORK / "analysis" / "shop_goods_20261104.json", {}) or {}
    out = {}
    for c in d.get("action_key_candidates") or []:
        cur, prop = c.get("current"), c.get("proposed")
        if isinstance(prop, (int, float)) and prop != (cur or 0):
            out[f"{c['path']}|{'up' if prop > (cur or 0) else 'down'}"] = PRIOR_M
    return out


def arms(ctx, L, pol, stats, kinds, now, ban):
    out = {}
    cats = top_cats(stats, 5, by="value")  # v1.26d: list suggestions by market value, not settle count
    paths = [e["path"] for e in ctx.space if "*" not in e["path"]]
    for e in ctx.space:
        if e["path"].startswith("categories.*."):
            key = e["path"].rsplit(".", 1)[1]
            paths += [f"categories.{c}.{key}" for c in arm_cats(pol, stats)]
    for p in paths:
        e = SP.entry(ctx.space, p)
        cur = SP.current(pol, p, e, kinds)
        if e["type"] == "list":
            if any(c not in (cur or []) for c in cats):
                out[p + "|insert"] = {
                    "path": p,
                    "current": cur,
                    "suggest": [c for c in cats if c not in cur][:3],
                }
            if cur:
                out[p + "|remove"] = {"path": p, "current": cur}
            continue
        for d in ("up", "down"):
            v = SP.step_value(e, cur, d)
            if v is None or (ban and SP.supply_up(e, cur, v)):
                continue
            out[f"{p}|{d}"] = {
                "path": p,
                "current": cur,
                "suggest": v,
                "type": e["type"],
                "range": e.get("values") or [e.get("min"), e.get("max")],
                "max_step": e.get("max_step"),
                "max_ratio": e.get("max_ratio"),
                "protocol": e["protocol"],
                "block_steps": e["block_steps"],
            }
    for r in L.get("retests") or []:
        if r["due"] <= now:
            out[r["id"]] = {"retest_of": r["of"], "edits": r["edits"]}
    return out


def _memview(ctx):
    """Read-only look at the memory dir: (graph snapshot or None, lessons.jsonl rows, how many of those rows the
    memory stage has ingested; its cursor.json "lessons" must name this lessons file). A wrong-typed cursor reads
    as nothing ingested."""
    g = _jload(ctx.P["graph"], None)
    g = g if isinstance(g, dict) else None
    rows = _rows(ctx.f("lessons.jsonl"))
    cur = _jload(ctx.P["graph"].with_name("cursor.json"), {})
    cur = cur.get("lessons") if isinstance(cur, dict) else None
    cur = cur if isinstance(cur, dict) else {}
    n = cur.get("n")
    n = n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else 0
    same = cur.get("path") == str(ctx.f("lessons.jsonl").resolve())
    return g, rows, n if g and same else 0


def recall(ctx, cats, keys, at=None, mem=None, n_lessons=N_LESSONS, order="graph_first"):
    """Memory recall used by the proposer and the memcheck, read-only on the memory dir. Experiment knowledge is
    the graph's exp:<arm> verdict / policy:<path> value memos (memory/lessons.py): the relevant ones (path in
    `keys` or naming one of `cats`) first, then the most recent, n_lessons in all (order "recent_first": the most
    recent only, relevance ignored; sec 7 harness.json). lessons.jsonl rows past the
    memory stage's cursor (not ingested yet) are laid on top and listed as lessons. Plus buyer_cat edges on `cats`
    and other memos naming cats / keys. at: the view at that time (graph versions open at `at`, lessons with
    ts < at; no buyer_cat / other memos). mem: a _memview() result to reuse.
    -> {"edges", "lessons" (texts), "lesson_rows", "exp": {arm: verdict}, "policy": {path: value}}"""
    from memory import lessons as LS

    g, rows, n = mem or _memview(ctx)
    edges, view = [], {"exp": {}, "policy": {}}
    if g:
        try:
            view = LS.view((g.get("edges") or {}).get("memo"), at)
            if at is None:
                from memory.graph import Graph

                G = Graph.from_json(g)
                bc = G.active_edges("buyer_cat")
                bc = [e for e in (bc if isinstance(bc, list) else []) if e["b"] in cats]
                edges += [
                    {"buyer_cat": e["b"], "count": e["attrs"]["count"]}
                    for e in sorted(bc, key=lambda e: -e["attrs"]["count"])[:6]
                ]
                mm = G.active_edges("memo")
                edges += [
                    {"memo": e["key"][:80], "value": str(e["attrs"].get("value"))[:60]}
                    for e in (mm if isinstance(mm, list) else [])
                    if e["a"].partition(":")[0] not in LS.KINDS
                    and any(k in e["key"] for k in list(cats) + keys)
                ][:4]
        except Exception:  # noqa: BLE001 - recall is best effort
            pass

    def rel(name):
        return name.split("|")[0] in keys or any(c in name for c in cats)

    mrows = [("exp", a, v, t) for a, (v, t) in view["exp"].items()]
    mrows += [("policy", q, v, t) for q, (v, t) in view["policy"].items()]
    if order == "recent_first":
        mrows.sort(key=lambda x: (-(x[3] or 0), x[1]))
    else:
        mrows.sort(key=lambda x: (not rel(x[1]), -(x[3] or 0), x[1]))
    edges += [
        {k: name, ("verdict" if k == "exp" else "value"): str(v)[:60]}
        for k, name, v, _ in mrows[:n_lessons]
    ]
    exp = {a: v for a, (v, _) in view["exp"].items()}
    pol = {q: v for q, (v, _) in view["policy"].items()}
    new = [
        r
        for r in rows[n:]
        if at is None or (isinstance(r.get("ts"), (int, float)) and r["ts"] < at)
    ]
    ends = LS.ends_by_id(_rows(ctx.f("experiments.jsonl"))) if new else {}
    for r in new:
        f = LS.facts(r, ends)
        exp[f["exp"][len("exp:"):]] = f["verdict"]
        if f["policy"] and f["value_after"] is not None:
            pol[f["policy"][len("policy:"):]] = f["value_after"]
    return {
        "edges": edges,
        "lessons": [x.get("text") for x in new[-n_lessons:]],
        "lesson_rows": new[-n_lessons:],
        "exp": exp,
        "policy": pol,
    }


VOID_CAP = "void_old_cap"


def superseded_cap(row):
    """v1.26b (2026-11-05): True for an experiment end row stopped by a loss cap that the current LOSS_CAP_FRAC
    would not trip. The 2 % cap before v1.25c stopped e0010, e0011, e0012, e0014 and e0015 at noise level (stats
    None, so the posterior never saw them); the proposer is shown them as void, not as rejected."""
    parts = str(row.get("reason") or "").split()
    if parts[:2] != ["loss_cap", "shortfall"] or len(parts) < 7 or parts[3] != ">" or parts[5] != "x":
        return False
    try:
        return float(parts[2]) <= LOSS_CAP_FRAC * float(parts[6])
    except (IndexError, ValueError):
        return False


def prompt_memory(ctx, rec):
    """v1.26b: the MEMORY block the proposer reads. An arm whose latest experiment was a superseded loss-cap stop
    shows verdict void_old_cap, and that experiment's lesson says so; rec itself (memcheck reads it) is unchanged."""
    ends = [x for x in _rows(ctx.f("experiments.jsonl")) if x.get("event") == "end"]
    last = {}
    for x in ends:
        last[str(x.get("arm") or "").removeprefix("retest|")] = x
    void = {a for a, x in last.items() if superseded_cap(x)}
    ids = {x.get("id") for x in ends if superseded_cap(x)}
    edges = [
        dict(e, verdict=VOID_CAP) if e.get("exp") in void and "verdict" in e else e
        for e in rec["edges"]
    ]
    lessons = [
        t + " [void: the 2% loss cap was removed 02:52 KST 11-05; a noise-level stop, no evidence]"
        if isinstance(r, dict) and r.get("exp") in ids and isinstance(t, str)
        else t
        for r, t in zip(rec.get("lesson_rows") or [], rec["lessons"])
    ]
    return dict(rec, edges=edges, lessons=lessons if len(lessons) == len(rec["lessons"]) else rec["lessons"])


def exp_table(ctx, lane):
    out = []
    ends = [x for x in _rows(ctx.f("experiments.jsonl")) if x.get("event") == "end"]
    for r in [x for x in ends if lane_of_exp(ctx.space, x) == lane][-15:]:
        st = r.get("stats") or {}
        out.append(
            {
                "arm": r["arm"],
                "edits": [(e["path"], e["current"], e["value"]) for e in r["edits"]],
                "effect": st.get("mean", st.get("diff_prev_block")),
                "ci": [st.get("lo"), st.get("hi")],
                "pairs": st.get("pairs"),
                "verdict": VOID_CAP if superseded_cap(r) else r["verdict"],
            }
        )
    return out


def objective(lane, L):
    """v1.26 (2026-11-05): the score the experiments serve, built in code: the official formula, our player standing
    (rank, gaps) and, for the terms this lane's verdict metric moves, pct, rank of n, gap to the next value above and
    the score one rank step is worth. Empty board data -> the formula alone."""
    bs = L.get("board_summary") or {}
    terms = {
        t: {k: bs[t].get(k) for k in ("pct", "rank", "n", "gap_up", "per_rank")}
        for t in LANE_TERMS.get(lane, ())
        if isinstance(bs.get(t), dict)
    }
    return (
        "OBJECTIVE the final rank at round end of total = shop(0.5 profit + 0.3 revenue + 0.2 stars_rank) + "
        "buyer(0.5 surplus + 0.3 forum + 0.1 deals + 0.1 posts), each term a percentile among ranked players. "
        "profit counts cash (book - stock_cost): stock is a cost until it sells, and stock left at round end is lost. "
        f"This lane's verdict moves {', '.join(LANE_TERMS.get(lane, ()))}.\n"
        f"SCORE {json.dumps({'standing': L.get('board_standing'), 'lane_terms': terms}, ensure_ascii=False)}\n"
    )


def proposer_prompt(lane, S, offered, rec, table, notes="", objective=""):
    return (
        "You tune a live market agent's policy by small experiments. "
        f"This is the {lane} lane ({', '.join(LANES[lane])} keys); other lanes run their own experiments at the "
        "same time. Pick ONE of the offered arms (or hold). "
        'Reply with strict JSON only: {"action": "change"|"hold", "edits": [{"path": str, "value": ...}], '
        '"why": "<=200 chars"}. Up to 3 edits of this lane; one must be an offered arm\'s path in its direction; '
        "values within range and max step. For a retest arm, use its edits exactly.\n"
        + objective
        + f"LANE {lane}\n"
        f"STATE {json.dumps(S, ensure_ascii=False, default=str)}\n"
        f"EXPERIMENTS {json.dumps(table, ensure_ascii=False, default=str)}\n"
        f"MEMORY {json.dumps({'edges': rec['edges'], 'lessons': rec['lessons']}, ensure_ascii=False)}\n"
        f"ARMS {json.dumps(offered, ensure_ascii=False, default=str)}\n"
        + (f"NOTES (harness)\n{notes}\n" if notes else "")
    )


def harness(ctx, lane):
    """<state>/harness.json for this lane's side, read at the start of its cycle (sec 7) -> (version or None,
    settings, applied). The file key "lanes" is kept for compatibility and is keyed by side (shop: price and stock,
    buyer). Missing file, bad JSON or a bad version: defaults, version None. A missing or invalid side object, or
    any invalid field in it: that side's defaults (applied False). Unknown feature names are ignored; a features list
    with no known name means all features."""
    d = _jload(ctx.f("harness.json"), None)
    out = dict(HARNESS_DEFAULT)
    if not isinstance(d, dict):
        return None, out, False
    ver = d.get("version")
    if not isinstance(ver, int) or isinstance(ver, bool):
        return None, out, False
    x = d.get("lanes")
    x = x.get(LANE_SIDE.get(lane, lane)) if isinstance(x, dict) else None
    if not isinstance(x, dict):
        return ver, out, False
    n, rc, nt, ft = (x.get(k, out[k]) for k in ("n_lessons", "recall", "prompt_notes", "features"))
    ok = (
        isinstance(n, int)
        and not isinstance(n, bool)
        and N_LESSONS_RANGE[0] <= n <= N_LESSONS_RANGE[1]
        and rc in RECALL_ORDERS
        and isinstance(nt, str)
        and len(nt) <= NOTES_MAX
        and (ft is None or (isinstance(ft, list) and all(isinstance(f, str) for f in ft)))
    )
    if not ok:
        return ver, out, False
    ft = [f for f in ft if f in FEATURES] if ft is not None else None
    return ver, dict(n_lessons=n, recall=rc, prompt_notes=nt, features=ft or None), True


def parse_json(text):
    if not isinstance(text, str):
        return None
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        d = json.loads(text[a : b + 1])
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def parse_proposal(text):
    d = parse_json(text)
    if not d or d.get("action") not in ("change", "hold"):
        return None
    if d["action"] == "change":
        ed = d.get("edits")
        if (
            not isinstance(ed, list)
            or not 1 <= len(ed) <= SP.MAX_EDITS
            or not all(
                isinstance(x, dict) and isinstance(x.get("path"), str) and "value" in x
                for x in ed
            )
        ):
            return None
        d["edits"] = [{"path": x["path"], "value": x["value"]} for x in ed]
    from pii_mask import mask

    d["why"] = mask(str(d.get("why") or ""))[:200]
    return d


def ask(ctx, role, prompt, now):
    from pii_mask import mask

    t0 = time.monotonic()
    try:
        text, err = ctx.get_llms()[role].complete(prompt, max_tokens=800)["text"], None
    except Exception as e:  # noqa: BLE001 - LLMUnavailable or a client failure
        text, err = None, type(e).__name__
    out = parse_proposal(text)
    _append(
        ctx.f("llm.jsonl"),
        {
            "ts": now,
            "role": role,
            "sha256": sha(prompt.encode("utf-8")),
            "head": mask(prompt)[:80],
            "chars": len(prompt),
            "err": err,
            "valid": out is not None,
            "ms": int((time.monotonic() - t0) * 1000),
        },
    )
    return out


# ---------------------------------------------------------------- one decision cycle
def match_arm(offered, rows, edits):
    for aid, a in offered.items():
        if "retest_of" in a:
            if sorted(
                (e["path"], json.dumps(e["value"])) for e in a["edits"]
            ) == sorted((e["path"], json.dumps(e["value"])) for e in edits):
                return aid
            continue
        for r in rows:
            if aid == f"{r['path']}|{r['dir']}":
                return aid
    return None


def cycle(ctx, L, lane, store, stats, now, record=True):
    """One decision of one lane: select among the lane's arms, propose, gate and (record=True) schedule the
    experiment; the edits apply iff the code guards pass (no approver). record=False (the deploy check) leaves the
    experiment unrecorded and returns the gated rows in res["_rows"]."""
    pol = L["pol"]
    ls = L["lanes"][lane]
    hv, hc, happ = harness(ctx, lane)
    cat = ST.catalog(ctx.P)
    kinds = kind_of(cat, _jload(ctx.P["demand"], []))
    ban = now >= ST.SUPPLY_STOP - ST.SUPPLY_BAN_S
    k = step_idx(pol, now)
    allarms = {
        a: x
        for a, x in arms(ctx, L, pol, stats, kinds, now, ban).items()
        if arm_lane(ctx.space, x) == lane
    }
    pri = priors(ctx)
    post = _jload(ctx.f("arms.json"), {})
    seed = SEED + k + 1000 * list(LANES).index(lane)
    top = X.thompson(sorted(allarms), post, pri, seed)
    offered = {a: dict(allarms[a], posterior_mean=round(m, 3)) for a, _, m in top}
    cats = top_cats(stats)
    shop = ST.shop_summary(store, stats, now)
    S = {
        "time": ST.time_info(now),
        "shop": dict(shop, inventory=dict(list(shop["inventory"].items())[:10])),
        "buyer": ST.buyer_summary(store, now),
        "board": L.get("board_summary"),
        "market": {c: stats["category"][c] for c in cats},
        "budget": {
            "h_to_freeze": ST.time_info(now)["h_to_freeze"],
            "switchback_experiments_left": max(  # v1.24b: longest run = 12 pairs of this lane's blocks
                0, int(ST.time_info(now)["h_to_freeze"] // max(1e-9, X.LOOKS[-1] * 2 * lane_block_steps(ctx, lane) * STEP_S / 3600))
            ),
        },
    }
    if LANE_SIDE[lane] == "shop":
        S["stars_guard"] = guard_nums(ctx, [offered[a].get("path") for a in offered], now)
    S["lanes"] = {
        ln: {q: x.get(q) for q in ("id", "arm", "status", "protocol", "t_start")}
        for ln, x in running(L)
        if ln != lane
    }
    if hc["features"]:
        S = {q: v for q, v in S.items() if q in hc["features"]}
    rec = recall(
        ctx,
        cats,
        [a.split("|")[0] for a in offered],
        n_lessons=hc["n_lessons"],
        order=hc["recall"],
    )
    res = {
        "lane": lane,
        "side": LANE_SIDE[lane],
        "harness_version": hv,
        "harness_applied": happ,
        "seed": seed,
        "offered": list(offered),
        "guards": [],
        "_allarms": allarms,
    }
    prompt = proposer_prompt(
        lane, S, offered, prompt_memory(ctx, rec), exp_table(ctx, lane), hc["prompt_notes"], objective(lane, L)
    )
    prop = ask(ctx, "prop", prompt, now)
    res["proposer"] = None if prop is None else prop["action"]
    if prop is None or prop["action"] == "hold":
        ls["next_cycle_step"] = k + (HOLD_STEPS if prop else 1)
        if prop:
            res["why"] = prop.get("why")  # v1.26b: a hold costs HOLD_STEPS; keep its reason in the cycle log
        return res
    bad, rows = SP.check(
        prop["edits"], pol, ctx.space, stats["category"].keys(), kinds, ban
    )
    if not bad and any(lane_of_path(ctx.space, r["path"]) != lane for r in rows):
        bad = [f"edits outside the {lane} lane"]
    aid = match_arm(offered, rows, prop["edits"]) if not bad else None
    if not bad and aid is None:
        bad = ["edits match no offered arm"]
    if ls.get("active"):
        bad.append(f"an experiment is already running in the {lane} lane")
    from pii_mask import mask

    bad = [mask(str(b))[:200] for b in bad]
    res["guards"] = bad
    res["_rows"] = rows
    ls["next_cycle_step"] = k + 1
    if not record or bad:
        return res
    best = max(allarms, key=lambda a: X.posterior(post, a, pri.get(a, 0.0))[0])
    long_ = any(r["entry"]["protocol"] == "long" for r in rows)
    retest = aid.startswith("retest|")
    L["n_exp"] = L.get("n_exp", 0) + 1
    exp = {
        "id": f"e{L['n_exp']:04d}",
        "lane": lane,
        "arm": aid,
        "mode": "retest" if retest else ("exploit" if aid == best else "explore"),
        "retest": retest,
        "protocol": "switchback" if retest or not long_ else "long",
        "block_steps": max(r["entry"]["block_steps"] for r in rows),  # v1.24: the family's length, was 3 for switchback
        "washout_steps": max(r["entry"].get("washout_steps", 0) for r in rows),
        "outcome": "value"
        if any(r["entry"]["family"] in ("supply", "structure") for r in rows)
        else None,
        "seed": SEED * 1000 + L["n_exp"],
        "side": rows[0]["entry"]["side"],
        "prior": pri.get(aid, 0.0),
        "edits": [
            {k2: r[k2] for k2 in ("path", "current", "value", "dir")} for r in rows
        ],
        "supply_up": any(
            SP.supply_up(r["entry"], r["current"], r["value"]) for r in rows
        ),
        "t_start": boundary(pol, k + 1),
        "status": "scheduled",
        "blocks": [],
        "A_version": L["live_version"],
        "B_version": None,
        "w": L["w"],
        "stars_base": L.get("stars_base"),
        "why": prop["why"],
    }
    if retest:
        L["retests"] = [r for r in L.get("retests") or [] if r["id"] != aid]
    ls["active"] = exp
    _append(ctx.f("experiments.jsonl"), dict(event="start", **_exp_row(exp)))
    res["started"] = exp["id"]
    return res


# ---------------------------------------------------------------- memcheck
MEMCHECK_STYLE = "LongMemEval-style (Wu et al., ICLR 2025 abilities)"
MEMCHECK_TYPES = ("information_extraction", "knowledge_update", "temporal", "abstention")
N_ABSTAIN = 5
NO_LESSON = ("aborted_external_edit",)  # check_live ends these without a lessons row


def _same(a, b):
    if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (a, b)):
        return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))
    return a == b


def _policy_at(ctx, t, cache):
    """The registered policy live at t (the latest version with t_from <= t), parsed and cached; None before the
    first version."""
    if "_vs" not in cache:
        cache["_vs"] = [(ctx.runs.meta(v)["t_from"], v) for v in ctx.runs.versions()]
    hit = max((x for x in cache["_vs"] if x[0] <= t), default=None)
    if hit is None:
        return None
    if hit[1] not in cache:
        cache[hit[1]] = json.loads(ctx.runs.policy_bytes(hit[1]))
    return cache[hit[1]]


def memquestions(ctx, mem=None):
    """Code-built questions over the experiment record, answered by recall():
    [(type, question, gold, got)]. information_extraction: the latest verdict per arm (a retest counts as its
    arm); knowledge_update: the arms whose verdict changed on the last retest / rerun; temporal: a policy path's
    value 1 s after each experiment ended, gold from the PolicyRuns versions; abstention: up to N_ABSTAIN action
    space arms never tested (gold None). Experiments ended without a lessons row (NO_LESSON) are left out."""
    from memory.lessons import base_arm, scalar

    mem = mem or _memview(ctx)
    ends = [
        r
        for r in _rows(ctx.f("experiments.jsonl"))
        if r.get("event") == "end" and r.get("verdict") and r["verdict"] not in NO_LESSON
    ]
    hist = {}
    for r in ends:
        hist.setdefault(base_arm(r.get("arm")), []).append(r["verdict"])
    got = recall(ctx, [], [], mem=mem)["exp"]
    qs = []
    for a, vs in hist.items():
        qs.append(("information_extraction", f"latest verdict of {a}?", vs[-1], got.get(a)))
        if len(vs) > 1 and vs[-1] != vs[-2]:
            qs.append(("knowledge_update", f"verdict of {a} after its retest?", vs[-1], got.get(a)))
    cache, kinds = {}, None
    for r in ends:
        path, t = (r.get("edits") or [{}])[0].get("path"), r.get("t_end")
        e = SP.entry(ctx.space, path) if path else None
        pol = _policy_at(ctx, t + 1, cache) if isinstance(t, (int, float)) and e else None
        if pol is None:
            continue
        if kinds is None:
            kinds = kind_of(ST.catalog(ctx.P), _jload(ctx.P["demand"], []))
        gold = scalar(SP.current(pol, path, e, kinds))
        ans = recall(ctx, [], [], at=t + 1, mem=mem)["policy"].get(path)
        qs.append(("temporal", f"value of {path} at {t + 1}?", gold, ans))
    never = sorted(
        f"{e['path']}|{d}"
        for e in ctx.space
        if "*" not in e["path"]
        for d in (("insert", "remove") if e.get("type") == "list" else ("up", "down"))
        if f"{e['path']}|{d}" not in hist
    )
    for a in never[:N_ABSTAIN]:
        qs.append(("abstention", f"verdict of {a}?", None, got.get(a)))
    return qs


def memcheck(ctx, L, now):
    """Every MEMCHECK_S: memquestions() scored per type (no LLM) -> one memcheck.jsonl row."""
    if now - (L.get("last_memcheck") or 0) < MEMCHECK_S:
        return None
    L["last_memcheck"] = now
    mem = _memview(ctx)
    qs = memquestions(ctx, mem)
    types = {}
    for t in MEMCHECK_TYPES:
        ok = [_same(g, a) for k, _, g, a in qs if k == t]
        types[t] = {
            "n": len(ok),
            "correct": sum(ok),
            "acc": round(sum(ok) / len(ok), 3) if ok else None,
        }
    c = sum(x["correct"] for x in types.values())
    row = {
        "ts": now,
        "style": MEMCHECK_STYLE,
        "n": len(qs),
        "correct": c,
        "acc": round(c / len(qs), 3) if qs else None,
        "types": types,
        "unread_lessons": len(mem[1]) - mem[2],
        "misses": [[k, q] for k, q, g, a in qs if not _same(g, a)][:5],
    }
    _append(ctx.f("memcheck.jsonl"), row)
    return row


# ---------------------------------------------------------------- entry
@contextlib.contextmanager
def _locked(ctx):
    with open(ctx.f("loop.lock"), "a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def step(now=None, ctx=None, **kw):
    """One runner tick. Never raises: -> {"status": ok | stopped | frozen | busy | bad_live | error, ...}."""
    now = time.time() if now is None else now
    try:
        ctx = ctx or Ctx(**kw)
        with _locked(ctx) as ok:
            if not ok:
                return {"status": "busy"}
            return _step(ctx, now)
    except Exception as e:  # noqa: BLE001 - the runner must keep going
        try:
            log(
                ctx,
                now,
                "alarm",
                what="step_error",
                error=type(e).__name__,
                msg=str(e)[:200],
                tb=traceback.format_exc()[-600:],
            )
        except Exception:  # noqa: BLE001
            pass
        return {"status": "error", "error": type(e).__name__, "msg": str(e)[:160]}


def observe(ctx, L, pol, now):
    """State build + board refresh + weights and the stars base (into L). -> (store, stats, board status)."""
    cat = ST.catalog(ctx.P)
    own = pol.get("owner") or {}
    authors = {str(x).lower() for x in [own.get("github")] + list(own.get("own_buyer_names") or []) if x}
    store = ST.update(ST.load_store(ctx.f("store.json")), ctx.P, now, cat, authors)
    ST.save_store(ctx.f("store.json"), store)
    stats = ST.market_stats(store, cat, now)
    brd, bst = B.refresh(ctx.sdir, now, ctx.fetch)
    login = (pol.get("owner") or {}).get("github")
    n, s = store["rv_all"]
    L["stars_base"] = {"n": n, "avg": s / n, "src": "ledger"} if n else None
    me = B._ours((brd or {}).get("shops") or [], login) or {}
    if all(isinstance(me.get(q), (int, float)) for q in ("reviews", "stars")) and me["reviews"] > 0:
        L["stars_base"] = {"n": me["reviews"], "avg": me["stars"], "src": "board"}
    if brd:
        L["w"] = B.weights(brd, login)["w"]
        L["board_summary"] = B.summary(brd, login)
        L["board_standing"] = B.standing(brd, login)
    else:
        m = ST.block_metrics(store, now - 86400, now)
        L["w"] = B.fixed_weights(
            {
                "profit": m["gp_h"],
                "revenue": m["rev_h"],
                "surplus": m["surplus_h"],
                "deals": m["deals_h"],
                **B.term_scale(ctx.sdir, login, now),
            }
        )["w"]
        if bst.get("alarm"):
            log(
                ctx,
                now,
                "alarm",
                what="board_auth_fallback",
                code=bst.get("last_code"),
            )
    return store, stats, bst


def _step(ctx, now):
    L = _jload(ctx.f("loop.json"), {})
    lanes = migrate(ctx, L)
    out = {"status": "ok"}
    out["live"] = check_live(ctx, L, now)
    if out["live"] == "bad_live":  # keep the last good state; retry next tick
        _jsave(ctx.f("loop.json"), L)
        out["status"] = "bad_live"
        return out
    L["pol"] = pol = json.loads(ctx.policy.read_bytes())
    try:
        for ln in [x for x in lanes if x not in LANES]:  # a lane switched off (forum): end it like STOP, path-level
            if lanes[ln].get("active"):
                end(ctx, L, lanes[ln]["active"], "stopped", None, now, reason="lane_off")
                out.setdefault("lane_off", []).append(ln)
            del lanes[ln]
        if ctx.f("STOP").exists():
            for _, exp in running(L):
                end(ctx, L, exp, "stopped", None, now, reason="STOP file")
            out["status"] = "stopped"
            return out
        store, stats, bst = observe(ctx, L, pol, now)
        out["board"] = bst.get("last_code")
        if now >= ST.ROUND_END - ST.FREEZE_S:
            for _, exp in running(L):
                end(
                    ctx,
                    L,
                    exp,
                    "frozen",
                    None,
                    now,
                    reason="freeze 3 h before round end",
                )
            out["status"] = "frozen"
            return out
        for ln, exp in running(L):
            out.setdefault("exp", {})[ln] = advance(ctx, L, exp, store, stats, now)
        # v1.25c: an experiment that ended above restored its A values in the live file; the cycles below must see
        # those, not the snapshot taken at the start of the tick (02:14 KST: e0014/e0015 were proposed from it)
        L["pol"] = pol = json.loads(ctx.policy.read_bytes())
        k = step_idx(pol, now)
        for ln in LANES:
            s = lanes[ln]
            nxt = s.get("next_cycle_step")
            if s.get("active") or k < (k if nxt is None else nxt):
                continue
            s["next_cycle_step"] = k + 1                  # an exception below does not re-run the cycle this step
            res = cycle(ctx, L, ln, store, stats, now)
            pub = {k2: v for k2, v in res.items() if not k2.startswith("_")}
            out.setdefault("cycles", {})[ln] = pub
            log(ctx, now, "cycle", **{k2: v for k2, v in pub.items() if k2 != "offered"})
        if out.get("cycles"):
            out["cycle"] = {"proposer": {ln: r["proposer"] for ln, r in out["cycles"].items()}}
        out["memcheck"] = memcheck(ctx, L, now)
        out["meta"] = meta.step(ctx, now)  # every META_S; None when not due, never raises (sec 13)
        if running(L):
            out["active"] = {ln: {k2: x.get(k2) for k2 in ("id", "arm", "status")} for ln, x in running(L)}
        return out
    finally:
        L.pop("pol", None)
        _jsave(ctx.f("loop.json"), L)


# ---------------------------------------------------------------- one-off deploy check
def check(policy_out, now=None, ctx=None, **kw):
    """One full cycle of every lane against the real environment that writes ONLY the copy at policy_out: the state
    dir, the PolicyRuns registry and the audit log are a temporary directory, the live policy is only read. The first
    lane whose proposal passes the guards is written; when none does (holds or refused edits), the write path is
    still exercised with a synthetic in-bounds edit of an offered arm. -> (exit code, summary dict)."""
    import shutil
    import tempfile
    import policy as POL
    now = time.time() if now is None else now
    out = {"board_ok": False, "ledger_ok": False, "proposer_json_ok": False,
           "gate": None, "wrote_copy": False, "validate_ok": False}
    tmp = tempfile.mkdtemp(prefix="icrl-check-")
    try:
        from harness.runs import PolicyRuns
        ctx = ctx or Ctx(sdir=tmp, **kw)
        live = ctx.policy
        dst = Path(policy_out)
        shutil.copyfile(live, dst)
        ctx.sdir, ctx.policy = Path(tmp), dst
        ctx.runs, ctx.audit = PolicyRuns(Path(tmp) / "runs", Path(tmp) / "runs"), Path(tmp) / "audit.jsonl"
        L = {}
        migrate(ctx, L)
        check_live(ctx, L, now)
        L["pol"] = pol = json.loads(dst.read_bytes())
        store, stats, bst = observe(ctx, L, pol, now)
        out["board_ok"] = bst.get("last_code") == 200
        out["ledger_ok"] = bool(store["m"]) and bool(stats["category"])
        res = {ln: cycle(ctx, L, ln, store, stats, now, record=False) for ln in LANES}
        out["proposer"] = {ln: r.get("proposer") for ln, r in res.items()}
        out["proposer_json_ok"] = all(r.get("proposer") is not None for r in res.values())
        ok = [r for r in res.values() if r.get("proposer") == "change" and r.get("_rows") and not r["guards"]]
        rows = ok[0]["_rows"] if ok else []
        if ok:
            out["lane"] = ok[0]["lane"]
        else:
            cat = ST.catalog(ctx.P)
            kinds = kind_of(cat, _jload(ctx.P["demand"], []))
            allarms = {a: x for r in res.values() for a, x in r["_allarms"].items()}
            for aid, a in sorted(allarms.items()):
                if "suggest" in a and not isinstance(a["suggest"], list):
                    bad, rows = SP.check([{"path": a["path"], "value": a["suggest"]}], pol, ctx.space,
                                         stats["category"].keys(), kinds, False)
                    if rows:
                        out["synthetic"] = aid
                        break
        out["gate"] = "ok" if rows else "no in-bounds edit"
        if rows:
            raw = SP.dumps_like(dst.read_bytes(), SP.apply(pol, rows))
            write_policy(ctx, L, raw, "change", "icrl deploy check", now)
            out["wrote_copy"] = dst.read_bytes() == raw and raw != live.read_bytes()
            out["edits"] = [(r["path"], r["current"], r["value"]) for r in rows]
        POL.validate(json.loads(dst.read_bytes()))
        out["validate_ok"] = True
    except Exception as e:                               # noqa: BLE001 - reported, exit 1
        out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    keys = ("board_ok", "ledger_ok", "proposer_json_ok", "wrote_copy", "validate_ok")
    return (0 if all(out[k] for k in keys) and out["gate"] == "ok" else 1), out


def main(argv=None):
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="icrl one-off deploy check (writes only --policy-out)")
    ap.add_argument("--check", action="store_true", required=True)
    ap.add_argument("--policy-out", required=True)
    a = ap.parse_args(argv)
    sys.path.insert(0, str(WORK))
    code, out = check(a.policy_out)
    print(json.dumps(out, ensure_ascii=False, default=str), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
