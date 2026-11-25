"""The agent's memory of the market's rules page, and the alarm when the page changes.

    refresh(state_dir)  fetch /agent.md at most every CHECK_EVERY seconds, compare with the remembered copy
    load(state_dir)     {"formula": ..., "hash": ..., "changed_at": ..., "diff": [...]}   no network: the buyer's path

Why: on 11-12 17:41 the market changed how a purchase is scored (patch 3.6). Nothing in this agent read the rules
after the day it was written, so the buyer, the tuner's prompt and the report all kept the 11-06 formula until a
person looked on 11-17. The rules page is part of the environment the agent acts in; it is remembered like a shop is.

What is kept in .market/: rulebook.md (the page as last seen), rulebook.prev.md (the page before the last change),
rulebook.json (hash, when checked, when changed, the changed lines, which scoring formula the text names). The
restock hook calls refresh() (a restock window opens every 20 minutes); the nightly reflection is shown the changed
lines before anything else. A page whose scoring sentence matches no known formula keeps the last known one in use
and says so in every buyer log line until a person adds the formula to objective.py.
"""
import difflib
import hashlib
import json
import os
import re
import time
from pathlib import Path

import httpx

import objective

MARKET = os.environ.get("MARKET_URL", "https://market.ourolabs.ai").rstrip("/")
FILE, PAGE, PREVIOUS = "rulebook.json", "rulebook.md", "rulebook.prev.md"
CHECK_EVERY = 1800
MAX_DIFF_LINES = 30
# the sentence of agent.md that defines the buyer's score, per formula objective.py knows
SIGNATURES = (
    ("margin_share", re.compile(r"\(list price - price\)\s*/\s*\(list price - unit cost\)", re.I)),
    ("list_discount", re.compile(r"surplus\s*/\s*list price", re.I)),
)


def detect(text):
    """Which scoring formula the page names, or "unknown"."""
    flat = " ".join((text or "").split())
    for name, pattern in SIGNATURES:
        if pattern.search(flat):
            return name
    return "unknown"


def changed_lines(old, new, limit=MAX_DIFF_LINES):
    """The lines that differ, as '- old' / '+ new', without the unchanged context."""
    out = [line for line in difflib.unified_diff((old or "").splitlines(), (new or "").splitlines(), lineterm="", n=0)
           if line[:1] in "+-" and not line.startswith(("+++", "---"))]
    return out[:limit]


def load(state_dir=".market"):
    try:
        book = json.loads((Path(state_dir) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        book = {}
    if book.get("formula") not in objective.FORMULAS:
        book["formula"] = book.get("last_known_formula") if book.get("last_known_formula") in objective.FORMULAS \
            else objective.DEFAULT_FORMULA
    return book


def fetch(timeout=15):
    r = httpx.get(MARKET + "/agent.md", timeout=timeout)
    r.raise_for_status()
    return r.text


def refresh(state_dir=".market", now=None, fetch=fetch, force=False):
    """Re-read the rules page when the last check is older than CHECK_EVERY. Returns (book, changed)."""
    state_dir, now = Path(state_dir), int(now or time.time())
    book = load(state_dir)
    if not force and book.get("checked_at") and now - int(book["checked_at"]) < CHECK_EVERY:
        return book, False
    try:
        text = fetch()
    except Exception as e:   # the market is unreachable: the remembered rules stay in force
        print(f"[rulebook] rules page unavailable: {type(e).__name__}: {str(e)[:80]}", flush=True)
        return book, False
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    changed = bool(book.get("hash")) and digest != book["hash"]
    named = detect(text)
    page = state_dir / PAGE
    if changed:
        try:
            old = page.read_text(encoding="utf-8")
        except OSError:
            old = ""
        (state_dir / PREVIOUS).write_text(old, encoding="utf-8")
        book.update({"changed_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)), "previous_hash": book["hash"],
                     "previous_formula": book.get("formula"), "diff": changed_lines(old, text)})
    if changed or not book.get("hash"):
        state_dir.mkdir(exist_ok=True)
        page.write_text(text, encoding="utf-8")
        book.setdefault("first_seen", time.strftime("%Y-%m-%d %H:%M", time.localtime(now)))
    if named in objective.FORMULAS:
        book["last_known_formula"] = named
    book.update({"hash": digest, "checked_at": now, "named_formula": named,
                 "formula": named if named in objective.FORMULAS else book["formula"]})
    tmp = state_dir / (FILE + ".tmp")
    tmp.write_text(json.dumps(book, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, state_dir / FILE)
    event = {"event": "rulebook", "hash": digest, "formula": book["formula"], "named_formula": named, "changed": changed}
    if changed:
        event["diff"] = book["diff"][:6]
    print("[strategy] " + json.dumps(event, ensure_ascii=False, sort_keys=True), flush=True)
    return book, changed
