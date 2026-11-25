"""memory.* and rec.* for the outer runtime (HARNESS.md 2.1, 2.3, 3.2, 5): summaries, profiles, stances, the brief.

They act on ctx.situation, which outer.slice fills: {"epoch", "slice", "night", "proposal_id"}.

- rec.summary: one analyst's slice summary; every finding needs evidence (ledger numbers, event kinds, tool names).
- rec.profile: one participant's profile. The observed part is filled from the collected facts by the tool, not
  written by the model; the model writes the interpretation, each claim with evidence.
- rec.stance: the strategy agent's stance toward one participant. Avoiding (refuse, block) and lifting it always
  wait for the user (D16); other stances are set directly only inside the delegation, else they wait too.
- rec.brief: the 2-hourly brief (HARNESS.md 5). Each option is a change to the strategy in force; the tool
  applies it to a copy and checks the schema, so an option the user accepts can be installed as it is. At night
  the brief is a report: no options.
- rec.audit: the audit agent's opinion on that brief.
"""
import copy
import json

from core import atomic, paths, reflex, schema
from tools.registry import Refused, tool

_EVID = {"type": "array", "items": {"type": "string", "minLength": 1}}
_FINDING = {"type": "object", "required": ["claim", "evidence"],
            "properties": {"claim": {"type": "string", "minLength": 1, "maxLength": 400}, "evidence": _EVID}}
CHANGE_KEYS = ("priorities", "phase", "limits", "experiment", "shop_card", "context",
               "playbook", "restock", "buyer", "repeat_buyer")   # 11/4: the rules' numbers (HARNESS 14)


def _sit(ctx, key):
    v = ctx.situation.get(key)
    if v is None:
        raise Refused(f"no {key} in this run", "rec.* tools are for the outer runtime's slice")
    return v


def _need_evidence(findings):
    if any(not isinstance(f, dict) for f in findings):      # 11/4 05:3x: a brief sent plain strings and crashed here
        raise Refused("each finding is an object {claim, evidence: [...]}, not a string",
                      'write [{"claim": "...", "evidence": ["ledger 123", "eval.funnel ..."]}]')
    bad = [f["claim"][:60] for f in findings if not f.get("evidence")]
    if bad:
        raise Refused(f"findings without evidence: {bad}", "add ledger numbers, event kinds or tool results")


def _slice_dir(ctx, epoch=None, slice_id=None):
    return paths.memory() / "epochs" / (epoch or _sit(ctx, "epoch")) / (slice_id or _sit(ctx, "slice"))


# --- reading

@tool("memory.summary", "An analyst's slice summary (market or self). Without epoch/slice: the newest one.",
      {"type": "object", "required": ["kind"],
       "properties": {"kind": {"type": "string", "enum": ["market", "self"]}, "epoch": {"type": "string"},
                      "slice": {"type": "string"}}})
def summary(ctx, kind, epoch=None, slice=None):
    if epoch and slice:
        s = atomic.read_json(_slice_dir(ctx, epoch, slice) / f"{kind}.json")
        if s is None:
            raise Refused(f"no {kind} summary for {epoch}/{slice}")
        return s
    files = sorted((paths.memory() / "epochs").glob(f"*/*/{kind}.json"), key=lambda f: f.parent.name)
    if not files:
        raise Refused(f"no {kind} summary yet", "the first slice writes one")
    return atomic.read_json(files[-1])


@tool("memory.profile", "One participant's full profile (as a shop and as a buyer): observed facts, our analyst's "
      "interpretation with evidence, whether it changed since the last slice.",
      {"type": "object", "required": ["name"], "properties": {"name": {"type": "string", "minLength": 1}}})
def profile(ctx, name):
    p = atomic.read_json(paths.memory() / "profiles" / f"{name}.json")
    if p is None:
        raise Refused(f"no profile for {name}", "memory.profiles lists the participants we have profiles for")
    return p


@tool("memory.profiles", "Participants we have profiles for, newest first: name, changed, updated, one-line summary.",
      {"type": "object", "properties": {"only": {"type": "string", "enum": ["all", "changed"]},
                                        "limit": {"type": "integer", "minimum": 1, "maximum": 100}}})
def profiles(ctx, only="all", limit=30):
    rows = []
    for f in (paths.memory() / "profiles").glob("*.json"):
        p = atomic.read_json(f, default=None) or {}
        if only == "changed" and not p.get("changed"):
            continue
        rows.append({"name": p.get("name"), "changed": p.get("changed"), "updated": p.get("updated"),
                     "as_shop": (p.get("as_shop") or {}).get("summary"), "as_buyer": (p.get("as_buyer") or {}).get("summary")})
    rows.sort(key=lambda r: r["updated"] or 0, reverse=True)
    return {"count": len(rows), "profiles": rows[:limit]}


# --- writing

@tool("rec.summary", "End the analysis: write this slice's summary (one paragraph) and findings, each with evidence.",
      {"type": "object", "required": ["summary", "findings"],
       "properties": {"summary": {"type": "string", "minLength": 1, "maxLength": 1500},
                      "findings": {"type": "array", "items": _FINDING}}}, side_effect="memory/epochs")
def rec_summary(ctx, summary, findings):
    kind = _sit(ctx, "kind")
    _need_evidence(findings)
    doc = {"kind": kind, "epoch": ctx.situation["epoch"], "slice": ctx.situation["slice"], "ts": ctx.now(),
           "by": ctx.caller, "summary": summary, "findings": findings}
    atomic.write_json(_slice_dir(ctx) / f"{kind}.json", doc)
    return {"action": "summary", "kind": kind, "findings": len(findings)}


@tool("rec.profile", "Write one participant's profile for this slice: a one-line summary and interpretation per "
      "side (shop, buyer), each claim with evidence; changed=true when its strategy looks different from before. "
      "The observed facts are filled in by the tool from what was collected.",
      {"type": "object", "required": ["name", "side", "summary", "interpreted"],
       "properties": {"name": {"type": "string", "minLength": 1}, "side": {"type": "string", "enum": ["shop", "buyer"]},
                      "summary": {"type": "string", "minLength": 1, "maxLength": 300},
                      "interpreted": {"type": "array", "items": _FINDING}, "changed": {"type": "boolean"}}},
      side_effect="memory/profiles")
def rec_profile(ctx, name, side, summary, interpreted, changed=False):
    _need_evidence(interpreted)
    path = paths.memory() / "profiles" / f"{name}.json"
    old = atomic.read_json(path, default=None)
    if old:
        atomic.append_line(paths.memory() / "profiles" / f"{name}.history.jsonl", json.dumps(old, ensure_ascii=False))
    live = atomic.read_json(paths.memory() / "facts" / "live.json", default={}) or {}
    observed = {"facts": (live.get("shop" if side == "shop" else "customer") or {}).get(name)}
    if side == "shop":
        s = reflex.load()["shops"].get(name)
        observed["with_our_buyer"] = {k: s[k] for k in ("deals", "fair", "cheated", "paid", "unpaid")} if s else None
    p = old or {"name": name, "first": ctx.now()}
    p[f"as_{side}"] = {"summary": summary, "observed": observed, "interpreted": interpreted, "slice": ctx.situation.get("slice")}
    p.update(updated=ctx.now(), changed=bool(changed))
    atomic.write_json(path, p)
    return {"recorded": name, "side": side, "changed": bool(changed)}


@tool("rec.stance", "Set our stance toward one participant as a shop or as a buyer, with why and evidence. Set "
      "avoid=true for any stance that refuses, blocks or stops dealing with them, or lifts such a stance: those wait "
      "for the user. Other stances apply directly only inside the delegation.",
      {"type": "object", "required": ["name", "side", "stance", "avoid", "why", "evidence"],
       "properties": {"name": {"type": "string", "minLength": 1}, "side": {"type": "string", "enum": ["shop", "buyer"]},
                      "stance": {"type": "string", "minLength": 1, "maxLength": 60}, "avoid": {"type": "boolean"},
                      "why": {"type": "string", "minLength": 1, "maxLength": 300}, "evidence": _EVID}},
      side_effect="memory/strategy/counterparts")
def rec_stance(ctx, name, side, stance, why, evidence, avoid):
    if not evidence:
        raise Refused("a stance needs evidence")
    avoid = avoid or any(w in stance.lower() for w in ("avoid", "block", "black", "refuse", "ban", "exclude", "stop", "lift", "차단", "거절", "피하"))
    allowed = "stances" in (ctx.strategy.get("delegation") or {}).get("auto", [])
    entry = {"stance": stance, "why": why, "evidence": evidence, "set_by": ctx.caller, "since": ctx.now()}
    if avoid or not allowed:
        atomic.append_line(paths.memory() / "strategy" / "counterparts_pending.jsonl",
                           json.dumps({"name": name, "side": side, **entry}, ensure_ascii=False))
        return {"pending_user": True, "why": "avoiding and lifting are the user's" if avoid else "outside the delegation"}
    path = paths.memory() / "strategy" / "counterparts.json"
    cp = atomic.read_json(path, default={}) or {}
    cp.setdefault(name, {})[f"as_{side}"] = entry
    cp[name]["approved"] = "delegation"
    atomic.write_json(path, cp)
    return {"pending_user": False, "applied": True}


def apply_change(strategy, change):
    """A copy of the strategy with one option's change applied (dict keys merge one level deep)."""
    bad = [k for k in change if k not in CHANGE_KEYS]
    if bad:
        raise Refused(f"an option may not change {bad}", f"options change only {list(CHANGE_KEYS)}")
    s = copy.deepcopy(strategy)
    change = dict(change)
    for k in ("context", "priorities"):          # 11/4 05:0x: a brief sent context as one string and was refused
        if isinstance(change.get(k), str):
            change[k] = [change[k]]
    for k, v in change.items():
        if k == "playbook" and isinstance(v, dict) and isinstance(s.get(k), dict):   # two levels: playbook.restock.*
            s[k] = {**s[k], **{r: ({**s[k].get(r, {}), **x} if isinstance(x, dict) else x) for r, x in v.items()}}
        elif isinstance(v, dict) and isinstance(s.get(k), dict) and k != "experiment":
            s[k] = {**s[k], **v}
        else:
            s[k] = v
    sc = s.get("shop_card") or {}
    if isinstance(sc.get("description"), str) and len(sc["description"]) > 600:   # the schema's limit: cut, not refuse
        s["shop_card"] = dict(sc, description=sc["description"][:599] + "…")
    s["version"] = strategy.get("version", 0) + 1
    s["epoch"] = f"e{s['version']}"
    s["approved"] = "DRAFT - proposal"
    errors = schema.validate(s, schema.STRATEGY)
    if errors:
        raise Refused(f"the strategy after this change does not fit the schema: {errors[:3]}")
    return s


@tool("rec.brief", "End the strategy turn: write the brief for the user. Options (2-3, none at night) are changes to the "
      "strategy in force (keys: priorities, phase, limits, experiment, shop_card, context, playbook, restock, buyer, "
      "repeat_buyer), each with the score part it "
      "targets, the expected effect, the risk and whether it can be undone; recommend one by id.",
      {"type": "object", "required": ["score", "happened", "hypothesis", "counterparts", "options"],
       "properties": {"score": {"type": "string", "minLength": 1, "maxLength": 600},
                      "happened": {"type": "array", "items": _FINDING},
                      "hypothesis": {"type": "string", "minLength": 1, "maxLength": 600},
                      "counterparts": {"type": "string", "maxLength": 800},
                      "options": {"type": "array", "items": {"type": "object",
                                  "required": ["id", "change", "targets", "expected", "risk", "reversible"],
                                  "properties": {"id": {"type": "string", "minLength": 1}, "change": {"type": "object"},
                                                 "targets": {"type": "string", "minLength": 1},
                                                 "expected": {"type": "string", "minLength": 1},
                                                 "risk": {"type": "string", "minLength": 1},
                                                 "reversible": {"type": "boolean"}}}},
                      "recommendation": {"type": ["string", "null"]}}}, side_effect="logs/strategy/briefs")
def rec_brief(ctx, score, happened, hypothesis, counterparts, options, recommendation=None):
    _need_evidence(happened)
    if ctx.situation.get("night") and options:
        raise Refused("at night the brief is a report only", "leave options empty; decisions are taken in the morning")
    if options and not 2 <= len(options) <= 3:
        raise Refused(f"{len(options)} options", "give 2 or 3 options, or none when nothing needs deciding")
    ids = [o["id"] for o in options]
    if options and recommendation not in ids and isinstance(recommendation, str):
        # 11/4 03:3x: the strategy agent wrote "A_breadth. To the user's question: ..." - take the id it starts with
        lead = next((i for i in sorted(ids, key=len, reverse=True) if recommendation.strip().startswith(i)), None)
        recommendation = lead or recommendation
    if options and recommendation not in ids:
        raise Refused(f"recommendation {recommendation!r} is not one of {ids}")
    strategies = {o["id"]: apply_change(ctx.strategy, o["change"]) for o in options}
    pid = _sit(ctx, "proposal_id")
    doc = {"proposal_id": pid, "slice": ctx.situation.get("slice"), "ts": ctx.now(),
           "version_from": ctx.strategy.get("version"), "score": score, "happened": happened, "hypothesis": hypothesis,
           "counterparts": counterparts, "options": options, "recommendation": recommendation, "strategies": strategies,
           "status": "open" if options else "report"}
    atomic.write_json(paths.memory() / "strategy" / "proposals" / f"{pid}.json", doc)
    atomic.write_json(paths.root() / "logs" / "strategy" / "briefs" / f"{pid}.json",
                      {k: v for k, v in doc.items() if k != "strategies"})
    return {"action": "brief", "proposal_id": pid, "options": ids}


@tool("rec.audit", "End the audit: do you agree with the brief's reading and its recommendation? Points of "
      "disagreement with why, checked against the scoring formula and the data.",
      {"type": "object", "required": ["agrees", "points"],
       "properties": {"agrees": {"type": "boolean"}, "prefer": {"type": ["string", "null"]},
                      "points": {"type": "array", "items": {"type": "object", "required": ["claim", "why"],
                                 "properties": {"claim": {"type": "string", "minLength": 1},
                                                "why": {"type": "string", "minLength": 1}}}}}},
      side_effect="memory/strategy/proposals")
def rec_audit(ctx, agrees, points, prefer=None):
    pid = _sit(ctx, "proposal_id")
    path = paths.memory() / "strategy" / "proposals" / f"{pid}.json"
    doc = atomic.read_json(path)
    if doc is None:
        raise Refused(f"no brief {pid} to audit")
    doc["audit"] = {"agrees": agrees, "prefer": prefer, "points": points, "by": ctx.caller, "ts": ctx.now()}
    atomic.write_json(path, doc)
    pub = paths.root() / "logs" / "strategy" / "briefs" / f"{pid}.json"
    p = atomic.read_json(pub, default=None)
    if p is not None:
        p["audit"] = doc["audit"]
        atomic.write_json(pub, p)
    return {"action": "audit", "agrees": agrees}
