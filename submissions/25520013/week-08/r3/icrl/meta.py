"""Meta-harness outer loop over the icrl proposers (Lee et al., "Meta-Harness", arXiv 2603.28052; DESIGN.md sec 13).

Every META_S the outer loop judges the harness (what each lane's proposer recalls and sees) of ONE lane and either
reverts that lane to its best recorded config or asks the LLM (role "meta") for one new config of that lane.
Judged by harness-quality metrics with enough samples in 6 h (memcheck accuracy, proposal validity), not by the
experiment score (experiments last 3-24 h). Running experiments are measured by code and are unaffected.

Writes only <state dir>/harness.json (atomic) and files under <state dir>/meta/. step() never raises.
"""

import hashlib
import json
import math
import os
import time
import traceback
from pathlib import Path

META_S = 10800  # 성수님 2026-11-04: run every 3 h (was 6 h); the sample floors below still gate every judgement
LANES = ("shop", "buyer", "forum")
RECALLS = ("graph_first", "recent_first")
N_LESSONS_RANGE = (5, 40)
NOTES_MAX = 600
# The proposer prompt's STATE keys in loop.cycle() today; the loop ignores names it does not know.
FEATURES = ("time", "shop", "buyer", "board", "market", "budget", "stars_guard", "lanes")  # = loop.FEATURES
DEFAULT_LANE = {
    "n_lessons": 20,
    "recall": "graph_first",
    "prompt_notes": "",
    "features": list(FEATURES),
}
W_MEM, W_VALID = 0.5, 0.5  # score = 0.5 memcheck accuracy + 0.5 lane proposal validity
MIN_MEM_ROWS = 4  # memcheck runs hourly: at least 4 h of checks since the lane's config changed
MIN_DECISIONS = (
    4  # per lane: a lane cycles only between experiments, ~1-6 decisions per 6 h
)
MARGIN_MIN = 0.05  # revert floor when both binomial SEs are ~0 (rates at 0 or 1)
Z_ONE_SIDED = 1.645  # one-sided 95 % on the score difference
N_EXCERPTS = 5


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
    """jsonl rows that parse to dicts; a garbage line is skipped, not the whole file."""
    try:
        lines = Path(p).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for x in lines:
        try:
            d = json.loads(x)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _csha(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode("utf-8")).hexdigest()[
        :16
    ]


# ---------------------------------------------------------------- harness config
def check_lane(d):
    """A lane config from the LLM or harness.json -> (normalized config, None) or (None, reason). Missing keys
    are invalid; unknown features are dropped; prompt_notes is PII-masked."""
    from pii_mask import mask

    if not isinstance(d, dict):
        return None, "not an object"
    n = d.get("n_lessons")
    if not (isinstance(n, int) and not isinstance(n, bool)):
        return None, "n_lessons not int"
    if not N_LESSONS_RANGE[0] <= n <= N_LESSONS_RANGE[1]:
        return None, "n_lessons out of range"
    if d.get("recall") not in RECALLS:
        return None, "recall not allowed"
    notes = d.get("prompt_notes")
    if not isinstance(notes, str):
        return None, "prompt_notes not str"
    if len(notes) > NOTES_MAX:
        return None, "prompt_notes too long"
    fs = d.get("features")
    if not isinstance(fs, list):
        return None, "features not list"
    fs = [
        f for f in dict.fromkeys(x for x in fs if isinstance(x, str)) if f in FEATURES
    ]
    if not fs:
        return None, "no known features"
    return {
        "n_lessons": n,
        "recall": d["recall"],
        "prompt_notes": mask(notes)[:NOTES_MAX],
        "features": fs,
    }, None


def load_harness(sdir):
    """-> (version, {lane: config}); a missing or invalid lane object falls back to the defaults (as the loop)."""
    h = _jload(Path(sdir) / "harness.json", None)
    h = h if isinstance(h, dict) else {}
    v = h.get("version")
    v = v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0
    raw = h.get("lanes") if isinstance(h.get("lanes"), dict) else {}
    lanes = {}
    for ln in LANES:
        cfg, _ = check_lane(raw.get(ln))
        lanes[ln] = cfg or json.loads(json.dumps(DEFAULT_LANE))
    return v, lanes


# ---------------------------------------------------------------- quality score
def lane_quality(sdir, lane, since, lane_version, now):
    """Harness quality of one lane over [since, now] from memcheck.jsonl and the loop's cycle rows."""
    sdir = Path(sdir)
    mem = [
        r
        for r in _rows(sdir / "memcheck.jsonl")
        if _num(r.get("ts"))
        and since <= r["ts"] <= now
        and _num(r.get("acc"))
        and _num(r.get("n"))
        and r["n"] > 0
    ]
    q = sum(r["n"] for r in mem)
    correct = sum(
        r["correct"] if _num(r.get("correct")) else r["acc"] * r["n"] for r in mem
    )
    acc = correct / q if q else None
    n_mem_eff = (
        q / len(mem) if mem else 0
    )  # the same questions recur hourly: questions per check, not the sum
    dec, valid, holds, guard_ex = 0, 0, 0, []
    for r in _rows(sdir / "log.jsonl"):
        if r.get("ev") != "cycle" or r.get("lane") != lane or not _num(r.get("ts")):
            continue
        if not since <= r["ts"] <= now:
            continue
        hv = r.get("harness_version")
        if _num(hv) and hv < lane_version:
            continue
        p = r.get("proposer")
        if p not in ("change", "hold"):
            continue  # None = LLM outage or unparsable reply; not a decision
        dec += 1
        g = r.get("guards")
        g = g if isinstance(g, list) else ([g] if g else [])
        if p == "hold":
            holds += 1
        elif not g:
            valid += 1
        else:
            guard_ex += [str(x)[:200] for x in g]
    val = valid / dec if dec else None
    enough = len(mem) >= MIN_MEM_ROWS and dec >= MIN_DECISIONS
    score = se = None
    if enough:
        score = W_MEM * acc + W_VALID * val
        se = math.sqrt(
            (W_MEM**2) * acc * (1 - acc) / max(n_mem_eff, 1)
            + (W_VALID**2) * val * (1 - val) / dec
        )
    return {
        "score": None if score is None else round(score, 4),
        "se": None if se is None else round(se, 4),
        "mem_acc": None if acc is None else round(acc, 4),
        "validity": None if val is None else round(val, 4),
        "hold_rate": round(holds / dec, 4) if dec else None,
        "samples": {
            "memcheck_rows": len(mem),
            "mem_questions_per_check": round(n_mem_eff, 2),
            "decisions": dec,
        },
        "enough": enough,
        "_guards": guard_ex[-N_EXCERPTS:],
    }


def revert_margin(se_cur, se_best):
    return max(
        MARGIN_MIN, Z_ONE_SIDED * math.sqrt((se_cur or 0) ** 2 + (se_best or 0) ** 2)
    )


# ---------------------------------------------------------------- LLM
def meta_prompt(lane, cur, history, misses, guards):
    return (
        "You improve the harness of a live market agent's experiment proposer for the lane "
        f"'{lane}': what it recalls from memory and which state it sees. Quality = 0.5 memory recall accuracy "
        "+ 0.5 share of proposals that are changes passing the code guards. Propose ONE new config for this lane. "
        'Reply with strict JSON only: {"n_lessons": int 5-40, "recall": "graph_first"|"recent_first", '
        f'"prompt_notes": str <= {NOTES_MAX} chars (advice shown to the proposer), "features": [names], '
        '"why": "<=200 chars"}.\n'
        f"FEATURES {json.dumps(list(FEATURES))}\n"
        f"CURRENT {json.dumps(cur, ensure_ascii=False)}\n"
        f"CANDIDATES {json.dumps(history, ensure_ascii=False)}\n"
        f"MEMCHECK_MISSES {json.dumps(misses, ensure_ascii=False)}\n"
        f"GUARD_REASONS {json.dumps(guards, ensure_ascii=False)}\n"
    )


def _ask(mdir, llm, prompt, now):
    from pii_mask import mask

    t0 = time.monotonic()
    try:
        text, err = llm.complete(prompt, max_tokens=800)["text"], None
    except Exception as e:  # noqa: BLE001 - LLMUnavailable or a client failure
        text, err = None, type(e).__name__
    d = None
    if isinstance(text, str):
        a, b = text.find("{"), text.rfind("}")
        if 0 <= a < b:
            try:
                d = json.loads(text[a : b + 1])
            except ValueError:
                d = None
    cfg, why = check_lane(d)
    _append(
        mdir / "llm.jsonl",
        {
            "ts": now,
            "role": "meta",
            "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "head": mask(prompt)[:80],
            "chars": len(prompt),
            "err": err,
            "valid": cfg is not None,
            "ms": int((time.monotonic() - t0) * 1000),
        },
    )
    if cfg is None:
        return None, err or why
    return cfg, mask(str(d.get("why") or ""))[:200]


def _default_llm(ctx):
    llms = ctx.get_llms()
    return llms.get("meta") or llms["prop"]


# ---------------------------------------------------------------- one run
def _misses(sdir, since):
    from pii_mask import mask

    out = []
    for r in _rows(Path(sdir) / "memcheck.jsonl"):
        if _num(r.get("ts")) and r["ts"] >= since and isinstance(r.get("misses"), list):
            out += [mask(str(m))[:200] for m in r["misses"]]
    return out[-N_EXCERPTS:]


def _run(ctx, now, llm, mdir, st):
    sdir = Path(ctx.sdir)
    version, lanes = load_harness(sdir)
    cands = _rows(mdir / "candidates.jsonl")
    known = [version] + [r["version"] for r in cands if _num(r.get("version"))]
    ls = st.setdefault("lanes", {})
    out = {"version": version, "lanes": {}}
    for ln in LANES:  # a lane whose config changed outside meta (or first seen) starts a new window now
        s = ls.get(ln)
        if (
            not isinstance(s, dict)
            or s.get("cfg_sha") != _csha(lanes[ln])
            or not _num(s.get("since"))
        ):
            ls[ln] = {"since": now, "version": version, "cfg_sha": _csha(lanes[ln])}
    quals = {}
    for ln in LANES:
        s = ls[ln]
        lv = s["version"] if _num(s.get("version")) else version
        q = lane_quality(sdir, ln, s["since"], lv, now)
        quals[ln] = q
        out["lanes"][ln] = {k: v for k, v in q.items() if not k.startswith("_")}
        if q["enough"]:
            _append(
                mdir / "candidates.jsonl",
                {
                    "ts": now,
                    "lane": ln,
                    "version": lv,
                    "config": lanes[ln],
                    "score": q["score"],
                    "se": q["se"],
                    "mem_acc": q["mem_acc"],
                    "validity": q["validity"],
                    "hold_rate": q["hold_rate"],
                    "samples": q["samples"],
                    "window": [s["since"], now],
                },
            )
    ok = [ln for ln in LANES if quals[ln]["enough"]]
    if not ok:
        out["status"] = "waiting"
        return out
    rot = st.get("rot") if isinstance(st.get("rot"), int) else 0
    order = [LANES[(rot + i) % len(LANES)] for i in range(len(LANES))]
    low = min(quals[ln]["score"] for ln in ok)
    lane = next(ln for ln in order if ln in ok and quals[ln]["score"] == low)
    st["rot"] = (LANES.index(lane) + 1) % len(LANES)
    out["lane"] = lane
    cur, lv = quals[lane], ls[lane]["version"]
    latest = {}
    for r in _rows(
        mdir / "candidates.jsonl"
    ):  # latest (longest-window) row per lane version
        if (
            r.get("lane") == lane
            and _num(r.get("version"))
            and _num(r.get("score"))
            and r["version"] != lv
        ):
            latest[r["version"]] = r
    best = max(latest.values(), key=lambda r: (r["score"], r["version"]), default=None)
    new_v = max(int(x) for x in known) + 1
    if best is not None:
        m = revert_margin(cur["se"], best.get("se") if _num(best.get("se")) else None)
        out["best"] = {
            "version": best["version"],
            "score": best["score"],
            "margin": round(m, 4),
        }
        bcfg, _ = check_lane(best.get("config"))
        if bcfg is not None and best["score"] - cur["score"] > m:
            note = f"revert {lane} to v{best['version']}"
            return _write(
                sdir, mdir, ls, lanes, lane, bcfg, new_v, now, note, out, "revert"
            )
    hist = [
        {
            k: r.get(k)
            for k in (
                "version",
                "config",
                "score",
                "mem_acc",
                "validity",
                "hold_rate",
                "samples",
            )
        }
        for r in sorted(latest.values(), key=lambda r: r["version"])[-8:]
    ]
    hist.append(
        dict(
            version=lv,
            config=lanes[lane],
            current=True,
            **{
                k: cur[k]
                for k in ("score", "mem_acc", "validity", "hold_rate", "samples")
            },
        )
    )
    prompt = meta_prompt(
        lane, lanes[lane], hist, _misses(sdir, ls[lane]["since"]), cur["_guards"]
    )
    try:
        model = llm if llm is not None else _default_llm(ctx)
    except Exception as e:  # noqa: BLE001 - no LLM client: keep current
        out.update(status="kept", reason=f"llm unavailable: {type(e).__name__}")
        return out
    cfg, why = _ask(mdir, model, prompt, now)
    if cfg is None:
        out.update(status="kept", reason=f"invalid llm reply: {why}")
        return out
    if cfg == lanes[lane]:
        out.update(status="kept", reason="llm proposed the current config")
        return out
    return _write(sdir, mdir, ls, lanes, lane, cfg, new_v, now, why, out, "candidate")


def _write(sdir, mdir, ls, lanes, lane, cfg, new_v, now, note, out, status):
    new = dict(lanes, **{lane: cfg})
    _jsave(
        sdir / "harness.json", {"version": new_v, "lanes": new, "note": note, "ts": now}
    )
    ls[lane] = {"since": now, "version": new_v, "cfg_sha": _csha(cfg)}
    out.update(status=status, new_version=new_v, config=cfg, note=note)
    return out


def step(ctx, now, llm=None):
    """One meta tick. -> None (not due) or {"status": waiting | kept | revert | candidate | error, ...}.
    Never raises."""
    mdir = None
    try:
        mdir = Path(ctx.sdir) / "meta"
        mdir.mkdir(parents=True, exist_ok=True)
        st = _jload(mdir / "state.json", {})
        st = st if isinstance(st, dict) else {}
        last = st.get("last_run")
        if _num(last) and now - last < META_S:
            return None
        st["last_run"] = now  # set first: an error below does not retry every tick
        _jsave(mdir / "state.json", st)
        out = _run(ctx, now, llm, mdir, st)
        _jsave(mdir / "state.json", st)
        _append(mdir / "log.jsonl", dict(out, ts=now))
        return out
    except Exception as e:  # noqa: BLE001 - the loop must keep going
        err = {"status": "error", "error": type(e).__name__, "msg": str(e)[:160]}
        try:
            if mdir is not None:
                _append(
                    mdir / "log.jsonl",
                    dict(err, ts=now, tb=traceback.format_exc()[-600:]),
                )
        except Exception:  # noqa: BLE001
            pass
        return err
