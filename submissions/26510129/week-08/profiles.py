"""Semantic memory: what the remembered orders add up to per shop, and the compaction that keeps the order log small.

Three layers, each derived from the one before it:

    episodic   .market/buyer_memory.json           one row per shop visit, the last HOT_HOURS only (memory_sync.py)
               .market/buyer_memory.archive.jsonl  older rows, append-only, never read on an order
    semantic   .market/profiles.json               per shop: how often it deals, what a deal there scores under the
                                                   rule in force, at what share of its ask it accepts, whether its
                                                   goods carry a warranty, whether it ever steered or cheated;
                                                   and my own running score (the board's Saved, estimated)
    procedural .market/lessons.jsonl + strategy_params.json   (tune.py)

consolidate() runs at the start of every order: rows older than HOT_HOURS are folded into lifetime counters and
moved to the archive, then the per-shop view is rebuilt from the counters and the recent rows.

Why (measured 11-17 19:30, 103,368 rows, 57 MB): every order re-read the whole log and re-derived each fact from
it - load 0.39 s, cooling-off 0.41 s, ranking 0.79 s (again on every retry), save 0.21 s per attempt: about 2.5 s
of a median 5.5 s order, growing by 4,700 rows a day. Two of the round's worst bugs were facts derived ad hoc and
never written down where anyone could read them: a blacklist that held the market's ten biggest shops for 1.5 days
(11-05) and a cooling-off list that held 48 shops for good (11-11). A profile is a file a person can open.

The lifetime counters hold scores under one formula; when rulebook.py reports another, they are rebuilt from the
archive (the order log is the source, the profile is a view of it).
"""
import json
import os
import time
from pathlib import Path

import objective

VERSION = 1
FILE = "profiles.json"
ARCHIVE = "buyer_memory.archive.jsonl"
HOT_HOURS = 48          # goods can break 6 h after a sale and a payment is refused within seconds: after 48 h a row is final
MIN_SCORED = 5          # settled deals before a shop's mean score is believed
MIN_ACCEPTED = 8        # accepted offers before a shop's acceptance threshold is believed
FIXED_FROM = 0.97       # a threshold this close to the ask: the shop does not bargain
QUIET = ("no_stock", "stale", "unreachable")   # a visit that ended before any offer says nothing about how the shop trades
COUNTERS = ("visits", "offers", "deals", "steer", "bad", "clean_deals", "unpaid", "w_n", "w_yes", "scored", "kept_sum")


def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _blank():
    return {name: 0 for name in COUNTERS}


def fold(shops, row, formula):
    """Add one order row to the lifetime counters of its shop. The rules mirror buyer_policy's (blacklist, warranty)."""
    name = row.get("shop")
    if not name:
        return
    c = shops.setdefault(name, _blank())
    c["visits"] += 1
    if row.get("outcome") not in QUIET:
        c["offers"] += 1
    if row.get("outcome") == "deal":
        c["deals"] += 1
    if row.get("outcome") == "unpaid":
        c["unpaid"] += 1
    if row.get("manipulative") is True:
        c["steer"] += 1
    if row.get("honest") is False or (row.get("broken") and not row.get("refunded")):
        c["bad"] += 1
    elif row.get("outcome") == "deal":
        c["clean_deals"] += 1
    if (row.get("settled") and row.get("warranty_hours") is not None and row.get("grade") in objective.COST_SHARE
            and not row.get("value") == 0 and (row.get("list_price") or 0) >= 50_000):
        c["w_n"] += 1
        c["w_yes"] += (row.get("warranty_hours") or 0) > 0
    s = objective.outcome_score(row, formula)
    if s is not None:
        c["scored"] += 1
        c["kept_sum"] = round(c["kept_sum"] + s, 4)
    c["last_at"] = max(c.get("last_at") or "", row.get("at") or "")


def observations(row):
    """What one visit shows about where the shop accepts, as (offer / its first ask, accepted) pairs: my first offer,
    and the price it agreed to or my last offer it did not."""
    ask, first, lp = row.get("ask"), row.get("first_offer"), row.get("list_price")
    if not _number(ask) or ask <= 0 or not _number(first) or first <= 0:
        return []
    if _number(lp) and lp > 0 and not 0.2 * lp <= ask <= 3 * lp:
        return []   # an ask parsed out of other text
    agreed = row.get("price") if row.get("outcome") in ("deal", "unpaid") and _number(row.get("price")) else None
    if agreed is not None and agreed == first:
        pairs = [(first / ask, True)]
    else:
        pairs = [(first / ask, False)]
        if agreed is not None:
            pairs.append((agreed / ask, True))
        elif _number(row.get("last_offer")) and row["last_offer"] != first:
            pairs.append((row["last_offer"] / ask, False))
    return [(r, ok) for r, ok in pairs if 0.3 <= r <= 1.05]


def threshold(accepted):
    """The share of its ask at which the shop usually starts to accept: the 20th percentile of the accepted offers
    (one old unit let go cheaply does not move it). None under MIN_ACCEPTED observations.
    11-17, 22,883 visits: YAHO1212, nocha12, bjeon01, 25620027 accept at 1.00 of the ask and nothing lower;
    psyoung82 at 0.83, ddolcom at 0.70, outseop at 0.90, buzllightyear at 0.93, Kiro729 at 0.92-0.96."""
    if len(accepted) < MIN_ACCEPTED:
        return None
    ordered = sorted(accepted)
    return round(min(1.0, ordered[int(len(ordered) * 0.2)]), 3)


def view(life, rows, formula):
    """The per-shop facts decisions read: lifetime counters plus the recent rows. Returns (shops, summary)."""
    recent, accepted, grades = {}, {}, {}
    for row in rows:
        fold(recent, row, formula)
        for ratio, ok in observations(row):
            if ok:
                accepted.setdefault(row.get("shop"), []).append(ratio)
        s = objective.outcome_score(row, formula)
        if s is not None:
            g = grades.setdefault(row.get("grade") or "?", {"n": 0, "score": 0.0, "paid": 0.0})
            g["n"] += 1
            g["score"] += s
            g["paid"] += row["price"] / row["list_price"]
    shops = {}
    for name in set(life) | set(recent):
        old, new = life.get(name) or _blank(), recent.get(name) or _blank()
        total = {k: old.get(k, 0) + new.get(k, 0) for k in COUNTERS}
        kept = (new["kept_sum"] / new["scored"] if new["scored"] >= MIN_SCORED
                else total["kept_sum"] / total["scored"] if total["scored"] >= MIN_SCORED else None)
        p_deal = (new["deals"] / new["offers"] if new["offers"] >= MIN_SCORED
                  else total["deals"] / total["offers"] if total["offers"] >= MIN_SCORED else None)
        theta = threshold(accepted.get(name, []))
        shops[name] = {
            "attempts": total["offers"], "deals": total["deals"], "recent_offers": new["offers"],
            "p_deal": round(p_deal, 3) if p_deal is not None else None,
            "kept": round(kept, 3) if kept is not None else None,
            "theta": theta, "fixed": theta is not None and theta >= FIXED_FROM,
            "warranty": round(total["w_yes"] / total["w_n"], 3) if total["w_n"] >= 3 else None,
            "steer": total["steer"], "bad": total["bad"], "clean_deals": total["clean_deals"], "unpaid": total["unpaid"],
            "last_at": max(old.get("last_at") or "", new.get("last_at") or ""),
        }
    scored = sum(c["scored"] for c in life.values()) + sum(c["scored"] for c in recent.values())
    kept_sum = sum(c["kept_sum"] for c in life.values()) + sum(c["kept_sum"] for c in recent.values())
    summary = {
        "deals_scored": scored,
        "saved": round(kept_sum / (scored + 100), 4) if scored else None,   # the board's Saved: sum / (deals + 100)
        "recent_by_grade": {g: {"n": v["n"], "score": round(v["score"] / v["n"], 3), "paid": round(v["paid"] / v["n"], 3)}
                            for g, v in sorted(grades.items())},
    }
    return shops, summary


def load(state_dir=".market"):
    try:
        prof = json.loads((Path(state_dir) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prof = {}
    return prof if prof.get("version") == VERSION else {}


def archived_rows(state_dir=".market"):
    """Every archived row, oldest first (offline analysis and rebuilds; never called on an order)."""
    try:
        with open(Path(state_dir) / ARCHIVE, encoding="utf-8") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def rebuild_life(state_dir, formula):
    """Recount the lifetime counters from the archive, under `formula`. Returns (shops, rows, the latest row's time)."""
    shops, n, latest = {}, 0, ""
    for row in archived_rows(state_dir):
        fold(shops, row, formula)
        n += 1
        latest = max(latest, row.get("at") or "")
    return shops, n, latest


def consolidate(rows, state_dir=".market", formula=objective.DEFAULT_FORMULA, now=None, hot_hours=HOT_HOURS):
    """Fold rows older than `hot_hours` into the lifetime counters, move them to the archive (removing them from
    `rows` in place) and rebuild the per-shop view. Returns (profiles, rows archived now).

    A row older than `archived_until` that shows up again (another process saved a copy it had loaded before the
    compaction) is already in the archive and is dropped, not counted twice."""
    state_dir = Path(state_dir)
    prof = load(state_dir)
    if prof.get("formula") != formula:
        life, n, latest = rebuild_life(state_dir, formula)
        prof = {"version": VERSION, "formula": formula, "life": life, "archived_rows": n,
                "archived_until": prof.get("archived_until") or latest,
                "rebuilt_at": time.strftime("%Y-%m-%d %H:%M"), "rebuilt_from": prof.get("formula")}
    cutoff = time.strftime("%Y-%m-%d %H:%M", time.localtime((now or time.time()) - hot_hours * 3600))
    until = prof.get("archived_until") or ""
    cold = [r for r in rows if (r.get("at") or "") < cutoff]
    moved = 0
    if cold:
        fresh = [r for r in cold if (r.get("at") or "") >= until]
        if fresh:
            state_dir.mkdir(exist_ok=True)
            with open(state_dir / ARCHIVE, "a", encoding="utf-8") as f:
                for r in fresh:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            for r in fresh:
                fold(prof["life"], r, formula)
            prof["archived_rows"] = prof.get("archived_rows", 0) + len(fresh)
        prof["archived_until"] = max(until, cutoff)
        rows[:] = [r for r in rows if (r.get("at") or "") >= cutoff]
        moved = len(cold)
    prof["shops"], summary = view(prof["life"], rows, formula)
    prof.update(summary)
    prof.update({"hot_rows": len(rows), "hot_hours": hot_hours, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    tmp = state_dir / (FILE + ".tmp")
    tmp.write_text(json.dumps(prof, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, state_dir / FILE)
    return prof, moved
