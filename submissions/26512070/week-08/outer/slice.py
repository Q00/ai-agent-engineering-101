"""One 2-hour slice of the outer runtime (HARNESS.md 2.3, 5): market analyst -> self analyst -> strategy, then the
audit in its own process (outer.audit).

    py -m outer.slice            # run one slice now

Each step is one agents.loop turn of its role, ending with its rec.* tool. A step that fails is recorded and
the next one still runs with what there is; the audit never blocks the brief: the slice starts it and goes on. At night (NIGHT hours) the
brief is a report only. The slice's window starts where the last slice ended (memory/facts/slice_state.json).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from agents import loop
from core import atomic, bus, paths
from core.events import EventWriter
from outer import audit, brief
from tools.registry import Ctx, call

CODE = Path(__file__).resolve().parent.parent   # the code; HARNESS_ROOT (the records) may be elsewhere
NIGHT = range(1, 8)            # 01:00-07:59 (HARNESS.md 5)
# User 11/3 23:2x: at night the strategy agent decides by itself every 30 minutes. A slice in these hours writes
# options, the audit runs in the slice, and the recommended option is installed when the audit agrees and the
# change keeps the safety terms (SAFE_KEYS); otherwise the brief waits for the user. These hours override NIGHT.
AUTO_HOURS = range(0, 8)       # 00:00-07:59
AUTO_EVERY_MIN = 30
# User 11/5 01:3x: a change must live long enough to be measured - v67 (auto, 01:33) landed 21 min after the user's
# v66 (01:12). No install (night mode or delegation) while the active strategy is younger than HOLD_S; the slice
# still writes its brief. 2 h at first; the user (02:0x) keeps the 30-min pace and gets the night as one report
# in the morning (outer/morning.py), so HOLD_S is just under one slice: an install lands ~3 min into its slice, so the
# next slice (age ~27 min) may install again, while a user install mid-slice (v66 at 01:12) holds the 01:30 one.
HOLD_S = 25 * 60
# User notes (logs/strategy/user_notes.md) are questions for a moment; the 11/5 01:00 brief still cited the
# 11/4 02:2x note as a current fact. Only sections whose '# YYYY-MM-DD HH:Mx' heading is this recent reach the brief.
NOTE_HOURS = 6


def slice_id(now):
    """The slice id: one per hour by day (YYYY-MM-DDTHH), one per half hour in AUTO_HOURS (YYYY-MM-DDTHHMM)."""
    tm = time.localtime(now)
    if tm.tm_hour in AUTO_HOURS:
        return time.strftime("%Y-%m-%dT%H", tm) + f"{tm.tm_min // AUTO_EVERY_MIN * AUTO_EVERY_MIN:02d}"
    return time.strftime("%Y-%m-%dT%H", tm)


DELEGATED = {"stock": {"limits": {"max_stock_share", "max_units_per_sku"}, "restock": None, "context": None},
             # user 11/4 02:1x: the rules' numbers keep changing with the data (HARNESS 14)
             "playbook": {"playbook": None, "context": None}}


def held(now):
    """Seconds left of the hold after the last install (active.json is written only by core.strategy.install), or 0."""
    try:
        age = now - (paths.memory() / "strategy" / "active.json").stat().st_mtime
    except OSError:
        return 0
    return round(HOLD_S - age) if 0 <= age < HOLD_S else 0


def recent_notes(text, now, hours=NOTE_HOURS):
    """The user_notes.md sections whose heading time is within `hours` of now ('x' in the minutes reads as 0)."""
    import re
    keep, cur = [], None
    for line in text.splitlines():
        m = re.match(r"# (\d{4}-\d{2}-\d{2} \d{2}):(\d)[0-9x]", line)
        if m:
            t = time.mktime(time.strptime(f"{m.group(1)}:{m.group(2)}0", "%Y-%m-%d %H:%M"))
            cur = [] if 0 <= now - t <= hours * 3600 else None
            if cur is not None:
                keep.append(cur)
        if cur is not None:
            cur.append(line)
    return "\n".join("\n".join(s) for s in keep).strip()


def delegated(strategy, change):
    """User 11/3 23:5x: inside strategy.delegation.auto the strategy agent decides by day too. True when every key
    the option changes is in a delegated area (DELEGATED; None = any sub-key)."""
    areas = [DELEGATED[a] for a in (strategy.get("delegation") or {}).get("auto", []) if a in DELEGATED]
    if not areas or not change:
        return False
    for k, v in change.items():
        ok = False
        for area in areas:
            if k in area and (area[k] is None or (isinstance(v, dict) and set(v) <= area[k])):
                ok = True
        if not ok:
            return False
    return True


def unsafe(active, new):
    """Why an automatic install would loosen a safety term the user set, or None."""
    la, ln = active.get("limits") or {}, new.get("limits") or {}
    if (ln.get("cash_floor") or 0) < (la.get("cash_floor") or 0):
        return f"cash_floor {la.get('cash_floor')} -> {ln.get('cash_floor')}"
    if (ln.get("below_cost") or {}).get("allowed") and not (la.get("below_cost") or {}).get("allowed"):
        return "below_cost allowed"
    if active.get("certified_only") and not new.get("certified_only"):
        return "certified_only off"
    wa, wn = active.get("warranty") or {}, new.get("warranty") or {}
    if (wn.get("hours") or 0) < (wa.get("hours") or 0):
        return f"warranty hours {wa.get('hours')} -> {wn.get('hours')}"
    sa, sn = (active.get("restock") or {}).get("stop_after"), (new.get("restock") or {}).get("stop_after")
    if sa and (not sn or sn > sa):
        return f"restock stop_after {sa} -> {sn} (stock is sold down before the round ends)"
    if (ln.get("food_share_max") or 0) > (la.get("food_share_max") or 0):
        return f"food_share_max {la.get('food_share_max')} -> {ln.get('food_share_max')}"
    ra, rn = active.get("repeat_buyer") or {}, new.get("repeat_buyer") or {}
    if ra.get("min_share_of_list") and (rn.get("min_share_of_list") or 0) < min(ra["min_share_of_list"], 0.8):
        return f"price floor {ra.get('min_share_of_list')} -> {rn.get('min_share_of_list')} of list"
    if (ln.get("max_stock_share") or 0) > max(la.get("max_stock_share") or 0, 0.5):
        return f"max_stock_share {la.get('max_stock_share')} -> {ln.get('max_stock_share')}"
    return None
PROFILES_PER_SLICE = 6


def _j(x, n=1500):
    return json.dumps(x, ensure_ascii=False, default=str)[:n]


def market_text(ctx, since):
    live = atomic.read_json(paths.memory() / "facts" / "live.json", default={}) or {}
    state = atomic.read_json(paths.memory() / "facts" / "collect_state.json", default={}) or {}
    shops = sorted((state.get("shops") or {}).items(), key=lambda x: -(x[1].get("deals_seen") or 0))[:20]
    customers = sorted((live.get("customer") or {}).items(), key=lambda x: -(x[1].get("deals_with_us") or 0))[:15]
    return "\n".join([
        "# the analysis at hand",
        f"Slice {ctx.situation['slice']} (since {time.strftime('%m-%d %H:%M', time.localtime(since))}).",
        f"Selling in the last {live.get('window_hours')} h: {_j(live.get('selling'), 1200)}",
        f"Participants: {_j(live.get('participants'), 400)}",
        f"Most active shops (deals seen, stars): {_j(shops, 1500)}",
        f"Our customers: {_j(customers, 1000)}",
        f"You have {loop.ROLES['market analyst']['steps']} steps. "
        f"Write up to {PROFILES_PER_SLICE} profiles (rec.profile) for the participants that matter most this slice "
        "(new, changed, or dealing with us), then end with rec.summary: what sells, who does what, who changed."])


def self_text(ctx, since):
    return "\n".join([
        "# the analysis at hand",
        f"Slice {ctx.situation['slice']}: evaluate our shop, our buyer and each inner agent since {since:.0f} "
        "(pass since to the eval.* tools). Say where we lost money, deals or votes and why, with evidence, "
        "then end with rec.summary."])


def strategy_text(ctx):
    s = ctx.strategy
    pending = paths.memory() / "strategy" / "counterparts_pending.jsonl"
    lines = ["# the brief at hand",
             f"Strategy in force: v{s.get('version')} ({s.get('epoch')}), phase {s.get('phase')}, approved {s.get('approved')!r}.",
             f"Experiment: {_j(s.get('experiment'), 800)}",
             f"Delegation: {_j(s.get('delegation'), 300)}"]
    for kind in ("market", "self"):
        r = call(ctx, "memory.summary", {"kind": kind})
        lines.append(f"This slice's {kind} summary: {_j(r['result'] if r['ok'] else r['reason'], 2500)}")
    from core import score
    sb = atomic.read_json(paths.memory() / "facts" / "scoreboard.json", default=None) or {}
    sc = sb.get("score") or {}
    lines.append(score.text() + " Where ALL has the most room (weight x (1 - percentile)): shop "
                 + _j((sc.get("shop") or {}).get("room"), 200) + ", buyer " + _j((sc.get("buyer") or {}).get("room"), 200)
                 + "; parts with the next row's value: " + _j({k: {f: {x: p[x] for x in ("value", "percentile", "next_value", "rows_above")}
                                                                    for f, p in ((sc.get(k) or {}).get("parts") or {}).items()}
                                                               for k in ("shop", "buyer")}, 1200))
    pos = atomic.read_json(paths.memory() / "facts" / "position.json", default=None)
    if pos:                                              # user 11/5 02:0x: our place in every part, and how it moved
        lines.append("Our rank now (outer/position.py, 1 = best; rank_1h/rank_6h = our rank then): Players "
                     + _j({k: pos["players"].get(k) for k in ("rank", "of", "next", "gap", "rank_1h", "rank_6h")}
                          if pos.get("players") else None, 200)
                     + "; parts " + _j({side: {f: {k: p.get(k) for k in ("rank", "of", "next", "gap", "rank_1h", "rank_6h")}
                                               for f, p in ((pos.get(side) or {}).get("parts") or {}).items()}
                                        for side in ("shop", "buyer")}, 1400))
    from outer import benchmark, health
    lines.append("Health (last 30 min, outer/health.py: what the shop, the buyer and restock did, and what crossed a "
                 "threshold): " + health.summary(atomic.read_json(paths.memory() / "facts" / "health.json",
                                                                  default=None))[:2000])
    bench = atomic.read_json(paths.memory() / "facts" / "benchmark.json", default=None)
    lines.append("Benchmark (the top players by board profit and buyer ALL, last 6 h of the public ledger; copy what "
                 "wins - the SKUs they sell that we lack, their prices, their speed): " + benchmark.summary(bench)[:2500])
    notes = paths.root() / "logs" / "strategy" / "user_notes.md"
    fresh = recent_notes(notes.read_text(encoding="utf-8"), time.time()) if notes.exists() else ""
    if fresh:                                            # 11/4 02:2x: what the user asks the strategy agent to judge
        lines.append("The user asks you to judge (answer it in this brief, with options if a change is due): "
                     + fresh[-2000:])
    bl = atomic.read_json(paths.memory() / "facts" / "buy_list.json", default=None)
    lines.append(f"Playbook in force (the numbers the rules run on; change them with an option's playbook): "
                 f"{_j(s.get('playbook') or 'defaults', 300)}; restock buy list now: "
                 f"{_j([(x['sku'], x['target'], x['have'], x['why']) for x in (bl or {}).get('lines', [])][:12], 900)}")
    if pending.exists():
        lines.append("Stances waiting for the user: " + _j(pending.read_text(encoding="utf-8").splitlines()[-10:], 1000))
    if ctx.situation.get("hold"):
        lines.append(f"The strategy in force was installed less than {HOLD_S // 60} min ago, so nothing is installed "
                     f"for another {ctx.situation['hold'] // 60} min: a change needs that long to be measured. Write "
                     "the brief with rec.brief as a report on how the change in force is doing so far (what moved, "
                     "with numbers). Options are allowed but wait for the user.")
    elif ctx.situation.get("auto"):
        lines.append("It is night and the user is away: your recommendation is installed by itself once the audit "
                     "agrees, and the next slice comes in 30 minutes. The goal is the board's Players rank: shop ALL = "
                     "0.5 profit + 0.3 revenue + 0.2 stars, buyer ALL = 0.5 surplus + 0.3 votes on buyer posts + "
                     "0.1 deals + 0.1 buyer posts, each a percentile. Write the brief with rec.brief: 2-3 options and "
                     "a recommendation if the numbers since the last slice show a better direction, else no options. "
                     "Change a few numbers at a time, so the next slice can tell what worked.")
    elif ctx.situation["night"]:
        lines.append("It is night: write a report only (no options).")
    else:
        lines.append("Write the brief with rec.brief: 2-3 options and a recommendation if something should change, "
                     "else no options."
                     + (" The user delegated the stock to you (strategy.delegation): an option that changes only "
                        "limits.max_stock_share (at most 0.5), limits.max_units_per_sku, restock or context is "
                        "installed by itself once the audit agrees; anything else waits for the user."
                        if "stock" in (s.get("delegation") or {}).get("auto", []) else ""))
    return "\n".join(lines)


def auto_install(pid, model_call=None):
    """Night mode or the delegation: audit the brief here, then install its recommendation if the audit agrees and it
    is safe."""
    from outer import decide
    op = audit.run(pid, model_call=model_call)
    doc = atomic.read_json(paths.memory() / "strategy" / "proposals" / f"{pid}.json", default={}) or {}
    rec = doc.get("recommendation")
    if not op or not op.get("agrees"):
        bus.alert("slice", "info", "auto_held", f"brief {pid}: the audit did not agree, option {rec} waits for the user")
        return {"installed": None, "why": "audit did not agree" if op else "no audit"}
    active = atomic.read_json(paths.memory() / "strategy" / "active.json", default={}) or {}
    why = unsafe(active, (doc.get("strategies") or {}).get(rec) or {})
    if why:
        bus.alert("slice", "warn", "auto_held", f"brief {pid}: option {rec} loosens a safety term ({why}), it waits "
                  "for the user")
        return {"installed": None, "why": why}
    d = decide.decide(pid, rec, note="night mode or delegation: audit agreed", by="auto")
    bus.alert("slice", "info", "auto_installed", f"brief {pid}: option {rec} installed as v{d.get('installed')} (night mode or delegation)")
    return {"installed": d.get("installed"), "option": rec}


def _lock_path():
    return paths.memory() / "facts" / "slice.lock"


def running():
    """True while a slice process holds the lock."""
    _lock_path().parent.mkdir(parents=True, exist_ok=True)
    f = open(_lock_path(), "a+b")
    try:
        atomic._lock(f)
    except OSError:
        f.close()
        return True
    atomic._unlock(f)
    f.close()
    return False


def run(now=None, model_call=None, sid=None):
    _lock_path().parent.mkdir(parents=True, exist_ok=True)
    lock = open(_lock_path(), "a+b")
    try:
        atomic._lock(lock)
    except OSError:
        lock.close()
        return {"skipped": "another slice is running"}
    try:
        return _run(now, model_call, sid)
    finally:
        atomic._unlock(lock)
        lock.close()


def _run(now=None, model_call=None, sid_override=None):
    now = now or time.time()
    st_path = paths.memory() / "facts" / "slice_state.json"
    st = atomic.read_json(st_path, default=None) or {"last": now - 7200}
    tm = time.localtime(now)
    sid = sid_override or slice_id(now)        # --id: a slice the user asked for between the scheduled ones
    hold = held(now)
    auto = tm.tm_hour in AUTO_HOURS and not hold
    tool_w, w = EventWriter("outer", source="tools"), EventWriter("slice", source="outer")
    out = {"slice": sid, "steps": {}}
    try:
        base = Ctx(caller="outer", _writer=tool_w)
        epoch = base.strategy.get("epoch") or "e0"
        w.context(strategy_version=base.strategy.get("version"), epoch=epoch)
        common = {"epoch": epoch, "slice": sid, "night": tm.tm_hour in NIGHT and not auto, "auto": auto,
                  "hold": hold, "proposal_id": sid}

        def step(role, caller, text_fn, extra=None):
            ctx = Ctx(caller=caller, _writer=tool_w)
            ctx.situation = dict(common, **(extra or {}))
            t0 = time.time()
            try:
                d, trace = loop.decide(role, ctx, text_fn(ctx), model_call=model_call)
            except Exception as e:                       # one broken step does not stop the slice
                d, trace = None, [{"observation": f"{type(e).__name__}: {e}"}]
            res = {"ok": d is not None, "s": round(time.time() - t0), "steps": len(trace)}
            if d is None:
                res["why"] = str(trace[-1]["observation"])[:200] if trace else "no answer"
            out["steps"][role] = res
            w.emit("slice.step", {"role": role, **res}, )
            return d

        step("market analyst", "market-analyst", lambda c: market_text(c, st["last"]), {"kind": "market"})
        step("self analyst", "self-analyst", lambda c: self_text(c, st["last"]), {"kind": "self"})
        d = step("strategy", "strategy", strategy_text)
        doc = atomic.read_json(paths.memory() / "strategy" / "proposals" / f"{sid}.json", default=None)
        if d and doc:
            if doc.get("options"):
                w.emit("strategy.proposal", {"proposal_id": sid, "version_from": doc.get("version_from"),
                                             "options": [o["id"] for o in doc["options"]]})
            pub = atomic.read_json(brief.briefs_dir() / f"{sid}.json", default=None)
            if pub:
                brief.write_markdown(pub)
            brief.refresh_pending()
            rec_change = next((o.get("change") for o in doc.get("options") or [] if o["id"] == doc.get("recommendation")), None)
            if doc.get("options") and doc.get("recommendation") and not hold and (auto or delegated(base.strategy, rec_change)):
                out["steps"]["auto"] = auto_install(sid, model_call)
            elif model_call is not None:                 # tests: the audit in this process, with the same script
                out["steps"]["audit"] = {"ok": audit.run(sid, model_call=model_call) is not None}
            else:                                        # its own process; the slice does not wait for it
                subprocess.Popen([sys.executable, "-m", "outer.audit", sid], cwd=CODE,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                                    else {"start_new_session": True}))
                out["steps"]["audit"] = {"started": True}
        else:
            bus.alert("slice", "warn", "no_brief", f"slice {sid}: the strategy agent wrote no brief", out["steps"])
        atomic.write_json(st_path, {"last": now, "slice": sid})
        w.emit("slice.done", out)
    finally:
        w.close()
        tool_w.close()
    return out


if __name__ == "__main__":
    sid = sys.argv[sys.argv.index("--id") + 1] if "--id" in sys.argv else None
    print(json.dumps(run(sid=sid), ensure_ascii=False))
