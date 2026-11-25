"""The market's patch notes (https://market.ourolabs.ai/rules), read at every meta-harness cycle.

The rules change during Round 3: Patch 3.2 (11/04) added surge cost and renamed profit to cash, Patch 3.3 (11/05) scored
shops by book instead of cash. The scorecard still ranked cash for hours after 3.3, because nothing told Terra and Astra
that the score had changed. check() parses the page's patch list and its scoring bars, remembers when each patch was first
seen (.market/patch_notes.json), and compares the published weights with scorecard.OBJECTIVE, so the pack says:
- new: patches first seen since the previous check (this cycle's news), recent: those first seen in the last day;
- scoring: the published parts and weights; objective_matches false and mismatch when scorecard.OBJECTIVE disagrees.
No model call. A page that cannot be read or parsed gives {"error": ...} and leaves the record alone.

    python patch_notes.py          # prints what a cycle would see (records nothing)
"""
import html
import json
import os
import re
import time

import httpx

RULES = "https://market.ourolabs.ai/rules"
STATE = os.path.join(".market", "patch_notes.json")
RECENT_HOURS = 24
TOLERANCE = 0.005      # published weights come from drawn bar widths (50%, 30%, ...)
KINDS = {"Shop ALL": "shops", "Buyer ALL": "buyers"}
# Fields scorecard.OBJECTIVE keeps only to read an older board (profit before Patch 3.3): never a match for a published
# part, so a patch that scores profit (or cash) again shows as a mismatch instead of passing through the alias.
LEGACY = {"profit"}


def text(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def parse(page):
    """{"patches": [{"id", "date", "title", "changes": [{"tag", "text"}]}], "scoring": {"shops": [[part, weight]], ...}}."""
    patches = []
    for art in re.findall(r'<article class="patch[^"]*">(.*?)</article>', page, re.S):
        head = dict(re.findall(r'<span class="(v|d|t)">(.*?)</span>', art.split("</header>")[0], re.S))
        changes = [{"tag": text(tag), "text": text(body)}
                   for tag, body in re.findall(r'<li><span class="tag">(.*?)</span>(.*?)</li>', art, re.S)]
        if head.get("v"):
            patches.append({"id": text(head["v"]), "date": text(head.get("d", "")), "title": text(head.get("t", "")),
                            "changes": changes})
    scoring = {}
    for name, parts in re.findall(r'<div class="bar"><span>(.*?)</span><div class="parts">(.*?)</div></div>', page, re.S):
        kind = KINDS.get(text(name))
        if not kind:
            continue
        got = []
        for width, label in re.findall(r'<span class="p\d+" style="width:(\d+(?:\.\d+)?)%">(.*?)</span>', parts, re.S):
            words = text(label).split()
            if words:
                got.append([words[0].lower(), round(float(width) / 100, 3)])
        scoring[kind] = got
    return {"patches": patches, "scoring": scoring}


def compare(scoring, objective):
    """Where the published weights and scorecard.OBJECTIVE disagree: [str], empty when they agree."""
    out = []
    for kind, parts in objective.items():
        published = scoring.get(kind)
        if not published:
            out.append(f"{kind}: no published weights found")
            continue
        mine = [(names, tuple(n for n in names if n not in LEGACY), w) for names, w in parts]   # (shown, matchable, w)
        for part, w in published:       # by name, in any order; bar widths are drawn, so 0.005 is the same weight
            match = next((mw for _, matchable, mw in mine if part in matchable), None)
            if match is None:
                out.append(f"{kind}: published {part} {w}, not in the scorecard")
            elif abs(w - match) > TOLERANCE:
                out.append(f"{kind}: published {part} {w}, scorecard {match}")
        for shown, matchable, mw in mine:
            if not any(part in matchable for part, _ in published):
                out.append(f"{kind}: scorecard {shown[0]} {mw}, not published")
    return out


def dated(p):
    """The patch's own date ("Nov 5, 2026") as local midnight, or None."""
    try:
        return time.mktime(time.strptime(p.get("date", ""), "%b %d, %Y"))
    except (ValueError, OverflowError):
        return None


def check(root=".", page=None, now=None, objective=None, record=True):
    """What this cycle should know about the rules. Never raises. record=False reads without marking anything seen (a
    look by hand must not take a patch out of the next cycle's "new")."""
    now = time.time() if now is None else now
    try:
        if page is None:
            page = httpx.get(RULES, timeout=20).text
        got = parse(page)
        if not got["patches"] or not got["scoring"]:
            return {"error": "the rules page had no patch list or scoring bars (layout changed?)", "url": RULES}
        if objective is None:
            import scorecard
            objective = scorecard.OBJECTIVE
        path = os.path.join(root, STATE)
        try:
            with open(path, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        seen, last = st.get("first_seen") or {}, st.get("checked_at") or 0
        for p in got["patches"]:        # the first check dates the patches already listed by their own date, not now
            seen.setdefault(p["id"], min(now, dated(p) or now) if not last else now)
        mismatch = compare(got["scoring"], objective)
        if record:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"first_seen": seen, "checked_at": now, "scoring": got["scoring"]}, f, ensure_ascii=False)
            os.replace(tmp, path)
        first = lambda p: seen[p["id"]]
        return {"url": RULES, "latest": got["patches"][0]["id"],
                "new": [p for p in got["patches"] if last and first(p) > last],
                "recent": [p for p in got["patches"] if now - first(p) < RECENT_HOURS * 3600],
                "first_check": not last, "scoring": got["scoring"],
                "objective_matches": not mismatch, "mismatch": mismatch}
    except Exception as e:  # noqa: BLE001 - the rules page never stops a cycle
        return {"error": f"{type(e).__name__}: {e}", "url": RULES}


if __name__ == "__main__":
    print(json.dumps(check(os.path.dirname(os.path.abspath(__file__)), record=False), ensure_ascii=False, indent=1))
