"""The market's own rules, read every run: /agent.md, the /rules patch notes and /api/supply-prices.

The market patches its rules mid-round without notice, so the prompt carries the current
'## Round N' section of /agent.md (fenced by decide.py: it is market text, also written for
coding agents installing the kit) instead of a hand-written copy. Each run keeps a sha8 of the
doc; when it changes, the new copy goes to rules_dir and a unified diff of the Round section is
kept for 24 h. A few numbers are parsed from the section after whitespace is normalised, each
with a plausibility range: a sentence the market rewords gives None and an alert, never a guess.
On a failed fetch the newest cached copy is used and the error recorded.

The /rules page (HTML) lists the patch notes, newest first, one <article class="patch..."> each: version,
date, title and the changes as text. state["rules"]["patches"] keeps {version: sha8}; the first sight only
records them, so the patches already out do not wake the model. A new or changed patch afterwards is one
`recent` entry (source "rules"), like an agent.md diff: run.py asks the model on any new entry and decide.py
shows it fenced. A page without any patch article is hashed as text instead; a change is one "rules-page"
entry, at most one per PAGE_WAKE_GAP_H (a page whose text moves every run must not wake the model every run). The page copy is kept in rules_dir (5 copies) for diffs and as the fallback on a failed fetch.
"""
import difflib
import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path

RULES_PATH = "/agent.md"
MAX_DOC = 200_000
SECTION_MAX = 16_000          # the Round 3 section passed 12,000 on 2026-11-07 (Patch 3.5 vote rules)
DIFF_MAX = 2_500
KEEP_DIFFS_H = 24
KEEP_DIFFS = 10
KEEP_COPIES = 5
PAGE_PATH = "/rules"
PATCH_TEXT_MAX = 1500
PAGE_WAKE_GAP_H = 6         # a page without patch articles wakes the model at most once in this many hours

# name -> (regex on whitespace-normalised text, cast, plausible lo, hi)
PATTERNS = {
    "supply_every_min": (r"[Ss]upply opens for everyone (?:once )?every (\d+) minutes", int, 5, 120),
    "supply_open_min": (r"[Ss]upply opens for everyone (?:once )?every \d+ minutes,? for (\d+) minutes", int, 1, 30),
    "break_window_h": (r"[Gg]oods can break up to (\d+) hours after the sale", int, 1, 48),
    "storage_pct_day": (r"cost (\d+(?:\.\d+)?)% of their cost per day in storage", float, 0, 100),   # a patch may raise it far past the 1% of 11-05
    "vote_min_deals": (r"at least (\d+) settled deals", int, 0, 100),
}
# name -> regex on the normalised text; the fact is True when the sentence is there, None (and an alert) when not:
# the capital posture depends on them (decide.MECHANICS), so a reworded rule must wake the model, not pass as before
PRESENCE = {
    "book_counts_stock_at_cost": r"[Bb]ook = cash \+ what the unsold units cost",
    "stock_pays_storage": r"[Gg]oods in stock (?:still )?(?:pay|cost) .{0,40}storage",
    # Patch 3.4: strategist/objective.py (score_twin) runs only while this is the Saved rule
    "saved_is_kept_share_over_deals_plus_100":
        r"Saved, the part that scores, is the sum over deals of surplus / list price.{0,120}divided by deals \+ 100",
}
POST_LIMIT = r"one post per (?:[^.;)]|\.(?=\d))*"   # every number in it must agree (shop and buyer)
POST_GAP = (1, 60)
WEIGHTS = r"{side} by (.{{1,200}}?)\(ALL weighs them ([0-9.,\s]+(?:and\s+[0-9.]+)?)"
FIELD_WORDS = (("surplus", "surplus"), ("saved", "saved"), ("vote", "forum"), ("forum", "forum"), ("star", "stars"),
               ("book", "book"), ("revenue", "revenue"), ("deal", "deals"), ("post", "posts"))   # first match wins
# Patch 3.4 renamed the buyer's surplus part to "saved rate" and words the forum part as "forum stances on its posts",
# so "saved" and "forum" come before "post"
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
    """(facts, alerts): {name: value or None} for PATTERNS, PRESENCE (True when the sentence is there),
    post_gap_min, shop_weights and buyer_weights."""
    t = " ".join((sec or "").split())
    facts, alerts = {}, []
    for name, (rx, cast, lo, hi) in PATTERNS.items():
        m = re.search(rx, t)
        v = cast(m.group(1)) if m else None
        if v is not None and not lo <= v <= hi:
            alerts.append(f"rules: {name} {v} is outside the plausible {lo}-{hi}, not used")
            v = None
        facts[name] = v
    for name, rx in PRESENCE.items():
        facts[name] = True if re.search(rx, t) else None
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


class _Page(HTMLParser):
    """The patch articles of /rules as {v, date, title, text} and the page's visible text (script and style
    left out). A change reads '- [Tag] text Do: ...'; the header spans .v/.d/.t give version, date and title."""
    BLOCKS = {"p", "div", "li", "h1", "h2", "h3", "h4", "header", "section", "article", "tr", "br", "ul"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.patches, self.text, self._cur, self._depth, self._spans, self._skip = [], [], None, 0, [], 0

    def _out(self, s):
        self.text.append(s)
        if self._cur is not None and not any(r in ("v", "d", "t") for r in self._spans):
            self._cur["parts"].append(s)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
            return
        cls = (dict(attrs).get("class") or "").split()
        if tag == "article" and (self._cur is not None or "patch" in cls):
            if self._cur is None:
                self._cur, self._depth = {"v": "", "d": "", "t": "", "parts": []}, 0
            self._depth += 1
        if tag == "span":
            role = next((c for c in cls if c in ("v", "d", "t", "tag", "do")), None) if self._cur is not None else None
            self._spans.append(role)
            self._out({"tag": "[", "do": " Do: "}.get(role, ""))
        elif tag == "li":
            self._out("\n- ")
        elif tag in self.BLOCKS:
            self._out("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            return
        if tag == "span":
            role = self._spans.pop() if self._spans else None
            self._out("] " if role == "tag" else " " if role in ("v", "d", "t") else "")
        elif tag in self.BLOCKS:
            self._out("\n")
        if tag == "article" and self._cur is not None:
            self._depth -= 1
            if self._depth <= 0:
                c, self._cur, self._spans = self._cur, None, []
                self.patches.append({"v": _lines(c["v"]), "date": _lines(c["d"]), "title": _lines(c["t"]),
                                     "text": _lines("".join(c["parts"]))})

    def handle_data(self, data):
        if self._skip:
            return
        role = next((r for r in reversed(self._spans) if r in ("v", "d", "t")), None)
        if self._cur is not None and role:
            self._cur[role] += data
        self._out(data) if not role else self.text.append(data)


def _lines(s):
    """Whitespace inside each line collapsed, empty lines dropped."""
    return "\n".join(x for x in (" ".join(line.split()) for line in s.splitlines()) if x)


def parse_page(html):
    """(patches, page_text): patches [{version, date, title, text, full}] newest first as the page lists them
    (version "3.3" from "Patch 3.3", else the header text, else "#<n>"; text cut to PATCH_TEXT_MAX, full uncut for
    the hash and diffs, so a change past the cut still counts); page_text the visible text of the whole page."""
    p = _Page()
    p.feed(html)
    p.close()
    out = []
    for i, a in enumerate(p.patches):
        m = re.search(r"\d+(?:\.\d+)+", a["v"])
        version = (m.group(0) if m else a["v"][:40]) or f"#{i + 1}"
        out.append({"version": version, "date": a["date"][:40], "title": a["title"][:120],
                    "text": a["text"][:PATCH_TEXT_MAX], "full": a["text"]})
    return out, _lines("".join(p.text))


def _patch_sha(p):
    return sha8("\n".join((p["date"], p["title"], p["full"])))


def _fetch_page(client):
    r = client.get(PAGE_PATH)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if not ctype.startswith("text/"):
        raise ValueError(f"content type {ctype[:60]!r}")
    if len(r.content) > MAX_DOC:
        raise ValueError(f"{len(r.content)} bytes, over {MAX_DOC}")
    return r.text


def _page_copies(rules_dir):
    return sorted(Path(rules_dir).glob("rules-*.html"), key=lambda p: p.stat().st_mtime, reverse=True)


def _store_page(rules_dir, sha, html):
    d = Path(rules_dir)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".rules-{sha}.html.tmp"
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(d / f"rules-{sha}.html")
    for old in _page_copies(d)[KEEP_COPIES:]:
        old.unlink(missing_ok=True)


def patch_notes(client, rs, now, rules_dir):
    """(patches, entries, from_cache, errors) for /rules. rs is state["rules"] (changed in place: "patches"
    {version: sha8}, "page_sha" while the page has no patch article, "page" the sha of the stored copy).
    entries: the new `recent` rows, none on the first sight or from the cache."""
    errors, from_cache = [], False
    try:
        html = _fetch_page(client)
    except Exception as e:
        errors.append(_err("patch_notes", e))
        try:
            cached = _page_copies(rules_dir)
            html = cached[0].read_text(encoding="utf-8") if cached else None
        except OSError:
            html = None
        from_cache = html is not None
        errors[-1] += " (using the cached copy)" if from_cache else " (no cached copy)"
    if html is None:
        return [], [], False, errors
    try:
        patches, text = parse_page(html)
    except Exception as e:                              # a page html.parser cannot read
        errors.append(_err("patch_notes", e))
        return [], [], from_cache, errors
    if from_cache:
        return patches, [], True, errors
    seen = "patches" in rs or "page_sha" in rs
    old_page = rs.get("page")
    page_sha = sha8(html)
    old_html = None
    if page_sha != old_page:
        try:
            p = Path(rules_dir) / f"rules-{old_page}.html"
            old_html = p.read_text(encoding="utf-8") if old_page and p.is_file() else None
            _store_page(rules_dir, page_sha, html)
            rs["page"] = page_sha
        except OSError as e:
            errors.append(_err("patch_notes cache", e))
    old_patches, old_text = parse_page(old_html) if old_html is not None else ([], None)
    old_by_v = {p["version"]: p for p in old_patches}
    entries = []
    if patches:
        known = rs.get("patches") if isinstance(rs.get("patches"), dict) else {}
        new = {p["version"]: _patch_sha(p) for p in patches}
        for p in reversed(patches):                     # oldest first, like the rest of `recent`
            v, sha = p["version"], new[p["version"]]
            if not seen or known.get(v) == sha:
                continue
            head = f"Patch {v} ({p['date']}): {p['title']}"
            was = old_by_v.get(v)
            body = (_diff(was["full"], p["full"]) if v in known and was is not None else p["text"])
            entries.append({"ts": now, "sha": sha, "source": "rules", "version": v, "title": p["title"],
                            "parsed_changed": {}, "diff": f"{head}\n{body}"[:DIFF_MAX + 200]})
        # versions gone from the page stay known (a re-listed one is not new), the oldest of them dropped past 40
        rs["patches"] = {**dict([kv for kv in known.items() if kv[0] not in new][-40:]), **new}
        rs.pop("page_sha", None)
    else:
        sha = sha8(text)
        last = max((r["ts"] for r in rs.get("recent") or [] if isinstance(r, dict) and r.get("source") == "rules-page"
                    and isinstance(r.get("ts"), (int, float))), default=None)
        if seen and rs.get("page_sha") != sha and last is not None and now - last < PAGE_WAKE_GAP_H * 3600:
            return patches, entries, False, errors      # page_sha kept: the change shows after the gap
        if seen and rs.get("page_sha") != sha:
            body = _diff(old_text, text) if old_text is not None else text[:DIFF_MAX]
            entries.append({"ts": now, "sha": sha, "source": "rules-page", "version": None,
                            "title": "the /rules page has no patch article (layout changed?)",
                            "parsed_changed": {}, "diff": body})
        rs["page_sha"] = sha
    return patches, entries, False, errors


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
    """(market, errors). state["rules"] keeps the last sha, the parsed facts, the recent diffs and the patch
    notes' hashes (patch_notes). market: sha, fetched, from_cache, section, section_found, facts, recent
    ([{ts, sha, diff, parsed_changed}, plus source/version/title for a patch note] in the last KEEP_DIFFS_H),
    alerts, patches ([{version, date, title}] from /rules, newest first), patches_from_cache, supply_prices,
    surge_units ({sku: units} of every SKU on the surge list), and error when the agent.md fetch failed."""
    errors = []
    rs = dict(state["rules"]) if isinstance(state.get("rules"), dict) else {}
    recent = [r for r in rs.get("recent") or [] if isinstance(r, dict) and isinstance(r.get("ts"), (int, float))
              and now - r["ts"] < KEEP_DIFFS_H * 3600][-KEEP_DIFFS:]
    market = {"sha": None, "fetched": now, "from_cache": False, "section": None, "section_found": False,
              "facts": {}, "recent": recent, "alerts": [], "patches": [], "patches_from_cache": False,
              "supply_prices": None, "surge_units": {}}
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
            rs.update(sha=sha, facts=facts)
    try:
        patches, entries, market["patches_from_cache"], errs = patch_notes(client, rs, now, rules_dir)
    except Exception as e:                              # the patch notes are one input among many
        patches, entries, errs = [], [], [_err("patch_notes", e)]
    errors += errs
    market["patches"] = [{k: p[k] for k in ("version", "date", "title")} for p in patches]
    recent += entries
    if rs:
        rs["recent"] = market["recent"] = recent[-KEEP_DIFFS:]
        state["rules"] = rs
    try:
        r = client.get("/api/supply-prices")
        r.raise_for_status()
        data = r.json()
        market["supply_prices"] = supply_prices(data)
        if market["supply_prices"] is None:
            errors.append("supply_prices: unexpected shape")
        else:
            market["surge_units"] = {s["sku"]: s["units"] for s in data.get("surge") or [] if isinstance(s, dict)
                                     and isinstance(s.get("sku"), str) and SKU.fullmatch(s["sku"])
                                     and isinstance(s.get("units"), (int, float)) and not isinstance(s["units"], bool)}
    except Exception as e:
        errors.append(_err("supply_prices", e))
    return market, errors


SURGE_FACTS = {"surge_free": ("free", "units of a SKU the market buys per rolling hour at the plain price"),
               "surge_per_unit": ("per_unit", "added to the cost factor per further unit"),
               "surge_max": ("max", "the cost factor's cap")}


def sync_proposals(pol, facts, supply_prices=None):
    """(rows, alerts): a source "market-rule" row for each guard.SYNCED key whose value differs from the market's.
    The surge_* facts come from supply_prices (the dict supply_prices() returns). A key whose fact is unknown
    keeps its value (the last synced one), with an alert."""
    from strategist import guard
    rows, alerts = [], []
    facts = dict(facts or {})
    for fact, (field, _) in SURGE_FACTS.items():
        facts[fact] = supply_prices.get(field) if isinstance(supply_prices, dict) else None
    for key, fact in guard.SYNCED.items():
        v = facts.get(fact)
        cur = guard.value(pol, key)
        if v is None:
            alerts.append(f"{key} not synced: the market's {fact} is unknown, kept at {cur}")
            continue
        new, clamped = guard._sync(key, v)
        if new != cur:
            why = (f"market rule: one post per {v} minutes" if fact == "post_gap_min"
                   else f"market rule (/api/supply-prices {SURGE_FACTS[fact][0]}): {v}, {SURGE_FACTS[fact][1]}")
            rows.append({"key": key, "value": v, "source": "market-rule", "reason": why})
        elif clamped:                                   # outside policy.RANGES: a row would be 'no change' every run
            alerts.append(f"{key} kept at {cur}: the market's {fact} {v} is outside policy.RANGES")
    return rows, alerts
