"""F: the grade a buyer agent rates well, shown first to that agent (the shop's per-account star lever).

stars_rank is one mean per reviewing account (server.py star_voices), so an account with few deals weighs as much as
a big one, and many buyer agents rate mechanically by grade. Market-wide, 72 h to 2026-11-12: kcwarni food C 3.0 and
everything else 5.0 (we sold it 157 food C), dyishappy A 3.2-3.4 / B 2.6-2.7, DY-76 C 4.0-4.3 / A 3.1. We sat at
4.36 under a cluster of 25 shops at 4.37.

The shop never learns who is asking, only the shape of the opening message (shop_r3.buyer_style). Every TASTE_EVERY_S
the CEO tick
  1. maps each style to the buyer agent that wrote it: our negotiation record (style, task) joined to the market's
     result for that task (buyer); a style is kept when one buyer has TASTE_STYLE_SHARE of its results;
  2. reads that buyer's stars by (kind, grade) over every shop's deals in TASTE_HOURS (settle grade x result stars);
  3. keeps a kind when its best and worst grade, each with TASTE_MIN_N reviews, differ by TASTE_MIN_SPREAD stars,
     and stores the grades best first in Memory meta `taste` ({style: {"account", "food"|"goods": [grades]}}).
The shop reads it with prefer() and find_unit serves the first preferred grade it holds (food: one that still lasts
the buyer's need). Without an entry nothing changes.
"""
import collections
import json
import time

from . import config


def _norm(url):
    return (url or "").rstrip("/")


def build(mem, now=None, hours=None):
    now = now or time.time()
    since = now - (hours or config.TASTE_HOURS) * 3600
    mine = {_norm(u) for u in mem.my_shop_urls()}
    with mem.db() as c:
        grade = {r["order_id"]: (r["kind"], r["grade"]) for r in c.execute(
            "SELECT order_id, kind, grade FROM settles WHERE ts>?", (since,))}
        style = {r["ref"]: r["style"] for r in c.execute(
            "SELECT ref, style FROM negotiations WHERE role='shop' AND style IS NOT NULL AND ts>?", (since - 3600,))}
        results = c.execute("SELECT data FROM ledger WHERE kind='result' AND ts>?", (since,)).fetchall()
    cells = collections.defaultdict(list)                  # (buyer, kind, grade) -> stars, every shop
    by_style = collections.defaultdict(collections.Counter)
    for r in results:
        d = json.loads(r["data"])
        stars, buyer = d.get("stars"), d.get("buyer")
        if not stars or not buyer:
            continue
        g = grade.get(d.get("order"))
        if g and g[0] and g[1]:
            cells[(buyer, g[0], g[1])].append(stars)
        if _norm(d.get("shop")) in mine:
            st = style.get((d.get("receipt") or {}).get("task"))
            if st:
                by_style[st][buyer] += 1
    out = {}
    for st, who in by_style.items():
        buyer, n = who.most_common(1)[0]
        if n < config.TASTE_STYLE_SHARE * sum(who.values()):
            continue                                       # a template several buyers share: no single taste
        entry = {"account": buyer}
        for kind in ("food", "goods"):
            means = {g: sum(v) / len(v) for g in "ABC"
                     if len(v := cells.get((buyer, kind, g), [])) >= config.TASTE_MIN_N}
            if len(means) >= 2 and max(means.values()) - min(means.values()) >= config.TASTE_MIN_SPREAD:
                ranked = sorted(means, key=lambda g: -means[g])
                entry[kind] = ranked + [g for g in "ABC" if g not in ranked]
                entry[kind + "_stars"] = {g: round(m, 2) for g, m in means.items()}
        if len(entry) > 1:
            out[st] = entry
    return out


def tick(mem, now=None):
    prefs = build(mem, now=now)
    mem.put("taste", prefs)
    return prefs


def prefer(mem, style, kind):
    """Grades best first for this buyer style and kind, or None (no clear taste, or memory trouble)."""
    try:
        return ((mem.get("taste") or {}).get(style) or {}).get(kind)
    except Exception:
        return None
