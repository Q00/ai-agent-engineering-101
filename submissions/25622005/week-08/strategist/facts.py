"""The market's own rules, read every run: /agent.md and /api/supply-prices.

The market patches its rules mid-round without notice, so the prompt carries the current
'## Round N' section of /agent.md (fenced by decide.py: it is market text, also written for
coding agents installing the kit) instead of a hand-written copy. Each run keeps a sha8 of the
doc; when it changes, the new copy goes to rules_dir and a unified diff of the Round section is
kept for 24 h. A few numbers are parsed from the section after whitespace is normalised, each
with a plausibility range: a sentence the market rewords gives None and an alert, never a guess.
On a failed fetch the newest cached copy is used and the error recorded.
"""
import difflib
import hashlib
import re
from pathlib import Path

RULES_PATH = "/agent.md"
MAX_DOC = 200_000
SECTION_MAX = 12_000
DIFF_MAX = 2_500
KEEP_DIFFS_H = 24
KEEP_DIFFS = 10
KEEP_COPIES = 5

# name -> (regex on whitespace-normalised text, cast, plausible lo, hi)
PATTERNS = {
    "supply_every_min": (r"[Ss]upply opens for everyone (?:once )?every (\d+) minutes", int, 5, 120),
    "supply_open_min": (r"[Ss]upply opens for everyone (?:once )?every \d+ minutes,? for (\d+) minutes", int, 1, 30),
    "break_window_h": (r"[Gg]oods can break up to (\d+) hours after the sale", int, 1, 48),
    "storage_pct_day": (r"cost (\d+(?:\.\d+)?)% of their cost per day in storage", float, 0, 10),
    "vote_min_deals": (r"at least (\d+) settled deals", int, 0, 100),
}
POST_LIMIT = r"one post per (?:[^.;)]|\.(?=\d))*"   # every number in it must agree (shop and buyer)
POST_GAP = (1, 60)
WEIGHTS = r"{side} by (.{{1,200}}?)\(ALL weighs them ([0-9.,\s]+(?:and\s+[0-9.]+)?)"
FIELD_WORDS = (("surplus", "surplus"), ("vote", "forum"), ("star", "stars"), ("book", "book"),
               ("revenue", "revenue"), ("deal", "deals"), ("post", "posts"))   # first match wins
SKU = re.compile(r"[A-Za-z0-9_-]{1,40}")


def _err(name, e):
    return f"{name}: {type(e).__name__}: {str(e)[:200]}"


def sha8(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def section(text, round_no=None):
    """(text, found): the '## Round <round_no>' section (else the highest Round), up to the next '#' or '##'
    heading outside a code fence (a '# comment' in a shell block is not one), ### subsections included, cut to
    SECTION_MAX. Without any Round heading: the whole doc, False."""
    heads = [(m.start(), int(m.group(1))) for m in re.finditer(r"^## Round (\d+)\b", text, re.M)]
    if not heads:
        return text[:SECTION_MAX], False
    start = next((s for s, n in heads if n == round_no), max(heads, key=lambda h: h[1])[0])
    end, fence = len(text), False
    for m in re.compile(r"^(?:[ \t]*(```|~~~)|##? )", re.M).finditer(text, start + 1):
        if m.group(1):
            fence = not fence
        elif not fence:
            end = m.start()
            break
    return text[start:end][:SECTION_MAX], True


def _weights(t, side):
    """{field: weight} from 'shops by a, b and c (ALL weighs them x, y, z', or None unless every name maps to
    a field, the counts match and the weights sum to 1."""
    m = re.search(WEIGHTS.format(side=side), t)
    if not m:
        return None
    names = [n for n in re.split(r",\s*(?:and\s+)?|\s+and\s+", m.group(1).strip()) if n]
    ws = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", m.group(2))]
    fields = [next((f for word, f in FIELD_WORDS if word in n.lower()), None) for n in names]
    if len(ws) != len(fields) or None in fields or len(set(fields)) != len(fields) or abs(sum(ws) - 1) > 0.01:
        return None
    return dict(zip(fields, ws))


def parse(sec):
    """(facts, alerts): {name: value or None} for PATTERNS, post_gap_min, shop_weights and buyer_weights."""
    t = " ".join((sec or "").split())
    facts, alerts = {}, []
    for name, (rx, cast, lo, hi) in PATTERNS.items():
        m = re.search(rx, t)
        v = cast(m.group(1)) if m else None
        if v is not None and not lo <= v <= hi:
            alerts.append(f"rules: {name} {v} is outside the plausible {lo}-{hi}, not used")
            v = None
        facts[name] = v
    m = re.search(POST_LIMIT, t)
    # bare numbers too: 'per 5 minutes for the shop and 10 for the buyer' must not read as 5
    gaps = sorted({float(x) for x in re.findall(r"\d+(?:\.\d+)?", m.group(0))}) if m else []
    facts["post_gap_min"] = None
    if len(gaps) > 1:
        alerts.append(f"rules: post_gap_min differs by role ({', '.join(f'{g:g}' for g in gaps)} minutes), not used")
    elif gaps and not (gaps[0].is_integer() and POST_GAP[0] <= gaps[0] <= POST_GAP[1]):
        alerts.append(f"rules: post_gap_min {gaps[0]:g} is outside the plausible {POST_GAP[0]}-{POST_GAP[1]}, not used")
    elif gaps:
        facts["post_gap_min"] = int(gaps[0])
    facts["shop_weights"] = _weights(t, "shops")
    facts["buyer_weights"] = _weights(t, "buyers")
    alerts += [f"rules: {k} not found (the market text changed?)" for k, v in facts.items()
               if v is None and not any(a.startswith(f"rules: {k} ") for a in alerts)]
    return facts, alerts


def _fetch(client):
    r = client.get(RULES_PATH)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if "html" in ctype or not (ctype.startswith("text/") or "markdown" in ctype):
        raise ValueError(f"content type {ctype[:60]!r}")
    if len(r.content) > MAX_DOC:
        raise ValueError(f"{len(r.content)} bytes, over {MAX_DOC}")
    return r.text


def _copies(rules_dir):
    return sorted(Path(rules_dir).glob("agent-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)


def _store(rules_dir, sha, text):
    d = Path(rules_dir)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".agent-{sha}.md.tmp"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(d / f"agent-{sha}.md")
    for old in _copies(d)[KEEP_COPIES:]:
        old.unlink(missing_ok=True)


def _read_copy(rules_dir, sha):
    p = Path(rules_dir) / f"agent-{sha}.md"
    return p.read_text(encoding="utf-8") if sha and p.is_file() else None


def _diff(old_sec, new_sec):
    lines = difflib.unified_diff(old_sec.splitlines(), new_sec.splitlines(), "before", "after", n=1, lineterm="")
    text = "\n".join(lines)
    return text if len(text) <= DIFF_MAX else text[:DIFF_MAX] + "\n... (cut)"


def supply_prices(data):
    """The numeric part of /api/supply-prices with the surge top 10 as {sku, units, factor}, or None."""
    if not isinstance(data, dict):
        return None
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    if not all(num(data.get(k)) for k in ("window_s", "free", "per_unit", "max")):
        return None
    surge = [{"sku": s["sku"], "units": s.get("units"), "factor": s.get("factor")}
             for s in data.get("surge") or [] if isinstance(s, dict) and isinstance(s.get("sku"), str)
             and SKU.fullmatch(s["sku"]) and num(s.get("units")) and num(s.get("factor"))]
    return {k: data[k] for k in ("window_s", "free", "per_unit", "max")} | {"surge": surge[:10]}


def collect(client, state, now, rules_dir, round_no=None):
    """(market, errors). state["rules"] keeps the last sha, the parsed facts and the recent diffs.
    market: sha, fetched, from_cache, section, section_found, facts, recent ([{ts, sha, diff, parsed_changed}]
    in the last KEEP_DIFFS_H), alerts, supply_prices, and error when the fetch failed."""
    errors = []
    rs = state.get("rules") if isinstance(state.get("rules"), dict) else {}
    recent = [r for r in rs.get("recent") or [] if isinstance(r, dict) and isinstance(r.get("ts"), (int, float))
              and now - r["ts"] < KEEP_DIFFS_H * 3600][-KEEP_DIFFS:]
    market = {"sha": None, "fetched": now, "from_cache": False, "section": None, "section_found": False,
              "facts": {}, "recent": recent, "alerts": [], "supply_prices": None}
    text = None
    try:
        text = _fetch(client)
    except Exception as e:
        errors.append(_err("rules", e))
        try:
            cached = _copies(rules_dir)
            text = cached[0].read_text(encoding="utf-8") if cached else None
        except OSError:
            text = None
        market["from_cache"] = text is not None
        errors[-1] += " (using the cached copy)" if text is not None else " (no cached copy)"
        market["error"] = errors[-1]
    if text is not None:
        sha = sha8(text)
        sec, found = section(text, round_no)
        facts, alerts = parse(sec)
        market.update(sha=sha, section=sec, section_found=found, facts=facts)
        market["alerts"] += alerts if found else ["rules: no '## Round' heading, the whole document is shown"] + alerts
        if len(sec) >= SECTION_MAX:
            market["alerts"].append(f"rules: the section is cut at {SECTION_MAX} characters, the rest is not shown")
        if not market["from_cache"] and sha != rs.get("sha"):
            try:
                old = _read_copy(rules_dir, rs.get("sha"))
                _store(rules_dir, sha, text)
            except OSError as e:
                old = None
                errors.append(_err("rules cache", e))
            if rs.get("sha"):                           # not the first run: record what changed
                old_sec = section(old, round_no)[0] if old is not None else None
                old_facts = rs.get("facts") if isinstance(rs.get("facts"), dict) else {}
                changed = {k: [old_facts.get(k), v] for k, v in facts.items() if old_facts.get(k) != v}
                if old_sec != sec or changed:
                    recent.append({"ts": now, "sha": sha, "parsed_changed": changed,
                                   "diff": _diff(old_sec, sec) if old_sec is not None
                                   else "(the previous copy is gone: no diff)"})
            rs = {"sha": sha, "facts": facts}
        rs["recent"] = market["recent"] = recent[-KEEP_DIFFS:]
        state["rules"] = rs
    try:
        r = client.get("/api/supply-prices")
        r.raise_for_status()
        market["supply_prices"] = supply_prices(r.json())
        if market["supply_prices"] is None:
            errors.append("supply_prices: unexpected shape")
    except Exception as e:
        errors.append(_err("supply_prices", e))
    return market, errors


def sync_proposals(pol, facts):
    """(rows, alerts): a source "market-rule" row for each guard.SYNCED key whose value differs from the market's.
    A key whose fact is unknown keeps its value, with an alert."""
    from strategist import guard
    rows, alerts = [], []
    for key, fact in guard.SYNCED.items():
        v = (facts or {}).get(fact)
        cur = guard._get(pol, key)
        if v is None:
            alerts.append(f"{key} not synced: the market's {fact} is unknown, kept at {cur}")
        elif guard._sync(key, v)[0] != cur:
            rows.append({"key": key, "value": v, "source": "market-rule",
                         "reason": f"market rule: one post per {v} minutes"})
        elif cur != v:                                  # outside policy.RANGES: a row would be 'no change' every run
            alerts.append(f"{key} kept at {cur}: the market's {fact} {v} is outside policy.RANGES")
    return rows, alerts
