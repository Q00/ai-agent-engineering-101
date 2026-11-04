"""icrl loop entry: step(now) -> status dict, never raises (DESIGN.md sec 2, 8, 9, 11).

Called by the runner every ~600 s. Acts on 20-min block boundaries by time (grid = policy volume.round_start_ts,
the first supply window opens_at, + k x 1200 s, shifted BOUNDARY_LEAD_S earlier so a switch lands before the supply
window it is meant for), idempotent across ticks and restarts: every bit of state is a file under the state dir.

Write path: policy_gate.check_writer("rlpo", live) -> PolicyRuns.register -> harness.rollback._atomic. Not
harness.rollback.apply_proposal: its harness.bounds whitelist refuses shop.*, buyer.max_shops, enums, lists and
category overrides, and its lock.verify / single-registry check do not fit an A/B switch every hour.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import time
import traceback
from pathlib import Path

from icrl import board as B
from icrl import experiment as X
from icrl import space as SP
from icrl import state as ST

HERE = Path(__file__).resolve().parent
WORK = HERE.parent
STEP_S = 1200
BOUNDARY_LEAD_S = 600
HOLD_STEPS = 3
LOSS_CAP_FRAC = 0.02
RETEST_AFTER_S = 86400
MEMCHECK_S = 3600  # 성수님 2026-11-04: memory recall check every hour (was daily)
SEED = 20261104
N_LESSONS = 20
N_CATS = 8
PRIOR_M = 0.3
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
            self.llms = {
                "prop": RoleLLM(live, "rlpo_prop"),
                "appr": RoleLLM(live, "rlpo_appr"),
            }
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
    try:
        return [
            json.loads(x)
            for x in Path(p).read_text(encoding="utf-8").splitlines()
            if x.strip()
        ]
    except (OSError, ValueError):
        return []


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


def arm_bytes(ctx, exp, arm):
    a_raw = ctx.runs.policy_bytes(exp["A_version"])
    if arm == "A":
        return a_raw
    return SP.dumps_like(a_raw, SP.apply(json.loads(a_raw), exp["edits"]))


def put_arm(ctx, L, exp, arm, now):
    raw = arm_bytes(ctx, exp, arm)
    if sha(raw) == L["live_sha"]:
        return False
    v = write_policy(
        ctx,
        L,
        raw,
        "change" if arm == "B" else "rollback",
        f"icrl {exp['id']} arm {arm}",
        now,
    )
    if arm == "B" and exp.get("B_version") is None:
        exp["B_version"] = v
    return True


def check_live(ctx, L, now):
    """Registers the first / externally edited live bytes as a baseline; aborts a running experiment on an edit."""
    raw = ctx.policy.read_bytes()
    h = sha(raw)
    if L.get("live_sha") == h:
        return None
    first = L.get("live_sha") is None
    v = ctx.runs.register(
        raw,
        now,
        "baseline",
        parent=L.get("live_version"),
        note="initial" if first else "external",
    )
    L.update(live_version=v, live_sha=h, base_version=v)
    if first:
        return None
    exp = L.get("active")
    if exp:
        exp.update(status="done", verdict="aborted_external_edit", t_end=now)
        _append(ctx.f("experiments.jsonl"), dict(event="end", **_exp_row(exp)))
        L["active"] = None
    log(ctx, now, "alarm", what="external_edit", version=v, aborted=bool(exp))
    return "external"


# ---------------------------------------------------------------- metrics
def proxy(m, exp):
    w = exp["w"]
    if exp["side"] == "buyer":
        return w["surplus"] * m["surplus_h"] + w["deals"] * m["deals_h"]
    if exp["protocol"] == "long":
        return m["value_h"]
    return w["profit"] * m["gp_h"] + w["revenue"] * m["rev_h"]


def won(m, exp):
    if exp["side"] == "buyer":
        return m["surplus_h"]
    return m["value_h"] if exp["protocol"] == "long" else m["gp_h"]


def measure(store, stats, exp, t0, t1):
    m = ST.block_metrics(store, t0, t1, stats if exp["protocol"] == "long" else None)
    return {"metric": proxy(m, exp), "won": won(m, exp), "hours": m["hours"]}


# ---------------------------------------------------------------- experiment engine
def _exp_row(exp):
    keep = (
        "id",
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
    )
    return dict(
        {k: exp.get(k) for k in keep},
        edits=[
            {k: r[k] for k in ("path", "current", "value", "dir")} for r in exp["edits"]
        ],
        blocks=len(exp["blocks"]),
    )


def end(ctx, L, exp, verdict, stats, now, reason=None):
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
            "arm": exp["arm"],
            "path": r0["path"],
            "dir": r0["dir"],
            "verdict": verdict,
            "text": f"{exp['arm']} {r0['current']}->{r0['value']}: {verdict} ({exp['protocol']}, "
            f"stats {json.dumps(stats, default=str)[:160]}{', ' + reason if reason else ''})",
        },
    )
    L["active"] = None
    L["next_cycle_step"] = step_idx(L["pol"], now)


def advance(ctx, L, store, stats, now):
    exp = L["active"]
    pol = L["pol"]
    blk_s = exp["block_steps"] * STEP_S
    if exp["status"] == "scheduled":
        if now < exp["t_start"]:
            return "scheduled"
        m = measure(store, stats, exp, now - blk_s, now)
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
        a_won = [b["won"] for b in exp["blocks"] if b["arm"] == "A" and "won" in b] or [
            exp["a_ref"]["won"]
        ]
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
    cur.update(measure(store, stats, exp, cur["t0"], now), t1=now)
    if exp["protocol"] == "long":
        prev = None
        if ST.covered(store, cur["t0"] - 86400):
            prev = measure(store, stats, exp, cur["t0"] - 86400, now - 86400)["metric"]
        v, st = X.long_verdict(cur["metric"], exp["a_ref"]["metric"], prev)
        end(ctx, L, exp, v, st, now)
        return v
    if cur["i"] % 2 == 1:
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


def top_cats(stats, n=N_CATS):
    return [
        c
        for c, _ in sorted(
            stats["category"].items(), key=lambda kv: -kv[1]["settles_h"]
        )
        if c != "?"
    ][:n]


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
    cats = top_cats(stats, 5)
    paths = [e["path"] for e in ctx.space if "*" not in e["path"]]
    for e in ctx.space:
        if e["path"].startswith("categories.*."):
            key = e["path"].rsplit(".", 1)[1]
            ov = [c for c in (pol.get("categories") or {}) if "." not in c]
            paths += [f"categories.{c}.{key}" for c in dict.fromkeys(ov + cats)]
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


def recall(ctx, cats, keys):
    """Memory recall used by the proposer and the memcheck: graph edges on these categories (read-only) + the last
    N_LESSONS lessons."""
    edges = []
    g = _jload(ctx.P["graph"], None)
    if g:
        try:
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
                if any(k in e["key"] for k in list(cats) + keys)
            ][:4]
        except Exception:  # noqa: BLE001 - recall is best effort
            pass
    return {
        "edges": edges,
        "lessons": [x.get("text") for x in _rows(ctx.f("lessons.jsonl"))[-N_LESSONS:]],
        "lesson_rows": _rows(ctx.f("lessons.jsonl"))[-N_LESSONS:],
    }


def exp_table(ctx):
    out = []
    for r in [x for x in _rows(ctx.f("experiments.jsonl")) if x.get("event") == "end"][
        -15:
    ]:
        st = r.get("stats") or {}
        out.append(
            {
                "arm": r["arm"],
                "edits": [(e["path"], e["current"], e["value"]) for e in r["edits"]],
                "effect": st.get("mean", st.get("diff_prev_block")),
                "ci": [st.get("lo"), st.get("hi")],
                "pairs": st.get("pairs"),
                "verdict": r["verdict"],
            }
        )
    return out


def proposer_prompt(S, offered, rec, table):
    return (
        "You tune a live market agent's policy by small experiments. Pick ONE of the offered arms (or hold). "
        'Reply with strict JSON only: {"action": "change"|"hold", "edits": [{"path": str, "value": ...}], '
        '"why": "<=200 chars"}. Up to 3 edits of one side; one must be an offered arm\'s path in its direction; '
        "values within range and max step. For a retest arm, use its edits exactly.\n"
        f"STATE {json.dumps(S, ensure_ascii=False, default=str)}\n"
        f"EXPERIMENTS {json.dumps(table, ensure_ascii=False, default=str)}\n"
        f"MEMORY {json.dumps({'edges': rec['edges'], 'lessons': rec['lessons']}, ensure_ascii=False)}\n"
        f"ARMS {json.dumps(offered, ensure_ascii=False, default=str)}\n"
    )


def approver_prompt(nums):
    return (
        "Approve or reject this policy experiment from the numbers alone. Reply with strict JSON only: "
        '{"decision": "approve"|"reject", "reason": "<=80 chars"}.\n'
        f"INPUT {json.dumps(nums, ensure_ascii=False, default=str)}\n"
    )


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


def parse_approval(text):
    d = parse_json(text)
    if not d or d.get("decision") not in ("approve", "reject"):
        return None
    from pii_mask import mask

    return {"decision": d["decision"], "reason": mask(str(d.get("reason") or ""))[:80]}


def ask(ctx, role, prompt, now):
    from pii_mask import mask

    t0 = time.monotonic()
    try:
        text, err = ctx.get_llms()[role].complete(prompt, max_tokens=800)["text"], None
    except Exception as e:  # noqa: BLE001 - LLMUnavailable or a client failure
        text, err = None, type(e).__name__
    out = (parse_proposal if role == "prop" else parse_approval)(text)
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


def cycle(ctx, L, store, stats, now, record=True):
    """One decision: select, propose, gate, approve and (record=True) schedule the experiment. record=False (the
    deploy check) leaves the experiment unrecorded and returns the gated rows in res["_rows"]."""
    pol = L["pol"]
    cat = ST.catalog(ctx.P)
    kinds = kind_of(cat, _jload(ctx.P["demand"], []))
    ban = now >= ST.SUPPLY_STOP - ST.SUPPLY_BAN_S
    k = step_idx(pol, now)
    allarms = arms(ctx, L, pol, stats, kinds, now, ban)
    pri = priors(ctx)
    post = _jload(ctx.f("arms.json"), {})
    seed = SEED + k
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
            "switchback_experiments_left": max(
                0, int(ST.time_info(now)["h_to_freeze"] // 24)
            ),
        },
    }
    rec = recall(ctx, cats, [a.split("|")[0] for a in offered])
    res = {"seed": seed, "offered": list(offered), "_allarms": allarms}
    prop = ask(ctx, "prop", proposer_prompt(S, offered, rec, exp_table(ctx)), now)
    res["proposer"] = None if prop is None else prop["action"]
    if prop is None or prop["action"] == "hold":
        res["approver"] = "skipped"
        L["next_cycle_step"] = k + (HOLD_STEPS if prop else 1)
        return res
    bad, rows = SP.check(
        prop["edits"], pol, ctx.space, stats["category"].keys(), kinds, ban
    )
    aid = match_arm(offered, rows, prop["edits"]) if not bad else None
    if not bad and aid is None:
        bad = ["edits match no offered arm"]
    if L.get("active"):
        bad.append("an experiment is already running")
    res["guards"] = bad
    ap = None
    res["_rows"] = rows
    if not bad:
        best = (
            max(allarms, key=lambda a: X.posterior(post, a, pri.get(a, 0.0))[0])
            if allarms
            else None
        )
        nums = {
            "edits": [
                {"path": r["path"], "current": r["current"], "proposed": r["value"]}
                for r in rows
            ]
            or [{"path": e["path"], "proposed": e["value"]} for e in prop["edits"]],
            "arm": aid,
            "mode": "retest"
            if aid and aid.startswith("retest|")
            else ("exploit" if aid == best else "explore"),
            "offline_prior": pri.get(aid),
            "guards": {"ok": not bad, "violations": len(bad)},
            "last24h": dict(
                ST.buyer_summary(store, now),
                rev_h=shop["rev_h"],
                gp_h=shop["gp_h"],
                balance=shop["balance"],
            ),
            "gamma": ST.time_info(now)["gamma"],
            "t_rem_h": ST.time_info(now)["t_rem_h"],
        }
        ap = ask(ctx, "appr", approver_prompt(nums), now)
        res["_nums"] = nums
    res["approver"] = None if ap is None else ap["decision"]
    L["next_cycle_step"] = k + 1
    if not record or bad or ap is None or ap["decision"] != "approve":
        return res
    long_ = any(r["entry"]["protocol"] == "long" for r in rows)
    retest = aid.startswith("retest|")
    L["n_exp"] = L.get("n_exp", 0) + 1
    exp = {
        "id": f"e{L['n_exp']:04d}",
        "arm": aid,
        "mode": nums["mode"],
        "retest": retest,
        "protocol": "switchback" if retest or not long_ else "long",
        "block_steps": 3
        if retest or not long_
        else max(r["entry"]["block_steps"] for r in rows),
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
        "why": prop["why"],
    }
    if retest:
        L["retests"] = [r for r in L.get("retests") or [] if r["id"] != aid]
    L["active"] = exp
    _append(ctx.f("experiments.jsonl"), dict(event="start", **_exp_row(exp)))
    res["started"] = exp["id"]
    return res


# ---------------------------------------------------------------- memcheck
def memcheck(ctx, L, now):
    if now - (L.get("last_memcheck") or 0) < MEMCHECK_S:
        return None
    L["last_memcheck"] = now
    truth = {}
    for r in _rows(ctx.f("experiments.jsonl")):
        if r.get("event") == "end" and r.get("verdict"):
            truth[r["arm"]] = r["verdict"]
    rec = recall(ctx, [], [])
    got = {}
    for x in rec["lesson_rows"]:
        got[x.get("arm")] = x.get("verdict")
    qs = [
        {
            "q": f"verdict of experiment on {a.replace('|', ' ')}?",
            "a": v,
            "got": got.get(a),
        }
        for a, v in truth.items()
    ]
    row = {
        "ts": now,
        "n": len(qs),
        "correct": sum(q["a"] == q["got"] for q in qs),
        "acc": round(sum(q["a"] == q["got"] for q in qs) / len(qs), 3) if qs else None,
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
    """One runner tick. Never raises: -> {"status": ok | stopped | frozen | busy | error, ...}."""
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
    """State build + board refresh + weights (into L). -> (store, stats, board status)."""
    cat = ST.catalog(ctx.P)
    store = ST.update(ST.load_store(ctx.f("store.json")), ctx.P, now, cat)
    ST.save_store(ctx.f("store.json"), store)
    stats = ST.market_stats(store, cat, now)
    brd, bst = B.refresh(ctx.sdir, now, ctx.fetch)
    login = (pol.get("owner") or {}).get("github")
    if brd:
        L["w"] = B.weights(brd, login)["w"]
        L["board_summary"] = B.summary(brd, login)
    else:
        m = ST.block_metrics(store, now - 86400, now)
        L["w"] = B.fixed_weights(
            {
                "profit": m["gp_h"],
                "revenue": m["rev_h"],
                "surplus": m["surplus_h"],
                "deals": m["deals_h"],
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
    out = {"status": "ok"}
    out["live"] = check_live(ctx, L, now)
    L["pol"] = pol = json.loads(ctx.policy.read_bytes())
    try:
        if ctx.f("STOP").exists():
            if L.get("active"):
                end(ctx, L, L["active"], "stopped", None, now, reason="STOP file")
            out["status"] = "stopped"
            return out
        store, stats, bst = observe(ctx, L, pol, now)
        out["board"] = bst.get("last_code")
        if now >= ST.ROUND_END - ST.FREEZE_S:
            if L.get("active"):
                end(
                    ctx,
                    L,
                    L["active"],
                    "frozen",
                    None,
                    now,
                    reason="freeze 3 h before round end",
                )
            out["status"] = "frozen"
            return out
        if L.get("active"):
            out["exp"] = advance(ctx, L, store, stats, now)
        k = step_idx(pol, now)
        if not L.get("active") and k >= L.get("next_cycle_step", k):
            L["next_cycle_step"] = k + 1                  # an exception below does not re-run the cycle this step
            res = cycle(ctx, L, store, stats, now)
            out["cycle"] = {k2: v for k2, v in res.items() if not k2.startswith("_")}
            log(ctx, now, "cycle", **{k2: v for k2, v in out["cycle"].items() if k2 != "offered"})
        out["memcheck"] = memcheck(ctx, L, now)
        if L.get("active"):
            out["active"] = {k2: L["active"].get(k2) for k2 in ("id", "arm", "status")}
        return out
    finally:
        L.pop("pol", None)
        _jsave(ctx.f("loop.json"), L)


# ---------------------------------------------------------------- one-off deploy check
def check(policy_out, now=None, ctx=None, **kw):
    """One full cycle against the real environment that writes ONLY the copy at policy_out: the state dir, the
    PolicyRuns registry and the audit log are a temporary directory, the live policy is only read. A proposer hold
    (or a refused / rejected edit) still exercises the write path with a synthetic in-bounds edit of an offered arm.
    -> (exit code, summary dict)."""
    import shutil
    import tempfile
    import policy as POL
    now = time.time() if now is None else now
    out = {"board_ok": False, "ledger_ok": False, "proposer_json_ok": False, "approver_json_ok": False,
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
        check_live(ctx, L, now)
        L["pol"] = pol = json.loads(dst.read_bytes())
        store, stats, bst = observe(ctx, L, pol, now)
        out["board_ok"] = bst.get("last_code") == 200
        out["ledger_ok"] = bool(store["m"]) and bool(stats["category"])
        res = cycle(ctx, L, store, stats, now, record=False)
        out["proposer"] = res.get("proposer")
        out["proposer_json_ok"] = res.get("proposer") is not None
        rows, nums = res.get("_rows") or [], res.get("_nums")
        approved = res.get("approver") == "approve" and not res.get("guards")
        if not (rows and approved):
            cat = ST.catalog(ctx.P)
            kinds = kind_of(cat, _jload(ctx.P["demand"], []))
            rows = []
            for aid, a in sorted(res["_allarms"].items()):
                if "suggest" in a and not isinstance(a["suggest"], list):
                    bad, rows = SP.check([{"path": a["path"], "value": a["suggest"]}], pol, ctx.space,
                                         stats["category"].keys(), kinds, False)
                    if rows:
                        out["synthetic"] = aid
                        break
            nums = {"edits": [{"path": r["path"], "current": r["current"], "proposed": r["value"]} for r in rows],
                    "arm": out.get("synthetic"), "mode": "check", "guards": {"ok": bool(rows), "violations": 0},
                    "gamma": ST.time_info(now)["gamma"], "t_rem_h": ST.time_info(now)["t_rem_h"]}
            ap = ask(ctx, "appr", approver_prompt(nums), now)
            out["approver"] = None if ap is None else ap["decision"]
        else:
            out["approver"] = res.get("approver")
        out["approver_json_ok"] = out["approver"] is not None
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
    keys = ("board_ok", "ledger_ok", "proposer_json_ok", "approver_json_ok", "wrote_copy", "validate_ok")
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
