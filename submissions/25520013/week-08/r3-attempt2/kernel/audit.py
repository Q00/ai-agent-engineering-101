"""Process audit of the operator model (meta-harness): how it works, not what it decides. Plain code, no model calls.

report(root, now) reads the trajectory (<root>/a2/trajectory.jsonl, last TRAJ_BYTES) and the experiment ledger and
counts, over the compact wakes in the window, what the operator ignored (tools, experiment groups, verdicts), wasted
(repeated calls, idle wakes) and got wrong (refused edits, model errors, wakes without done). lines() is the [감사]
block of the state card; tool_table() is the per-tool table for the auditor model. Missing or corrupt files count as
zero; nothing here raises on bad rows. Nothing is masked here (callers mask).
"""

import json
import math
import os

from kernel import common as c
from kernel import experiments as ex

TRAJ = os.path.join("a2", "trajectory.jsonl")
TRAJ_BYTES = 30_000_000
GROUPS = ("sale", "restock", "buyer", "shop")
GROUP_KO = {
    "sale": "판매 값",
    "restock": "재고 값",
    "buyer": "구매 값",
    "shop": "가게 값",
}
ENDED = ("adopted", "kept", "dropped", "reverted", "voided")
DECIDED = (
    "adopted",
    "kept",
    "dropped",
    "reverted",
)  # voided has no verdict, so it is not in the hit rate
GOOD = ("adopted", "kept")
STALE_WAKES = 2
REASON_CHARS = 40
UNUSED_SHOWN = 8
IDS_SHOWN = 10
LINES_MAX = 6
LINE_CHARS = 200
BLOCK_CHARS = 700


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def group_of(section, key):
    """buyer.* section -> buyer; a restock key (experiments.SLOW_KEYS, per-grade suffix ignored) -> restock; section
    shop -> shop; anything else -> sale."""
    section = section if isinstance(section, str) else ""
    key = key if isinstance(key, str) else ""
    if section.startswith("buyer."):
        return "buyer"
    if key.split(".", 1)[0] in ex.SLOW_KEYS:
        return "restock"
    if section == "shop":
        return "shop"
    return "sale"


def _wakes(root):
    """-> (wakes, poll_crash_ts). wakes: every operator_wake row in file order with the tool rows written since the
    previous wake row ("tools": [(name, args)], "obs": [(name, obs_chars)])."""
    wakes, crashes, tools = [], [], []
    for r in c.tail_json(os.path.join(root, TRAJ), TRAJ_BYTES):
        if r.get("agent") != "operator" or not _num(r.get("ts")):
            continue
        kind = r.get("kind")
        if kind == "tool":
            name = r.get("name") if isinstance(r.get("name"), str) else "?"
            args = (
                r.get("args")
                if isinstance(r.get("args"), str)
                else json.dumps(r.get("args"), sort_keys=True, default=str)
            )
            tools.append(
                (name, args, r.get("obs_chars") if _num(r.get("obs_chars")) else None)
            )
        elif kind == "operator_wake":
            wakes.append(dict(r, _tools=tools))
            tools = []
        elif kind == "operator_poll" and r.get("status") == "crash":
            crashes.append(r["ts"])
    return wakes, crashes


def _compact(w):
    res = w.get("result")
    return isinstance(res, dict) and _num(res.get("calls"))


def _posted(res):
    f = res.get("forum")
    if isinstance(f, list):
        return bool(f)
    return isinstance(f, dict) and _num(f.get("queued")) and f["queued"] > 0


def _idle(w):
    res = w["result"]
    if w.get("status") != "done":
        return False
    pol = res.get("policy") if isinstance(res.get("policy"), list) else []
    if any(isinstance(p, dict) and p.get("ok") is True for p in pol):
        return False
    nt = res.get("new_tool")
    if isinstance(nt, dict) and nt.get("ok"):
        return False
    return not _posted(res)


def _ledger(root):
    try:
        rows = ex.load(root)
    except Exception:  # noqa: BLE001 - a ledger the loader cannot read counts as empty
        rows = []
    return [e for e in rows if isinstance(e, dict)]


def report(root, now, hours=24, since=None, listed=None):
    """Audit counts over [since, now] when since is given, else [now - hours h, now]. See the module docstring."""
    start = since if _num(since) else now - hours * 3600
    all_wakes, poll_crashes = _wakes(root)
    in_win = [w for w in all_wakes if start <= w["ts"] <= now]
    wakes = [w for w in in_win if _compact(w)]
    n = len(wakes)
    calls = sum(w["result"]["calls"] for w in wakes)
    chars = sum(
        w["result"].get("prompt_chars")
        for w in wakes
        if _num(w["result"].get("prompt_chars"))
    )

    tools, obs = {}, {}
    for w in wakes:
        seen = {}
        for name, args, oc in w["_tools"]:
            t = tools.setdefault(
                name,
                {
                    "uses": 0,
                    "wakes": 0,
                    "obs_chars_mean": 0,
                    "obs_chars_max": 0,
                    "repeats": 0,
                },
            )
            t["uses"] += 1
            if not any(k[0] == name for k in seen):
                t["wakes"] += 1
            if (name, args) in seen:
                t["repeats"] += 1
            seen[(name, args)] = True
            if oc is not None:
                obs.setdefault(name, []).append(oc)
    for name, v in obs.items():
        tools[name]["obs_chars_mean"] = round(sum(v) / len(v))
        tools[name]["obs_chars_max"] = max(v)
    listed = set(x for x in (listed or ()) if isinstance(x, str))

    edits = {"asked": 0, "ok": 0, "refused": 0, "reasons": {}}
    for w in wakes:
        pol = w["result"].get("policy")
        for p in pol if isinstance(pol, list) else []:
            edits["asked"] += 1
            if isinstance(p, dict) and p.get("ok") is True:
                edits["ok"] += 1
                continue
            edits["refused"] += 1
            err = str(p.get("error") if isinstance(p, dict) else "not an object")[
                :REASON_CHARS
            ]
            edits["reasons"][err] = edits["reasons"].get(err, 0) + 1

    rows = _ledger(root)
    started = [
        e for e in rows if _num(e.get("started_ts")) and start <= e["started_ts"] <= now
    ]
    ended = [
        e
        for e in rows
        if e.get("status") in ENDED
        and _num(e.get("ended_ts"))
        and start <= e["ended_ts"] <= now
    ]
    running = [e for e in rows if e.get("status") == "running"]
    exps = {
        s: sum(1 for e in ended if e["status"] == s)
        for s in ("adopted", "kept", "dropped", "reverted", "voided")
    }
    exps = {
        "started": len(started),
        "running": len(running),
        **exps,
        "by_group": {
            g: sum(1 for e in started if group_of(e.get("section"), e.get("key")) == g)
            for g in GROUPS
        },
    }
    busy = {group_of(e.get("section"), e.get("key")) for e in running}
    untouched = [g for g in GROUPS if exps["by_group"][g] == 0 and g not in busy]

    compact_ts = [w["ts"] for w in all_wakes if _compact(w) and w["ts"] <= now]
    stale = []

    def order(e):  # numeric ids in number order, any other id after them as text
        i = e.get("id")
        return (e["ended_ts"], 0, i, "") if _num(i) else (e["ended_ts"], 1, 0, str(i))

    for e in sorted(ended, key=order):
        t, g = e["ended_ts"], group_of(e.get("section"), e.get("key"))
        if sum(1 for ts in compact_ts if ts > t) < STALE_WAKES:
            continue
        if any(
            _num(x.get("started_ts"))
            and x["started_ts"] > t
            and group_of(x.get("section"), x.get("key")) == g
            for x in rows
        ):
            continue
        stale.append(e.get("id"))

    decided = [e for e in ended if e["status"] in DECIDED]
    rep = {
        "window_h": round((now - start) / 3600, 2),
        "from_ts": start,
        "wakes": n,
        "calls": calls,
        "prompt_chars": chars,
        "calls_per_wake": round(calls / max(n, 1), 2),
        "chars_per_call": round(chars / max(calls, 1)),
        "idle_wakes": sum(1 for w in wakes if _idle(w)),
        "model_errors": sum(1 for w in wakes if w.get("status") == "model_error"),
        "no_done": sum(1 for w in wakes if w.get("status") == "no_done"),
        "crashes": sum(1 for ts in poll_crashes if start <= ts <= now)
        + sum(1 for w in in_win if w.get("status") == "crash"),
        "tools": dict(sorted(tools.items())),
        "repeat_calls": sum(t["repeats"] for t in tools.values()),
        "unused_tools": sorted(listed - set(tools)),
        "edits": edits,
        "experiments": exps,
        "untouched_groups": untouched,
        "stale_verdicts": stale,
        "hit": {
            "ended": len(decided),
            "good": sum(1 for e in decided if e["status"] in GOOD),
        },
    }
    rep["score"] = score(rep)
    return rep


def score(rep):
    """mistakes_per_wake = (repeat_calls + refused edits + model_errors + no_done) / wakes; idle_share = idle / wakes;
    hit_rate = good / ended, None with no ended experiment."""
    n = max(rep.get("wakes") or 0, 1)
    bad = (rep.get("repeat_calls") or 0) + (
        (rep.get("edits") or {}).get("refused") or 0
    )
    bad += (rep.get("model_errors") or 0) + (rep.get("no_done") or 0)
    hit = rep.get("hit") or {}
    return {
        "mistakes_per_wake": round(bad / n, 2),
        "idle_share": round((rep.get("idle_wakes") or 0) / n, 2),
        "hit_rate": round(hit["good"] / hit["ended"], 2) if hit.get("ended") else None,
        "wakes": rep.get("wakes") or 0,
    }


def _h(hours):
    return int(hours) if _num(hours) and float(hours).is_integer() else hours


def lines(root, now, hours=24, listed=None):
    """[감사] block: one head line and up to LINES_MAX - 1 non-zero findings, facts only, BLOCK_CHARS in total."""
    rep = report(root, now, hours=hours, listed=listed)
    h = _h(hours)
    if not rep["wakes"]:
        return [f"[감사] 최근 {h}시간 점검 기록 없음"]
    out = [
        f"[감사] 최근 {h}시간 점검 {rep['wakes']}회, 호출 {rep['calls']}회, "
        f"점검당 실수 {rep['score']['mistakes_per_wake']:.1f}, 빈 점검 {rep['idle_wakes']}회"
    ]
    found = []
    if rep["untouched_groups"]:
        names = ", ".join(GROUP_KO[g] for g in rep["untouched_groups"])
        found.append(f"실험 시작 0건이고 진행 중인 실험도 없는 묶음: {names}임")
    if rep["stale_verdicts"]:
        sv = rep["stale_verdicts"]
        ids = ", ".join(f"#{i}" for i in sv[:IDS_SHOWN])
        ids += f" 외 {len(sv) - IDS_SHOWN}건" if len(sv) > IDS_SHOWN else ""
        found.append(
            f"판정 뒤 점검 {STALE_WAKES}회 이상 지나도 같은 묶음 새 실험 없음: {ids}"
        )
    ed = rep["edits"]
    if ed["refused"]:
        reason, k = sorted(ed["reasons"].items(), key=lambda x: (-x[1], x[0]))[0]
        found.append(
            f"정책 수정 {ed['asked']}건 중 {ed['refused']}건 거절됨, 최다 사유 '{reason}' {k}건임"
        )
    if rep["repeat_calls"]:
        name, t = sorted(rep["tools"].items(), key=lambda x: (-x[1]["repeats"], x[0]))[
            0
        ]
        found.append(
            f"같은 점검 안 같은 인자 반복 호출 {rep['repeat_calls']}회, 최다 {name} {t['repeats']}회임"
        )
    errs = [
        (rep["model_errors"], "모델 오류"),
        (rep["no_done"], "done 없이 끝남"),
        (rep["crashes"], "충돌"),
    ]
    if any(k for k, _ in errs):
        found.append(", ".join(f"{label} {k}회" for k, label in errs if k) + "임")
    if rep["unused_tools"]:
        un = rep["unused_tools"]
        more = f" 외 {len(un) - UNUSED_SHOWN}개" if len(un) > UNUSED_SHOWN else ""
        found.append(f"안 쓴 도구 {len(un)}개: {', '.join(un[:UNUSED_SHOWN])}{more}임")
    total = len(out[0])
    for f in found:
        f = "  " + (f if len(f) <= LINE_CHARS else f[: LINE_CHARS - 1] + "…")
        if len(out) >= LINES_MAX:
            break
        if (
            total + 1 + len(f) > BLOCK_CHARS
        ):  # a line that does not fit is skipped; a shorter later one may
            continue
        out.append(f)
        total += 1 + len(f)
    return out


def tool_table(root, now, hours=24, listed=None):
    """One row per listed or used tool: {name, uses, wakes, obs_chars_mean, repeats, listed}, by uses desc then name."""
    rep = report(root, now, hours=hours, listed=listed)
    shown = set(x for x in (listed or ()) if isinstance(x, str))
    out = []
    for name in shown | set(rep["tools"]):
        t = rep["tools"].get(name) or {}
        out.append(
            {
                "name": name,
                "uses": t.get("uses", 0),
                "wakes": t.get("wakes", 0),
                "obs_chars_mean": t.get("obs_chars_mean", 0),
                "repeats": t.get("repeats", 0),
                "listed": name in shown,
            }
        )
    return sorted(out, key=lambda r: (-r["uses"], r["name"]))
