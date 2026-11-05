"""One strategist run every 30 minutes: collect, brief, ask, guard, rewrite SKILL.md, record.

Each run also reads the market's own rules (strategist/facts.py): the prompt carries them, and a key
pinned to a market rule (guard.SYNCED) is set from them with source "market-rule", in the same guarded
edit as the model's changes.

Each applied model change-set is checked an hour later (strategist/outcome.py): the result goes to
changes.jsonl and the model's history, a food conversion drop after buyer.cap_food or
buyer.min_surplus_list is reverted (source "revert", no model call that run), and while a check is
pending the model is not asked unless the market's rules changed or a linked constraint is broken.
In apply mode logs/strategist/ in the shop (strategist/visible.py) gets the live policy numbers and the
change table.

The shop reads strategy/SKILL.md on every call, so the only thing this process changes in the
shop is a few numbers in that file and the focus SKU list (stock.focus_skus, chosen from the ledger
by strategist/focus.py, not by the model; written with the model's numbers in the same edit).
Everything else it does is for the user to check later: each run leaves its brief, prompt, raw model
answer and a Markdown report, one runs.jsonl row, one inventory.jsonl row (our stock and what came
in, sold or left unsold since the last run), and one changes.jsonl row per rewrite. A run never raises and never retries a write: a SKILL.md
that is invalid, paused (strategy/STRATEGIST_PAUSE) or edited during the run is left as it is,
and the reason is recorded. One process at a time holds home/lock. Two strategists with different
homes may still point at the same shop, so what protects SKILL.md lives next to it: the rewrite
holds strategy/.strategist.lock, and strategy/.strategist-cooldown.json (written right after each
rewrite) carries the cooldown to every strategist and across a crash.

Without an explicit shop (--shop or SHOP_HOME) the shop is this repository, whose strategy/SKILL.md
is the one the shop image ships, so the default mode is then propose and nothing is written.
"""
import fcntl
import json
import math
import os
import signal
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

import httpx

import policy
from memory.store import append_jsonl, atomic_write_json, read_json, tail_jsonl
from strategist import analyze, collect, decide, facts, focus, guard, outcome, publish, skillfile, visible

W = Path(__file__).resolve().parents[1]
HEALTH_MAX_AGE_S = 300
HEARTBEAT_S = 60
MAX_HISTORY = 6


@dataclass
class Config:
    shop_home: Path
    home: Path
    mode: str = "apply"                 # "apply" | "propose" (never writes SKILL.md)
    interval_s: int = 1800
    first_delay_s: int = 60
    market_url: str = "https://market.ourolabs.ai"
    min_confidence: float = 0.6
    max_changes: int = 2                # model changes per run: fewer keys per set, clearer cause and effect
    cooldown_h: float = 3               # a changed key can move again six runs later
    llm_timeout: int = 300
    me: str = "psyoung82"
    log_repo: str = ""                  # strategist/publish.py: empty disables publishing
    log_branch: str = "main"
    log_key: str = ""

    @property
    def skill(self):
        return self.shop_home / "strategy" / "SKILL.md"

    @property
    def pause_file(self):
        return self.shop_home / "strategy" / "STRATEGIST_PAUSE"

    @property
    def skill_lock(self):
        return self.shop_home / "strategy" / ".strategist.lock"

    @property
    def cooldown_file(self):
        return self.shop_home / "strategy" / ".strategist-cooldown.json"


def config(shop=None, home=None, mode=None, interval=None):
    """Config from the environment; arguments that are not None win. The mode defaults to apply only
    for an explicit shop: the repository's own SKILL.md is never rewritten unless asked with --mode apply."""
    shop = shop or os.environ.get("SHOP_HOME")
    mode = mode or os.environ.get("STRATEGIST_MODE") or ("apply" if shop else "propose")
    if mode not in ("apply", "propose"):
        raise ValueError(f"STRATEGIST_MODE must be apply or propose, not {mode!r}")
    return Config(shop_home=Path(shop or W),
                  home=Path(home or os.environ.get("STRATEGIST_HOME", W / ".market" / "strategist")),
                  mode=mode,
                  interval_s=int(interval or os.environ.get("STRATEGIST_INTERVAL_S", 1800)),
                  market_url=os.environ.get("MARKET_URL", "https://market.ourolabs.ai"),
                  log_repo=os.environ.get("STRATEGIST_LOG_REPO", ""),
                  log_branch=os.environ.get("STRATEGIST_LOG_BRANCH") or "main",
                  log_key=os.environ.get("STRATEGIST_LOG_KEY", ""))


# --- small file helpers

def _lock(home):
    """An open file holding the exclusive lock home/lock, or None when another process has it."""
    return _flock(home / "lock")


def _flock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def _write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _run_id(home, now):
    """YYYYmmdd-HHMM local time; a second run in the same minute gets -2, -3, ..."""
    base = time.strftime("%Y%m%d-%H%M", time.localtime(now))
    rid, n = base, 1
    while (home / "briefs" / f"{rid}.json").exists():
        n += 1
        rid = f"{base}-{n}"
    return rid


def _history(home):
    """The last runs as the prompt shows them: what was changed, our metrics then, and the outcome check
    of the run's changes from changes.jsonl (result, metrics base -> after, reverted keys) once it is in."""
    rows = [r for r in tail_jsonl(home / "runs.jsonl", MAX_HISTORY) if isinstance(r, dict)]
    outs = {o.get("outcome_of"): o for o in tail_jsonl(home / "changes.jsonl", 200)
            if isinstance(o, dict) and o.get("outcome_of")}
    hist = []
    for r in rows:
        h = {"run_id": r.get("run_id"), "ts": r.get("ts"), "status": r.get("status"),
             "applied": [{k: a.get(k) for k in ("key", "old", "new", "source")} for a in r.get("applied") or []],
             "confidence": r.get("confidence"), "me": r.get("me")}
        o = outs.get(r.get("run_id"))
        if o:
            h["outcome"] = {k: o.get(k) for k in ("result", "metrics", "reverted")}
        hist.append(h)
    return hist


# --- the run

def run_once(cfg, now=None, client=None):
    """One run under the lock. Returns the runs.jsonl row; never raises."""
    now = time.time() if now is None else now
    lock = _lock(cfg.home)
    if lock is None:
        return {"ts": now, "status": "locked", "errors": ["another strategist holds the lock"]}
    try:
        return _run(cfg, now, client)
    finally:
        lock.close()


def _run(cfg, now, client=None):
    t0 = time.monotonic()
    home = cfg.home
    paused = cfg.pause_file.exists()
    rec = {"run_id": _run_id(home, now), "ts": now, "mode": cfg.mode, "paused": paused, "status": None,
           "policy_ver_before": None, "policy_ver_after": None, "applied": [], "proposed": [], "rejected": [],
           "confidence": None, "assessment": "", "watch": [], "focus": None, "errors": [], "me": None,
           "rules_sha": None, "alerts": [], "outcomes": [], "elapsed_ms": None}
    state = read_json(home / "state.json", {})
    state = state if isinstance(state, dict) else {}
    try:
        _steps(cfg, now, client, state, rec)
    except Exception as e:                              # record and keep the process alive
        rec["status"] = "crash"
        rec["errors"].append(f"crash: {type(e).__name__}: {str(e)[:300]}")
        rec["errors"].append(traceback.format_exc(limit=3)[-600:])
    rec["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
    try:
        append_jsonl(home / "runs.jsonl", rec)
        state["heartbeat"] = time.time()
        state["last_run"] = rec["run_id"]
        atomic_write_json(home / "state.json", state)
    except Exception as e:
        rec["errors"].append(f"record: {type(e).__name__}: {e}")
    return rec


def _steps(cfg, now, client, state, rec):
    home, rid, t0 = cfg.home, rec["run_id"], time.monotonic()
    # 1 the file exactly as it is on disk (not policy.load, which may fall back silently)
    text, front, body, skill_errors = skillfile.read(cfg.skill)
    in_file = front.get("policy") if isinstance(front.get("policy"), dict) else {}
    pol = policy.fill_defaults(in_file)[0]              # what the shop runs on; only in_file keys can be written
    if skill_errors:
        rec["errors"].append("skill invalid: " + "; ".join(skill_errors)[:500])
    else:
        rec["policy_ver_before"] = rec["policy_ver_after"] = skillfile.ver(text)
        _check_version(front.get("version"), state, rec)

    # 2 collect (with the market's rules), inventory moves and focus SKUs, brief
    own_client = client is None
    client = client or httpx.Client(base_url=cfg.market_url, timeout=20)
    try:
        raw, errors = collect.fetch_public(client, state, now, cache_dir=home / "cache", me=cfg.me)
        market = _market(client, state, now, home, raw, errors)
    finally:
        if own_client:
            client.close()
    rec["rules_sha"], rec["alerts"] = market.get("sha"), list(market.get("alerts") or [])
    window_h = max(1, math.ceil(cfg.interval_s / 3600))
    own = collect.read_own(cfg.shop_home, now, window_h=window_h, history_h=outcome.HISTORY_H)
    for e in (own.get("error"), own.get("inventory_error")):
        if e:
            errors.append(e)
            raw["errors"] = errors
    rec["errors"] += errors
    ledger_bad = bool(raw.get("ledger_gap")) or any(str(e).startswith("ledger:") for e in errors)
    flows, known = _inventory_flows(state, own.get("inventory"), raw.get("ledger"), now, ledger_bad)
    buckets = _ledger_buckets(state, raw, own, known, now, cfg.me)
    focus_part = _focus(state, raw, None if skill_errors else pol, now, cfg.me, rec)
    mf = market.get("facts") if isinstance(market.get("facts"), dict) else {}
    brief = analyze.brief(raw, own, state.get("last_brief"), pol, now, me=cfg.me, window_h=window_h,
                          flows=flows, known=known, focus=focus_part, buckets=buckets,
                          weights={"shop": mf.get("shop_weights"), "buyer": mf.get("buyer_weights")})
    rec["me"] = brief.get("me")
    atomic_write_json(home / "briefs" / f"{rid}.json", brief)
    state["last_brief"] = brief

    # 3-6 the rows to guard: market-rule syncs and outcome reverts first, then the model's; one guard call,
    # one write. The focus list is written even when the model gives nothing.
    if skill_errors:
        rec["status"] = "skill invalid"
    else:
        gpol = _with_focus(in_file, rec["focus"])
        rows, alerts = facts.sync_proposals(pol, market.get("facts"))
        rec["alerts"] += alerts + guard.linked_alerts(pol)
        reverts, rec["outcomes"] = outcome.check(state, own.get("history"), buckets, pol, now)
        rows += reverts
        wait = outcome.waiting(state, now)
        rules_changed = any(isinstance(r, dict) and isinstance(r.get("ts"), (int, float))
                            and r["ts"] > (state.get("last_model_ts") or 0) for r in market.get("recent") or [])
        if reverts:
            rec["status"] = "outcome revert"            # the model sees the result next run
        elif wait and not rules_changed and not guard.linked_alerts(pol):
            rec["status"] = f"waiting for outcome of {wait}"
        else:
            state["last_model_ts"] = now
            rows += _ask(cfg, body, gpol, brief, market, rec)
        accepted = _guard(cfg, now, gpol, rows, state, rec)
        _write(cfg, now, text, accepted, state, rec)
        if any(a["source"] == "revert" for a in rec["applied"]):
            rec["status"] = "reverted"
        outcome.register(state, rid, rec["applied"], now + (time.monotonic() - t0), own.get("history"), buckets)
        _outcome_rows(cfg, now, rec)
    _inventory_row(cfg, now, brief, rec)
    _report(cfg, rec, brief)
    if cfg.mode != "apply":                             # propose mode writes nothing into the shop
        return
    try:
        visible.write(cfg.shop_home, home, cfg.skill, now)
    except Exception as e:                              # the public copy must never fail the run
        rec["errors"].append(f"visible: {type(e).__name__}: {str(e)[:200]}")


def _ledger_buckets(state, raw, own, known, now, me):
    """This run's settle rows added to state["ledger_buckets"] (collect.ledger_buckets). Ours: to our shop's URL
    or of a unit we hold or held at the last run."""
    url = analyze.our_url(raw.get("board"), me)
    units = {str(u.get("unit")) for u in (own.get("inventory") or {}).get("units") or [] if isinstance(u, dict)}
    held = set(known) | units
    ours = lambda d: analyze._ours(d.get("shop"), me, url) or str(d.get("unit")) in held
    state["ledger_buckets"] = collect.ledger_buckets(state.get("ledger_buckets"), raw.get("ledger"),
                                                     raw.get("catalog"), ours, now)
    return state["ledger_buckets"]


def _outcome_rows(cfg, now, rec):
    """One changes.jsonl row per finished outcome check. A revert that was not written (paused, propose mode,
    rejected by the guard) says why in its result."""
    done = {a["key"] for a in rec["applied"] if a["source"] == "revert"}
    for o in rec["outcomes"]:
        result = o["result"]
        if result == "revert":
            why = next((r["why"] for r in rec["rejected"] if r["key"] in o["revert"]), rec["status"])
            result = "reverted" if set(o["revert"]) <= done else f"revert not applied ({str(why)[:80]})"
        o["result"] = result
        append_jsonl(cfg.home / "changes.jsonl", {
            "ts": now, "run_id": rec["run_id"], "outcome_of": o["outcome_of"], "result": result,
            "metrics": o["metrics"], "n": o["n"], "reverted": sorted(set(o["revert"]) & done),
            "superseded": o["superseded"]})


def _check_version(version, state, rec):
    """A version lower than the last one seen means the file was replaced (e.g. a git checkout or pull of
    the shared strategy/ folder): the strategist's earlier changes are gone. Recorded, not undone."""
    last = state.get("skill_version")
    if isinstance(version, int) and isinstance(last, int) and version < last:
        rec["errors"].append(f"SKILL.md version went back from {last} to {version}: the file was replaced "
                             "(git checkout or pull?), so earlier strategist changes are no longer in it")
    if isinstance(version, int):
        state["skill_version"] = version


def _inventory_flows(state, inv, ledger, now, ledger_bad=False):
    """Units in, sold and gone unsold (expired food, seized) since the last run, from the last run's snapshot
    (state["inventory"]["units"]: unit id -> [sku, cost, kind]) and this run's settle rows. A unit can leave
    the inventory file before or after its settle row is read: settled units still held wait in "pending"
    for the next run, and a unit gone without a settle row waits in "vanished" (unit id -> [sku, cost,
    since_ts]): sold if a later run's settle rows name it, gone unsold once a full run has read none. With a
    ledger error or gap (ledger_bad) nothing is called gone unsold this run. Returns (flows, unit ids held at
    the last run); flows is None on the first run, and without an inventory file nothing changes."""
    prev = state.get("inventory") if isinstance(state.get("inventory"), dict) else {}
    old = prev.get("units") if isinstance(prev.get("units"), dict) else None
    if not isinstance((inv or {}).get("units"), list):
        return None, set(old or ())
    cur = {str(u["unit"]): [u.get("sku"), analyze._num(u.get("cost")) or 0, u.get("kind")]
           for u in inv["units"] if u.get("unit") is not None}
    settled = {str(r["data"].get("unit")) for r in ledger or []
               if isinstance(r, dict) and r.get("kind") == "settle" and isinstance(r.get("data"), dict)}
    settled |= set(prev.get("pending") or [])
    late = prev.get("vanished") if isinstance(prev.get("vanished"), dict) else {}
    late = {u: v for u, v in late.items() if u not in cur}      # gone at an earlier run, no settle row then
    gone = old.keys() - cur.keys() if old is not None else set()
    unsold = set() if ledger_bad else late.keys() - settled
    vanished = {u: v for u, v in late.items() if u not in settled and u not in unsold}
    vanished.update({u: [old[u][0], old[u][1], now] for u in gone - settled})
    state["inventory"] = {"units": cur, "pending": sorted(settled & cur.keys()), "vanished": vanished}
    if old is None:
        return None, set()
    return {"in": len(cur.keys() - old.keys()), "sold": len((gone | late.keys()) & settled),
            "gone_unsold": len(unsold), "gone_cost": round(sum(late[u][1] for u in unsold))}, set(old)


def _focus(state, raw, pol, now, me, rec):
    """Add this run's ledger rows to the 6 h counts and choose the focus list (strategist/focus.py).
    rec["focus"]: old and new list, added, removed, their counts as evidence, and a status ("unchanged",
    or after _write how the change went). Returns the brief's focus part; None without a valid policy."""
    fs = state.get("focus") if isinstance(state.get("focus"), dict) else {}
    state["focus"] = focus.update(fs, raw.get("ledger"), now, me)
    if pol is None:
        return None
    st = pol["stock"]
    catalog = raw.get("catalog") if isinstance(raw.get("catalog"), dict) else {}
    rows = focus.select(fs, catalog, pol, now)
    old = list(st["focus_skus"])
    # without a catalog every SKU would look ineligible: keep the list
    new = focus.choose(rows, old, pol, now - fs["since"] >= focus.WINDOW_S) if catalog else old
    added, removed = [s for s in new if s not in old], [s for s in old if s not in new]
    rec["focus"] = {"old": old, "new": new, "added": added, "removed": removed,
                    "status": "unchanged" if new == old else None,
                    "evidence": {s: {k: v for k, v in focus.stats(fs, s).items() if k != "sku"}
                                 for s in added + removed}}
    return {"current": old, "next": new, "covered_h": focus.covered_h(fs, now),
            "candidates": [r for r in rows if r["settles_6h"] >= st["focus_min_settles"]][:analyze.FOCUS_TOP],
            "forum_mentions": focus.mentions(raw.get("forum"))}


def _market(client, state, now, home, raw, errors):
    """facts.collect's dict ({} if it fails), its errors added to errors."""
    try:
        market, errs = facts.collect(client, state, now, home / "rules", (raw.get("stats") or {}).get("round"))
    except Exception as e:                              # the rules are one input among many
        market, errs = {}, [f"rules: {type(e).__name__}: {str(e)[:200]}"]
    errors += errs
    return market


def _with_focus(pol, fc):
    """pol with the focus list this run will write, so the guard checks the model's numbers against it."""
    if not fc or fc["status"] is not None:
        return pol
    return pol | {"stock": pol["stock"] | {"focus_skus": fc["new"]}}


def _ask(cfg, body, pol, brief, market, rec):
    """The model's proposals, each marked source "model" whatever the answer says. [] with rec["status"]
    set when the answer gives nothing to guard."""
    rid = rec["run_id"]
    prompt = decide.build_prompt(body, pol, guard.tunable_view(pol), brief, _history(cfg.home), market,
                                 cfg.max_changes, rec["alerts"])
    _write_text(cfg.home / "prompts" / f"{rid}.txt", prompt)
    obj, raw_answer, err = decide.ask(prompt, cfg.llm_timeout)
    _write_text(cfg.home / "raw" / f"{rid}.txt", raw_answer or "")
    if err:
        rec["status"] = "llm error"
        rec["errors"].append(f"llm: {err}")
        return []
    rec.update(confidence=obj["confidence"], assessment=obj["assessment"], watch=obj["watch"])
    if obj["confidence"] < cfg.min_confidence:
        rec["status"] = "low confidence"
        why = f"confidence {obj['confidence']} < {cfg.min_confidence}"
        rec["rejected"] = [{"key": c.get("key"), "requested": c.get("value"), "why": why} for c in obj["changes"]]
        return []
    return [c | {"source": "model"} for c in obj["changes"]]


def _guard(cfg, now, pol, rows, state, rec):
    """Guard every row in one call. Returns the accepted changes; rec["status"] says why there are none."""
    # the cooldown is this home's and every other strategist's on this shop
    last_ts = state.setdefault("last_change_ts", {})
    shared = read_json(cfg.cooldown_file, {})
    for k, t in (shared.items() if isinstance(shared, dict) else ()):
        if isinstance(t, (int, float)) and not isinstance(t, bool) and t > (last_ts.get(k) or 0):
            last_ts[k] = t
    accepted, rejected = guard.guard(rows, pol, last_ts, now, cfg.max_changes, cfg.cooldown_h,
                                     last=_last_change(state, cfg.home), blocked=state.get("blocked"))
    rec["rejected"] += rejected
    if not accepted and rec["status"] is None:
        rec["status"] = "no change"
    return accepted


def _last_change(state, home):
    """state["last_change"]: key -> {ts, old, new, run_id, source} of the strategist's last write of that key.
    A state from before this field is filled once from changes.jsonl."""
    lc = state.get("last_change")
    if isinstance(lc, dict):
        return lc
    lc = {}
    for r in tail_jsonl(home / "changes.jsonl", 1000):
        if not isinstance(r, dict) or not isinstance(r.get("diff"), dict):
            continue
        kinds = r.get("kind") if isinstance(r.get("kind"), dict) else {}
        for k, d in r["diff"].items():
            if k != "stock.focus_skus" and isinstance(d, list) and len(d) == 2:
                lc[k] = {"ts": r.get("ts"), "old": d[0], "new": d[1], "run_id": r.get("run_id"),
                         "source": kinds.get(k, "model")}
    state["last_change"] = lc
    return lc


def _write(cfg, now, text, accepted, state, rec):
    """The accepted numbers and a changed focus list go into SKILL.md in one edit, in apply mode without a
    pause file. rec["status"] follows the model's changes; the focus list's outcome is rec["focus"]["status"]."""
    fc = rec["focus"] if rec["focus"] and rec["focus"]["status"] is None else None
    rec["paused"] = rec["paused"] or cfg.pause_file.exists()      # created during the model call
    if not accepted and not fc:
        return
    if cfg.mode != "apply" or rec["paused"]:
        held = "paused" if rec["paused"] else "proposed"
        if accepted:
            rec["status"], rec["proposed"] = held, accepted
        if fc:
            fc["status"] = held
        return
    _apply(cfg, now, text, accepted, fc, state, rec)
    if rec["policy_ver_after"] != rec["policy_ver_before"] and isinstance(state.get("skill_version"), int):
        state["skill_version"] += 1                     # skillfile.apply bumps version by one


def _apply(cfg, now, text, accepted, fc, state, rec):
    changes = {a["key"]: a["new"] for a in accepted} | ({"stock.focus_skus": fc["new"]} if fc else {})

    def held_back(status, why):
        if accepted:
            rec["status"], rec["proposed"] = status, accepted
        if fc:
            fc["status"] = status
        rec["errors"].append(why)

    lock = None
    try:
        lock = _flock(cfg.skill_lock)
        if lock is None:
            held_back("locked", f"another strategist is writing {cfg.skill.name}; nothing written")
            return
        new_text = skillfile.apply(cfg.skill, text, changes)
    except skillfile.Conflict as e:
        held_back("conflict", f"conflict: {e}; nothing written")
        return
    except (ValueError, OSError) as e:
        held_back("apply failed", f"apply: {type(e).__name__}: {str(e)[:300]}")
        return
    finally:
        if lock is not None:
            lock.close()
    if accepted:
        rec["status"], rec["applied"] = "applied", accepted
    if fc:
        fc["status"] = "applied"
    rec["policy_ver_after"] = skillfile.ver(new_text)
    last_ts, last = state.setdefault("last_change_ts", {}), _last_change(state, cfg.home)
    for a in accepted:
        last_ts[a["key"]] = now
        last[a["key"]] = {"ts": now, "old": a["old"], "new": a["new"], "run_id": rec["run_id"], "source": a["source"]}
    if accepted:                                        # first, so a crash right after keeps the cooldown
        try:
            shared = read_json(cfg.cooldown_file, {})
            shared = shared if isinstance(shared, dict) else {}
            atomic_write_json(cfg.cooldown_file, shared | {a["key"]: now for a in accepted})
        except OSError as e:
            rec["errors"].append(f"cooldown file: {type(e).__name__}: {e}")
    row = {"ts": now, "old_ver": rec["policy_ver_before"], "new_ver": rec["policy_ver_after"],
           "diff": {a["key"]: [a["old"], a["new"]] for a in accepted},
           "reason": "; ".join(f"{a['key']}: {a['reason']}" for a in accepted),
           "source": "strategist", "run_id": rec["run_id"], "kind": {a["key"]: a["source"] for a in accepted}}
    if fc:
        row["diff"]["stock.focus_skus"] = [fc["old"], fc["new"]]
        row["kind"]["stock.focus_skus"] = "focus"
        trend = f"stock.focus_skus: ledger trend, added {fc['added']}, removed {fc['removed']}"
        row["reason"] = "; ".join(x for x in (row["reason"], trend) if x)
        row["evidence"] = {"stock.focus_skus": fc["evidence"]}
    append_jsonl(cfg.home / "changes.jsonl", row)


def _inventory_row(cfg, now, brief, rec):
    """One inventory.jsonl row per run with an inventory file: stock, moves since the last run, focus list."""
    inv = brief.get("inventory")
    if not inv:
        return
    flows, f = inv.get("since_last_run") or {}, rec.get("focus") or {}
    append_jsonl(cfg.home / "inventory.jsonl", {
        "ts": now, **{k: inv.get(k) for k in ("balance", "inv_cost", "units", "skus")},
        **{k: flows.get(k) for k in ("in", "sold", "gone_unsold", "gone_cost")},
        "focus": f.get("new") if f.get("status") == "applied" else f.get("old")})


# --- 6 the report the user reads

def _cell(v):
    return "-" if v is None else str(v).replace("|", "\\|").replace("\n", " ")


def _metrics(brief):
    lines = []
    delta = brief.get("delta") or {}
    for sec in ("shop", "buyer"):
        d = (brief.get("me") or {}).get(sec)
        if not d:
            continue
        lines += [f"\n**{sec}**\n", "| 지표 | 값 | 지난 실행 대비 |", "|---|---|---|"]
        dd = delta.get(sec) or {}
        lines += [f"| {k} | {_cell(v)} | {_cell(dd.get(k, ''))} |" for k, v in d.items()]
    return lines or ["(보드에서 우리 행을 찾지 못함)"]


def _inventory_lines(brief):
    inv = brief.get("inventory")
    if not inv:
        return ["(.market/inventory.json 없음)"]
    fl = inv.get("since_last_run") or {}
    n_cost = lambda d: f"{_cell(d.get('n'))}개 (원가 {_cell(d.get('cost'))})"
    out = [f"- 잔액 {_cell(inv['balance'])}, 재고 원가 {_cell(inv['inv_cost'])} (D1 상한 {_cell(inv['cap'])}), "
           f"{inv['units']}개, SKU {inv['skus']}개",
           f"- 지난 실행 이후: 입고 {_cell(fl.get('in'))}, 판매 {_cell(fl.get('sold'))}, "
           f"팔리지 않고 빠짐 {_cell(fl.get('gone_unsold'))} (원가 {_cell(fl.get('gone_cost'))})",
           f"- 오래된 상품 {n_cost(inv['stale_goods'])}, 24 h 안에 만료될 식품 {n_cost(inv['food_at_risk'])}, "
           f"만료 {n_cost(inv['expired'])}"]
    if inv["top"]:
        out += ["", "| SKU | 수량 | 원가 | 가장 오래된 h | 식품 남은 h | 시간당 판매 | 버틸 h |", "|---|---|---|---|---|---|---|"]
        out += [f"| {_cell(r['sku'])} | {r['qty']} | {r['cost']} | {_cell(r['age_h'])} | {_cell(r['food_left_h'])} "
                f"| {_cell(r['sales_per_h'])} | {_cell(r['cover_h'])} |" for r in inv["top"]]
    return out


def _focus_lines(rec, brief):
    f = rec.get("focus")
    if not f:
        return ["(없음)"]
    out = [f"- 지금: {', '.join(f['old']) or '(없음)'}",
           f"- 이번 선택: {', '.join(f['new']) or '(없음)'} — **{f['status']}**"]
    for s in f["added"] + f["removed"]:
        ev = f["evidence"][s]
        out.append(f"  - {'추가' if s in f['added'] else '제외'} `{s}`: 6 h 체결 {ev['settles_6h']}, "
                   f"공급 상점 {ev['suppliers_6h']}, ledger seq {ev['refs']}")
    bf = brief.get("focus") or {}
    out.append(f"- 집계 기간: {_cell(bf.get('covered_h'))} h")
    if bf.get("forum_mentions"):
        out.append("- 포럼 언급 (참고만, 선택에 쓰지 않음): "
                   + ", ".join(f"{_cell(k)} {_cell(v)}" for k, v in bf["forum_mentions"].items()))
    return out


def _report(cfg, rec, brief):
    when = time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(rec["ts"]))
    mode = rec["mode"] + (" (일시정지: STRATEGIST_PAUSE)" if rec["paused"] else "")
    out = [f"# 전략 점검 {rec['run_id']}", "",
           f"- 시각: {when}", f"- 모드: {mode}", f"- 결과: **{rec['status']}**",
           f"- 정책 버전: {rec['policy_ver_before']} → {rec['policy_ver_after']}",
           f"- 확신도: {rec['confidence']}", f"- 남은 시간: {brief.get('hours_left')} h", "",
           "## 판단", "", rec["assessment"] or "(없음)", "", "## 우리 지표"]
    out += _metrics(brief)
    out += ["", "## 적용한 변경", ""]
    out += [f"- `{a['key']}`: {a['old']} → {a['new']}" + (f" (요청 {a['requested']}, 범위로 조정)" if a["clamped"] else "")
            + f" — {_cell(a['reason'])}" for a in rec["applied"]] or ["(없음)"]
    if rec["proposed"]:
        out += ["", "## 통과했지만 쓰지 않은 변경", ""]
        out += [f"- `{a['key']}`: {a['old']} → {a['new']} — {_cell(a['reason'])}" for a in rec["proposed"]]
    out += ["", "## 거절한 제안", ""]
    out += [f"- `{_cell(r['key'])}` = {_cell(r['requested'])}: {_cell(r['why'])}" for r in rec["rejected"]] or ["(없음)"]
    out += ["", "## 이전 변경의 결과 (1시간 뒤 점검)", ""]
    out += [f"- {_cell(o['outcome_of'])}: **{_cell(o['result'])}** — "
            + ", ".join(f"{m} {_cell(v[0])} → {_cell(v[1])}" for m, v in o["metrics"].items())
            for o in rec["outcomes"]] or ["(없음)"]
    out += ["", "## 재고", ""] + _inventory_lines(brief)
    out += ["", "## 집중 SKU (stock.focus_skus)", ""] + _focus_lines(rec, brief)
    out += ["", "## 다음에 볼 것", ""] + ([f"- {_cell(w)}" for w in rec["watch"]] or ["(없음)"])
    out += ["", "## 경고 (시장 규칙, 연결 제약)", ""] + ([f"- {_cell(a)}" for a in rec["alerts"]] or ["(없음)"])
    out += ["", "## 오류", ""] + ([f"- {_cell(e)}" for e in rec["errors"]] or ["(없음)"])
    text = "\n".join(out) + "\n"
    _write_text(cfg.home / "reports" / f"{rec['run_id']}.md", text)
    _write_text(cfg.home / "reports" / "latest.md", text)


# --- long-running process and health

def _beat(home):
    try:
        atomic_write_json(home / "heartbeat.json", {"ts": time.time(), "pid": os.getpid()})
    except OSError:
        pass


def summary(rec):
    """The one-line JSON a run prints."""
    return json.dumps({k: rec.get(k) for k in ("run_id", "status", "mode", "paused", "policy_ver_after",
                                                "confidence", "elapsed_ms")}
                      | {"applied": [f"{a['key']} {a['old']}->{a['new']}" for a in rec.get("applied") or []],
                         "focus": (rec.get("focus") or {}).get("status"),
                         "rejected": len(rec.get("rejected") or []), "errors": len(rec.get("errors") or [])},
                      ensure_ascii=False)


def loop(cfg):
    """Run every interval_s from run start, the first one first_delay_s after start. Exit 0 on SIGTERM/SIGINT.
    The heartbeat is its own file, written by a thread, so a long run (slow market, 300 s model call)
    still looks alive to the healthcheck and never races the run's state.json write."""
    lock = _lock(cfg.home)
    if lock is None:
        print(json.dumps({"status": "locked", "error": f"another strategist holds {cfg.home / 'lock'}"}), flush=True)
        return 1
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    _beat(cfg.home)

    def beats():
        while not stop.wait(HEARTBEAT_S):
            _beat(cfg.home)

    threading.Thread(target=beats, daemon=True).start()
    next_run = time.time() + cfg.first_delay_s
    try:
        while not stop.is_set():
            if time.time() >= next_run:
                start = time.time()
                rec = _run(cfg, start)
                print(summary(rec), flush=True)
                try:
                    pub = publish.publish(cfg, rec)
                except Exception as e:                  # publishing must never end the loop
                    pub = {"error": f"{type(e).__name__}: {e}"[:400]}
                if pub and pub.get("error"):
                    print(json.dumps({"publish": pub}, ensure_ascii=False), flush=True)
                next_run = start + cfg.interval_s
            else:
                stop.wait(1)
    finally:
        lock.close()
    return 0


def health(home, now=None):
    """0 when a heartbeat is younger than HEALTH_MAX_AGE_S, else 1."""
    now = time.time() if now is None else now
    ts = []
    for name, key in (("heartbeat.json", "ts"), ("state.json", "heartbeat")):
        d = read_json(Path(home) / name, {})
        t = d.get(key) if isinstance(d, dict) else None
        if isinstance(t, (int, float)) and not isinstance(t, bool):
            ts.append(t)
    return 0 if ts and now - max(ts) < HEALTH_MAX_AGE_S else 1
