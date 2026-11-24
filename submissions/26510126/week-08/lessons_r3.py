"""Each team area's lessons: one short reference the specialist reads, rewritten from the verdict ledger now and then.

Until 11/9 a specialist saw the last 12 versions of its numbers and the last 6 number verdicts, so anything older was
gone or passed on by word of mouth ("judged in both directions"). The memory study (Anthropic, components of agent
memory, 2026) found the learning loop closed when the model kept one consolidated reference with each rule's evidence
and whether it was verified, rewrote it between sessions, and fixed wrong notes against the record.

  facts(area)   the record, by code: the area's number verdicts as re-judged (rejudge_r3: VERIFIED or UNVERIFIED),
                the code changes to its files with their verdicts, the buyer's A/B verdicts. Each fact has an id.
  dream(area)   a model (sonnet) rewrites the area's lessons from its old lessons and the facts: at most MAX lessons,
                each a rule, the fact ids it rests on, a status (verified / unverified / contradicted) and a trap.
                Code drops a lesson citing no fact or an unknown one, so no evidence is invented; the old version
                goes into the history (where a rule came from and what it replaced).
  text(area)    what the specialist reads, under MAX_CHARS.
The meta cycle (meta_r3.cycle) dreams the most overdue area once its DREAM_EVERY has passed.
"""
import json
import time
from pathlib import Path

import metrics_r3
import rejudge_r3

HERE = Path(__file__).resolve().parent
OUT = Path("logs") / "lessons_r3.json"
DREAM_EVERY = 3 * 3600
MAX, MAX_CHARS, KEEP_HISTORY = 8, 3000, 10
TITLES = {"sales": "head of sales", "merch": "head of merchandising", "buying": "head of buying",
          "community": "community lead"}
# supervisor and other hand-made rows name no files: their words place them (the 11/9 restore of 4d10151 was missed)
WORDS = {"merch": ("restock", "stock", "dear_depth", "heat", "sku"), "buying": ("buyer", "C_CAP", "MAX_RAISE"),
         "community": ("forum", "post"), "sales": ("seller", "ask_frac", "floor", "haggl")}
# code verdicts before 46776c3 (11/9 11:17:58) compared raw shop numbers 3 h before and after: the hour of day
CODE_FIX_T = 1794190678
FILES = {"merch": ("restock_r3.py", "demand_r3.py", "prices_r3.py"), "buying": ("buyer_r3.py", "buyer.py"),
         "community": ("forum_r3.py",), "sales": ("seller_r3.py",)}
STATUSES = ("verified", "unverified", "contradicted")


def merch_keys():
    import meta_team_r3
    return set(meta_team_r3.MERCH_KEYS)


def stamp(t):
    return time.strftime("%m/%d %H:%M", time.localtime(t or 0))


def facts(area, base=HERE):
    """[(id, t, text)] for the area, oldest first: its record as code and the re-judge read it."""
    base = Path(base)
    rows = metrics_r3.jl(base / rejudge_r3.CHANGES)
    try:
        again = json.loads((base / rejudge_r3.OUT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        again = {}
    merch, out = merch_keys(), []
    for r in rows:
        src, change = r.get("source"), str(r.get("change") or "")
        if src == "inner loop (number)" and not change.startswith("fast guard"):
            keys = {p.split(" ")[0] for p in change.split(", ") if " -> " in p}
            mine = keys & merch if area == "merch" else keys - merch if area == "sales" else set()
            if mine:
                out.append((r["t"], f"number {change}: {rejudge_r3.note(again.get(rejudge_r3.key(r)))}"))
        elif src == "inner loop (buyer A/B)" and area == "buying":
            out.append((r["t"], f"buyer A/B {change}: {str(r.get('verdict'))[:160]}"))
        elif src in ("meta (auto)", "supervisor", "data", "bug", "design", "measured failure", "user") and \
                (any(f in (r.get("files") or []) or f in change for f in FILES[area]) or
                 (not r.get("files") and any(w in change for w in WORDS[area]))):
            v = str(r.get("verdict") or r.get("after") or "no verdict")[:240]
            if src == "meta (auto)" and not r.get("ab_key") and r["t"] < CODE_FIX_T and r.get("verdict"):
                v += " [raw before/after, reads the hour of day: weak]"
            out.append((r["t"], f"code [{src}] {change[:240]}: {v}"))
    out.sort(key=lambda x: x[0])
    return [(f"F{i + 1}", t, f"{stamp(t)} {text}") for i, (t, text) in enumerate(out)]


def load(base=HERE):
    try:
        return json.loads((Path(base) / OUT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


DREAM = """You keep the lessons of the {title} of a shop-and-buyer agent team in an online market of AI agents
(Round 3, until 11/17; the aim is board points: shop book, revenue and stars, buyer saved). The lessons are the one
reference the {title} reads every cycle, so they must be short, true to the record and useful for the next change.

The record (facts, oldest first; numbers re-judged on our share of the market's paid sales are VERIFIED, MIXED ones had
another change in the same hours and are NO verdict, those judged before 11/9 11:13 on raw margin/h are UNVERIFIED
because they read the hour of day; a lesson must never rest on MIXED facts, and a bound or a value now is not a
lesson; code changes carry their own
verdicts; code verdicts marked "raw before/after" read the hour of day and are weak, A/B ones compare the same
hours; in an A/B the arm named before "won" is the winner; a later row that undoes or corrects a change supersedes
it, so never recommend a value a later row undid):
{facts}

The supervisor's standing orders now (they win over any lesson: drop or rewrite a lesson that contradicts them, e.g.
one that demands an A/B the orders no longer require, or judges an area on another area's metric):
{orders}

The lessons now (may be empty or stale):
{lessons}

Rewrite the lessons: at most {max}. Each lesson is a rule the {title} can act on, the ids of the facts it rests on,
a status, and a trap (how acting on it went wrong, or would). Status: "verified" when it rests on VERIFIED facts or
code verdicts measured on the board's terms; "unverified" when it rests only on UNVERIFIED facts (say so, they are
weak); "contradicted" when newer facts point the other way (keep it so the trap is remembered). Quote only numbers
that appear in the facts. Drop a lesson the facts no longer support; merge duplicates; prefer recent facts.
Answer with JSON only: {{"lessons": [{{"rule": "...", "evidence": ["F3", "F9"], "status": "verified", "trap": "..."}}]}}"""


CHECK = """Check each lesson below against the facts it cites. A lesson passes only if its cited facts say what the
rule claims (same number, same direction, same metric, and the facts' own verdict words: MIXED and "no verdict" are
not evidence, UNVERIFIED is weak). Fail a lesson that reads more into its facts than they say, cites the wrong
metric for its area (the {title}'s board terms: {terms}), or contradicts the standing orders below.

Standing orders:
{orders}

Lessons with their cited facts:
{items}

Answer with JSON only: {{"pass": [indexes of passing lessons], "fail": {{"<index>": "why, in a few words"}}}}"""
TERMS = {"sales": "shop book, revenue, stars_rank", "merch": "shop book, revenue, stars_rank",
         "buying": "buyer saved, deals", "community": "the forum rating (stances, dislikes), posts"}


def grounded(x, text_of):
    """A lesson's status as its facts allow: 'verified' needs at least one cited fact that is not MIXED, UNVERIFIED or
    without a verdict (the memory study: mark which facts are confirmed; 11/9 a lesson called an MIXED arm verified)."""
    weak = ("MIXED", "UNVERIFIED", "no verdict", "not re-judged", "not judged", "weak", "raw before/after")
    if x["status"] == "verified" and all(any(w in text_of.get(e, "") for w in weak) for e in x["evidence"]):
        return dict(x, status="unverified")
    return x


async def check(area, kept, text_of, orders, ask):
    """(lessons a second reading finds true to their cited facts, {index: why failed} or None when unchecked).
    Memory study: check notes against the record. A contradicted lesson is kept as it is (its facts point against
    its rule by definition; its trap is the point). The check sees the whole orders and whole facts (11/9 review:
    cut to 4,000 and 300 characters it missed rule 7 and the "weak" tails). Fails open, marked unchecked."""
    todo = [(i, x) for i, x in enumerate(kept) if x["status"] != "contradicted"]
    if not todo:
        return kept, {}
    items = "\n".join(f"[{i}] ({x['status']}) {x['rule']}\n" + "\n".join(f"    {e}: {text_of.get(e, '?')[:1200]}"
                                                                         for e in x["evidence"])
                      for i, x in todo)
    ans = await ask(CHECK.format(title=TITLES[area], terms=TERMS[area], orders=orders[:12000], items=items),
                    model="sonnet", timeout=180, max_chars=40_000)
    ok = (ans or {}).get("pass")
    if not isinstance(ok, list):
        return kept, None
    ok = {int(i) for i in ok if str(i).strip().lstrip("-").isdigit()}
    fail = (ans or {}).get("fail") if isinstance((ans or {}).get("fail"), dict) else {}
    return [x for i, x in enumerate(kept) if x["status"] == "contradicted" or i in ok], fail


def valid(lessons, ids):
    """The model's lessons that cite only known facts, with a known status, at most MAX."""
    out = []
    for x in lessons if isinstance(lessons, list) else []:
        if not isinstance(x, dict) or not isinstance(x.get("rule"), str) or not x["rule"].strip():
            continue
        ev = [e for e in x.get("evidence") or [] if isinstance(e, str)]
        if not ev or any(e not in ids for e in ev) or x.get("status") not in STATUSES:
            continue
        out.append({"rule": x["rule"].strip()[:240], "evidence": ev, "status": x["status"],
                    "trap": str(x.get("trap") or "")[:160]})
    return out[:MAX]


async def dream(area, base=HERE, ask=None, now=None):
    """Rewrite the area's lessons once; returns how many lessons were kept, or None when the model gave nothing."""
    import harness
    ask = ask or harness.ask
    base, now = Path(base), now or time.time()
    fs = facts(area, base)
    if not fs:
        return None
    shown = fs[-80:]                                    # the newest 80 facts: the prompt stays near 15,000 characters
    data = load(base)
    cur = data.get(area) or {}
    try:                                                # 11/9 audit: lessons of 14:39 still demanded A/Bs the 13:2x orders dropped
        import board_terms_r3                           # rendered: the numbers the team reads, not placeholders
        orders = board_terms_r3.render((base / "standing_orders_r3.md").read_text(encoding="utf-8"))[:12000]
    except OSError:
        orders = "(none)"
    prompt = DREAM.format(title=TITLES[area], max=MAX, facts="\n".join(f"{i}: {x}" for i, _, x in shown),
                          orders=orders, lessons=json.dumps(cur.get("lessons") or [], ensure_ascii=False))
    # 45,000: with the rendered orders the merch prompt reached 31,748 characters and the harness refused it (11/9 17:47)
    ans = await ask(prompt, model="sonnet", timeout=240, max_chars=45_000)
    kept = valid((ans or {}).get("lessons"), {i for i, _, _ in shown})
    if not kept:
        # a failed rewrite waits a full DREAM_EVERY like a good one (it was retried every 15-minute cycle)
        data = load(base)
        data.setdefault(area, {})["tried"] = now
        path = base / OUT
        path.parent.mkdir(exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        return None
    text_of = {i: x for i, _, x in shown}
    kept = [grounded(x, text_of) for x in kept]
    kept, failed = await check(area, kept, text_of, orders, ask)
    # nothing passed: store the empty set and move t on (11/9 review: returning None left the old, unchecked lessons
    # injected and re-ran two sonnet calls every cycle)
    when = {i: t for i, t, _ in fs}
    import re
    for x in kept:                                      # the facts' times travel with a lesson; ids are renumbered
        x["when"] = [stamp(when[e]) for e in x["evidence"]]
        for k in ("rule", "trap"):                      # the specialist sees no ids: "F13 -53%" -> "11/05 23:29 -53%"
            x[k] = re.sub(r"\bF(\d+)\b", lambda m: stamp(when[m.group(0)]) if m.group(0) in when else m.group(0), x[k])
    history = (cur.get("history") or []) + ([{"t": cur["t"], "lessons": cur["lessons"]}] if cur.get("lessons") else [])
    # read again just before writing and change only this area: on 11/9 12:07 a hand-run rewrite of sales, loaded before
    # the meta cycle wrote community's lessons, saved over them
    data = load(base)
    data[area] = {"t": now, "facts": len(fs), "lessons": kept, "history": history[-KEEP_HISTORY:],
                  "failed_check": failed if failed is not None else "unchecked"}
    path = base / OUT
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return len(kept)


def due(base=HERE, now=None):
    """The most overdue area whose DREAM_EVERY has passed, or None."""
    now, data = now or time.time(), load(base)
    late = [(now - max((data.get(a) or {}).get("t", 0), (data.get(a) or {}).get("tried", 0)), a) for a in TITLES]
    late = [x for x in late if x[0] >= DREAM_EVERY]
    return max(late)[1] if late else None


def text(area, base=HERE):
    """The area's lessons for its specialist, or "" before the first dream."""
    cur = load(base).get(area) or {}
    if not cur.get("lessons"):
        return ""
    out = (f"\nYour lessons (rewritten {stamp(cur['t'])} from the verdict ledger; verified rests on judged facts, "
           f"unverified only on raw verdicts, contradicted was overturned and is kept for its trap; name the ids "
           f"of those that shape your answer in lessons_used):\n")
    for i, x in enumerate(cur["lessons"]):
        line = f"- L{i + 1} [{x['status']}] {x['rule']} (facts {', '.join(x.get('when') or [])}" + \
               (f"; trap: {x['trap']}" if x.get("trap") else "") + ")\n"
        if len(out) + len(line) > MAX_CHARS:
            break
        out += line
    return out


USE = Path("logs") / "lessons_use_r3.jsonl"
HIDE_SHARE = 0.25


def shown_now(now):
    """{area: shown?} for this cycle: each area's lessons are hidden in HIDE_SHARE of cycles, drawn from the cycle's
    quarter-hour and the area (stable within a cycle, independent across areas)."""
    import hashlib
    slot = int(now // 900)
    return {a: int(hashlib.sha1(f"{slot}|{a}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF >= HIDE_SHARE for a in TITLES}


def record_use(answers, base=HERE, now=None, shown=None):
    """One line per specialist answer: the lessons it named, the version it read and the numbers it proposed."""
    now, data = now or time.time(), load(base)
    path = Path(base) / USE
    path.parent.mkdir(exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for role, ans in answers.items():
            used = (ans or {}).get("lessons_used")
            used = [u for u in used if isinstance(u, str) and u.startswith("L")] if isinstance(used, list) else []
            prop = (ans or {}).get("proposal") if isinstance((ans or {}).get("proposal"), dict) else {}
            f.write(json.dumps({"t": round(now), "area": role, "used": used, "proposal": sorted(prop),
                                "shown": (shown or {}).get(role, True),
                                "version": (data.get(role) or {}).get("t")}) + "\n")


def outcome(r, again):
    """A ledger row's verdict in one word: verified (re-judged), mixed, flat, kept / not taken (buyer A/B), pending."""
    if r.get("source") == "inner loop (buyer A/B)":
        a = str(r.get("after") or "")
        return "kept" if a.startswith("kept") else "not taken" if a.startswith("not taken") else "pending"
    st = (again.get(rejudge_r3.key(r)) or {}).get("status") or "pending"
    return {"confirmed": "verified", "reversed": "verified"}.get(st, st)


def evaluate(base=HERE, hours=48, now=None):
    """Memory evaluation (the memory study tested agents with and without memory). The comparison that counts is
    shown vs hidden (meta hides an area's lessons in HIDE_SHARE of cycles): how the changes that followed were
    judged. Self-reported use (lessons_used) is kept as a side count. A change is matched to an answer when a number
    it proposed (shop numbers; buyer numbers via their A/B rows) changed within 20 minutes after it; fast-guard
    reverts are left out. Forum weights have no ledger rows, so community is counted but not judged."""
    now, base = now or time.time(), Path(base)
    uses = [u for u in metrics_r3.jl(base / USE) if now - u.get("t", 0) < hours * 3600]
    try:
        again = json.loads((base / rejudge_r3.OUT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        again = {}
    changes = [r for r in metrics_r3.jl(base / rejudge_r3.CHANGES)
               if (r.get("source") == "inner loop (number)" and not str(r.get("change", "")).startswith("fast guard"))
               or r.get("source") == "inner loop (buyer A/B)"]
    out = {}
    for area in TITLES:
        us = [u for u in uses if u["area"] == area]
        o = {"answers": len(us), "hidden": sum(1 for u in us if not u.get("shown", True)),
             "named_a_lesson": sum(1 for u in us if u["used"]), "changes": {"shown": {}, "hidden": {}}}
        for u in us:
            keys = set(u.get("proposal") or [])
            for r in changes:
                words = str(r.get("change", "")).replace("buyer ", "", 1)
                if keys and 0 <= r["t"] - u["t"] <= 1200 and \
                        keys & {p.split(" ")[0] for p in words.split(", ") if " -> " in p}:
                    side = o["changes"]["shown" if u.get("shown", True) else "hidden"]
                    st = outcome(r, again)
                    side[st] = side.get(st, 0) + 1
        out[area] = o
    return out


if __name__ == "__main__":
    import asyncio
    import sys
    for a in (sys.argv[1:] or TITLES):
        print(a, len(facts(a)), "facts;", asyncio.run(dream(a)), "lessons")
        print(text(a))
