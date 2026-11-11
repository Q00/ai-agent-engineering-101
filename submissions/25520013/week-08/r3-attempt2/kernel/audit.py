"""Process audit of the operator model (meta-harness): how it works, not what it decides. Plain code, no model calls.

report(root, now) reads the trajectory (<root>/a2/trajectory.jsonl, last TRAJ_BYTES) and the experiment ledger and
counts, over the compact wakes in the window, what the operator ignored (tools, experiment groups, verdicts), wasted
(repeated calls, idle wakes) and got wrong (refused edits, model errors, wakes without done). lines() is the [감사]
block of the state card; tool_table() is the per-tool table for the auditor model. Missing or corrupt files count as
zero; nothing here raises on bad rows. Nothing is masked here (callers mask).

role (shop, buyer: the role operators) limits everything to that role's rows (the trajectory rows its process wrote,
its experiments) and adds per tool the calls outside its recommended list and the tool_search queries;
role_tool_table() is that role's table for the auditor. Without a role the report is the single operator's, as before.
"""

import json
import math
import os

from kernel import code_edit
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


def _wakes(root, role=None):
    """-> (wakes, poll_crash_ts). wakes: every operator_wake row in file order with the tool rows written since the
    previous wake row ("tools": [(name, args)], "obs": [(name, obs_chars)]). role: only that role's rows."""
    wakes, crashes, tools = [], [], []
    for r in c.tail_json(os.path.join(root, TRAJ), TRAJ_BYTES):
        if r.get("agent") != "operator" or not _num(r.get("ts")):
            continue
        if role is not None and r.get("role") != role:
            continue
        kind = r.get("kind")
        if kind == "tool":
            name = r.get("name") if isinstance(r.get("name"), str) else "?"
            args = (
                r.get("args")
                if isinstance(r.get("args"), str)
                else json.dumps(r.get("args"), sort_keys=True, default=str)
            )
            oc = r.get("obs_chars") if _num(r.get("obs_chars")) and not r.get("cached") else None
            err = str(r.get("obs_head") or "오류")[:80] if r.get("err") is True else None
            search = (str(r.get("query") or "")[:60], r.get("hits")) if name == "tool_search" else None
            tools.append((name, args, oc, err, r.get("cut") is True, r.get("recommended") is False, search))
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
    if isinstance(res.get("code"), dict) and res["code"].get("ok") is True:
        return False
    return not _posted(res)


def _ledger(root):
    try:
        rows = ex.load(root)
    except Exception:  # noqa: BLE001 - a ledger the loader cannot read counts as empty
        rows = []
    return [e for e in rows if isinstance(e, dict)]


def _groups(role):
    return GROUPS if role is None else tuple(g for g in GROUPS if (g == "buyer") == (role == "buyer"))


def report(root, now, hours=24, since=None, listed=None, role=None):
    """Audit counts over [since, now] when since is given, else [now - hours h, now]. See the module docstring."""
    start = since if _num(since) else now - hours * 3600
    all_wakes, poll_crashes = _wakes(root, role)
    searches = []
    in_win = [w for w in all_wakes if start <= w["ts"] <= now]
    wakes = [w for w in in_win if _compact(w)]
    n = len(wakes)
    calls = sum(w["result"]["calls"] for w in wakes)
    chars = sum(
        w["result"].get("prompt_chars")
        for w in wakes
        if _num(w["result"].get("prompt_chars"))
    )

    tools, obs, unfounded = {}, {}, []
    for w in wakes:
        seen = {}
        g = (w.get("result") or {}).get("grounding") if isinstance(w.get("result"), dict) else None
        g = g if isinstance(g, dict) else {}
        unfounded += [str(x)[:20] for x in (g.get("unfounded") if isinstance(g.get("unfounded"), list) else [])]
        for name, args, oc, err, cut, outside, search in w["_tools"]:
            if search is not None:
                searches.append(search)
            t = tools.setdefault(
                name,
                {
                    "uses": 0,
                    "wakes": 0,
                    "obs_chars_mean": 0,
                    "obs_chars_max": 0,
                    "repeats": 0,
                    "errors": 0,
                    "last_error": "",
                    "cut": 0,
                    "uncited": 0,
                },
            )
            t["uses"] += 1
            t["cut"] += cut
            if role is not None:
                t["outside"] = t.get("outside", 0) + outside
            if err:
                t["errors"] += 1
                t["last_error"] = err
            if not any(k[0] == name for k in seen):
                t["wakes"] += 1
            if (name, args) in seen:
                t["repeats"] += 1
            seen[(name, args)] = True
            if oc is not None:
                obs.setdefault(name, []).append(oc)
        for name in g.get("uncited") if isinstance(g.get("uncited"), list) else []:
            if name in tools:
                tools[name]["uncited"] += 1
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
    if role is not None:
        rows = [e for e in rows if ex.role_of(e) == role]
    groups = _groups(role)
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
            for g in groups
        },
    }
    busy = {group_of(e.get("section"), e.get("key")) for e in running}
    untouched = [g for g in groups if exps["by_group"][g] == 0 and g not in busy]

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
        "tool_errors": sum(t["errors"] for t in tools.values()),
        "cut_reads": sum(t["cut"] for t in tools.values()),
        "uncited_reads": sum(t["uncited"] for t in tools.values()),
        "unfounded_numbers": unfounded[:20],
        "unused_tools": sorted(listed - set(tools)),
        "edits": edits,
        "experiments": exps,
        "untouched_groups": untouched,
        "stale_verdicts": stale,
        "hit": {
            "ended": len(decided),
            "good": sum(1 for e in decided if e["status"] in GOOD),
        },
        "code": code_edit.summary(root, now, since=start),  # not in score
    }
    if role is not None:
        zero = [q for q, hits in searches if hits == 0]
        rep.update(role=role, outside_calls=sum(t.get("outside", 0) for t in tools.values()),
                   searches=len(searches), search_zero=len(zero), search_zero_queries=zero[-5:])
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


def lines(root, now, hours=24, listed=None, role=None):
    """[감사] block: one head line and up to LINES_MAX - 1 non-zero findings, facts only, BLOCK_CHARS in total. role: that
    role's report (listed is then its recommended list)."""
    rep = report(root, now, hours=hours, listed=listed, role=role)
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
    if rep.get("tool_errors"):  # a failing tool is the kernel's fault or a wrong call: either way it must be seen
        name, t = sorted(rep["tools"].items(), key=lambda x: (-x[1]["errors"], x[0]))[0]
        found.insert(
            0,
            f"도구가 오류로 답한 호출 {rep['tool_errors']}회, 최다 {name} {t['errors']}회: '{t['last_error'][:60]}'. "
            "인자를 고쳐 부르거나 다른 도구를 쓰고, 계속되면 notes에 적음",
        )
    if rep.get("unfounded_numbers"):
        u = rep["unfounded_numbers"]
        found.insert(
            1 if rep.get("tool_errors") else 0,
            f"실험 근거(reason, expect)에 쓴 숫자 중 이번 점검에 본 자료에 없는 것 {len(u)}개 (예: {', '.join(u[:4])}). "
            "근거 숫자는 도구로 읽은 값을 그대로 씀",
        )
    if rep.get("uncited_reads"):
        name, t = sorted(rep["tools"].items(), key=lambda x: (-x[1]["uncited"], x[0]))[0]
        found.append(
            f"읽고도 결정이나 결론에 숫자를 하나도 쓰지 않은 도구 결과 {rep['uncited_reads']}회, 최다 {name} {t['uncited']}회임"
        )
    if rep.get("cut_reads"):
        name, t = sorted(rep["tools"].items(), key=lambda x: (-x[1]["cut"], x[0]))[0]
        found.append(f"길어서 잘린 채 본 도구 결과 {rep['cut_reads']}회, 최다 {name} {t['cut']}회: 인자로 범위를 좁혀 부름")
    if rep["repeat_calls"]:
        name, t = sorted(rep["tools"].items(), key=lambda x: (-x[1]["repeats"], x[0]))[
            0
        ]
        found.append(
            f"같은 점검 안 같은 인자 반복 호출 {rep['repeat_calls']}회, 최다 {name} {t['repeats']}회임"
        )
    code = rep.get("code") or {}
    if (code.get("reverted_canary") or 0) + (code.get("reverted_eval") or 0) + (code.get("refused_restart") or 0):
        lr = code.get("last_revert") or {}
        found.insert(0, f"코드 수정이 되돌려짐 (감시 {code.get('reverted_canary', 0)}건, 비교 {code.get('reverted_eval', 0)}건, "
                        f"재시작 실패 {code.get('refused_restart', 0)}건), 마지막 #{lr.get('id')}: '{str(lr.get('why') or '')[:80]}'. "
                        "같은 원인을 다시 고치기 전에 이유를 읽음")
    if (code.get("refused") or 0) >= 2:
        cls, k = sorted(code["refused_by"].items(), key=lambda x: (-x[1], x[0]))[0]
        found.append(f"코드 수정 제안 {code['refused']}건 거절됨, 최다 사유 '{code_edit.CLS_KO.get(cls, cls)}' {k}건임")
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
    if rep.get("search_zero"):
        found.append(f"tool_search 결과 0개 {rep['search_zero']}회 (예: {', '.join(rep['search_zero_queries'][-3:])}): "
                     "낱말을 바꿔 다시 찾음")
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
                "errors": t.get("errors", 0),
                "last_error": t.get("last_error", ""),
                "cut": t.get("cut", 0),
                "uncited": t.get("uncited", 0),
                "listed": name in shown,
            }
        )
    return sorted(out, key=lambda r: (-r["uses"], r["name"]))


def role_tool_table(root, now, role, recommended, hours=24):
    """One role's tools for the auditor: rows {name, calls, outside, recommended, errors, cut, uncited} for every tool
    the role called or has recommended (calls desc, then name), and its tool_search numbers {searches, search_zero,
    queries: the latest zero-hit queries}."""
    rep = report(root, now, hours=hours, listed=recommended, role=role)
    rec = set(x for x in (recommended or ()) if isinstance(x, str))
    rows = []
    for name in rec | set(rep["tools"]):
        t = rep["tools"].get(name) or {}
        rows.append({"name": name, "calls": t.get("uses", 0), "outside": t.get("outside", 0), "recommended": name in rec,
                     "errors": t.get("errors", 0), "cut": t.get("cut", 0), "uncited": t.get("uncited", 0)})
    rows.sort(key=lambda r: (-r["calls"], r["name"]))
    return rows, {"searches": rep["searches"], "search_zero": rep["search_zero"], "queries": rep["search_zero_queries"]}
