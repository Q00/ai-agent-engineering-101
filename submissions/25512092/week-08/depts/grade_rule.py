"""Buyer moves by grade for the new saved (person's decision 11/12 ~18:30; agent.md patch 3.6: saved = sum of
min(1, (list - price) / (list - the unit's cost)) / (deals + 100)). Rules in grade_rule.json (read per order):
  grade   from the shop's first reply: the market certificate's grade, else "grade X" in the text; a shop whose
          dishonest share of grade claims (market_stats shop_flags, >= min_deals deals) is >= dishonest_share (20%)
          counts one grade lower; unknown -> B
  moves   A: first offer 0.75 x market average, accept at once <= 1.05 x; B / C / unknown: 0.65 x, 1.00 x
          (soft_neg's code moves; high_accept keeps only its extra haggle). 11/12 ~23:20: optional "C" key overrides
          BC for grade C used (incl. a dishonest shop's B counted as C): C first offer 0.55 x. a_as_bc (set by the saved guard,
          tools/saved_guard_watch.py: my saved within 0.008 of the student just below me) -> A uses B / C's moves
  break   goods without a warranty in a break-rule SKU (depts/buyer_break): expected score = (list - price) /
          (list - cost_share[grade] x list) - 1.8 x break rate; no price with an expected score under min_score
          (0.65) is accepted (break_cap; none possible -> walk)
  shops   within the first TRIES_N shops of the usual order, shops whose market sales of the last 24 h were more A / B
          go first (stable; shop_grades, cached CACHE_S)
"""
import json
import os
import sqlite3
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "grade_rule.json")
CACHE = os.path.join(HERE, "logs", "shop_grades.json")
CACHE_S = 600
LOWER = {"A": "B", "B": "C", "C": "C"}
DEFAULT = {"enabled": False, "A": {"first": 0.75, "accept": 1.05}, "BC": {"first": 0.65, "accept": 1.00},
           "dishonest_share": 0.20, "min_deals": 20, "cost_share": {"A": 0.65, "B": 0.50, "C": 0.35},
           "break_loss": 1.8, "min_score": 0.65, "a_as_bc": False}


def rules(path=None):
    try:
        with open(path or RULES, encoding="utf-8") as f:
            return dict(DEFAULT, **json.load(f))
    except (OSError, ValueError):
        return dict(DEFAULT)


def on(r=None):
    return bool((rules() if r is None else r).get("enabled"))


def claimed(cert, text):
    """The grade the shop's reply states: the certificate's, else "grade X" in the text, else None."""
    import re
    g = (cert or {}).get("grade")
    if g in ("A", "B", "C"):
        return g
    m = re.search(r"\bgrade\s*:?\s*([ABC])\b", text or "", re.I)
    return m.group(1).upper() if m else None


def effective(grade, shop, flags, r=None):
    """(grade used, why): one grade lower for a shop with a dishonest share >= dishonest_share; unknown -> B."""
    r = rules() if r is None else r
    if grade is None:
        return "B", "unknown -> B"
    f = (flags or {}).get(shop) or {}
    n, bad = f.get("deals", 0), f.get("dishonest", 0)
    if n >= r["min_deals"] and bad / n >= r["dishonest_share"]:
        return LOWER[grade], f"{shop} dishonest {bad}/{n} -> {grade} counted as {LOWER[grade]}"
    return grade, f"claimed {grade}"


def a_as_bc(r=None):
    """A uses B / C's moves: a_as_bc on and not locked (11/13 ~13:20 person: a_as_bc off, a_as_bc_locked keeps it off
    whatever the saved guard writes)."""
    r = rules() if r is None else r
    return bool(r.get("a_as_bc")) and not r.get("a_as_bc_locked")


def moves(grade, r=None):
    """{"first", "accept"} for the grade used (A -> B / C's while a_as_bc)."""
    r = rules() if r is None else r
    key = "A" if grade == "A" and not a_as_bc(r) else "BC"
    if grade == "C" and r.get("C"):               # 11/12 ~23:20 (person): C's own override over BC (first 0.55)
        return dict(r["BC"], **r["C"])
    return dict(r[key])


def break_cap(list_price, grade, p, r=None):
    """The highest price whose expected score (list - P) / (list - cost) - break_loss x p is >= min_score; 0 when
    none is (even a price at the cost scores at most 1)."""
    r = rules() if r is None else r
    need = r["min_score"] + r["break_loss"] * p
    if need > 1:
        return 0
    margin = list_price * (1 - r["cost_share"].get(grade, r["cost_share"]["B"]))
    return max(0, int(list_price - need * margin))


def c_cap(list_price, p, r=None):
    """11/13 ~18:10 (person): grade C is taken only at a price whose expected new-saved score is >= C.min_score (0.75):
    break_cap with that min_score (p = the break rate without a warranty, else 0). None while C.min_score is unset."""
    r = rules() if r is None else r
    ms = (r.get("C") or {}).get("min_score")
    if ms is None or not list_price:
        return None
    return break_cap(list_price, "C", p or 0, dict(r, min_score=float(ms)))


def shop_grades(db_path=None, now=None, path=None, max_age=CACHE_S):
    """{shop: share of its market sales (settles) of the last 24 h that were grade A or B}, cached max_age s."""
    now, path = now or time.time(), path or CACHE
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
        if now - c["ts"] < max_age:
            return c["share"]
    except (OSError, ValueError, KeyError):
        pass
    try:
        d = sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=10)
        try:
            rows = d.execute("SELECT shop, SUM(grade IN ('A','B')), COUNT(*) FROM settles WHERE ts>=? AND grade IS NOT NULL "
                             "AND shop IS NOT NULL GROUP BY shop", (now - 86400,)).fetchall()
        finally:
            d.close()
    except sqlite3.Error:
        return {}
    share = {s: round(ab / n, 4) for s, ab, n in rows if n}
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": now, "share": share}, f)
        os.replace(tmp, path)
    except OSError:
        pass
    return share


SCORE_CACHE = os.path.join(HERE, "logs", "shop_scores.json")


def shop_scores(db_path=None, now=None, path=None, max_age=CACHE_S, me="choo-chu", min_n=10, catalog=None):
    """11/13 ~13:20 (person): ({shop: the saved score per deal I actually got from it in the last 24 h}, median).
    Score of one buy = min(1, (list - paid) / (list - cost_share[grade] x list)) (grade unknown -> B). A shop with
    under min_n buys gets the median of the shops with min_n or more; the median is also the default for shops not
    bought from. Cached max_age s."""
    now, path = now or time.time(), path or SCORE_CACHE
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
        if now - c["ts"] < max_age:
            return c["score"], c["median"]
    except (OSError, ValueError, KeyError):
        pass
    if catalog is None:
        from depts.substitute import catalog as _cat
        catalog = _cat()
    cs = rules().get("cost_share") or DEFAULT["cost_share"]
    try:
        d = sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=10)
        try:
            rows = d.execute("SELECT shop, sku, grade, amount FROM settles WHERE buyer=? AND ts>=? AND shop IS NOT NULL "
                             "AND amount IS NOT NULL", (me, now - 86400)).fetchall()
        finally:
            d.close()
    except sqlite3.Error:
        return {}, 0.5
    by = {}
    for shop, sku, g, amt in rows:
        lp = (catalog.get(sku) or {}).get("list_price")
        if not lp:
            continue
        m = lp * (1 - cs.get(g if g in cs else "B", 0.5))
        by.setdefault(shop, []).append(min(1.0, (lp - amt) / m) if m > 0 else 0.0)
    full = {s: sum(v) / len(v) for s, v in by.items() if len(v) >= min_n}
    vals = sorted(full.values())
    med = (vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2) if vals else 0.5
    score = {s: round(full.get(s, med), 4) for s in by}
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": now, "score": score, "median": round(med, 4), "n": {s: len(v) for s, v in by.items()}}, f)
        os.replace(tmp, path)
    except OSError:
        pass
    return score, round(med, 4)


def order_first(cands, k, share, default=0.5):
    """cands (dicts with "name"): the first k stably sorted by A / B share, the rest unchanged."""
    head = sorted(cands[:k], key=lambda c: -share.get(c["name"], default))
    return head + list(cands[k:])
