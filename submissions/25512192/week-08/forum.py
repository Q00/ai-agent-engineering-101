"""The forum tool: the buyer posts a short market report built from the ledger, citing ledger entries.

Buyer rank is 0.3 forum votes, and votes come from other agents. A post that is useful to them (what sells,
at what share of list price, which shops' grade claims held up) and that they can check (refs = ledger seqs)
is the honest way to earn them. The text is made in code from state/market_stats.json, not by a model, so
it cannot carry anything the ledger does not show. Topics rotate; one post per POST_EVERY.
"""
import json
import time

import market_memory as mm

POST_EVERY = 5 * 60 + 10        # the market allows one per 5 minutes; post count is 0.1 of buyer ALL
MIN_SETTLES = 30
LAST = mm.STATE / "forum_last.json"


def short(category):
    return category.split(">")[-1]


def best_sellers(stats):
    rows = [(c, s) for c, s in mm.category_rank(stats) if s["n"]][:5]
    if not rows:
        return None, []
    text = "Round 3 best sellers, last 6 h (settled deals, median paid / list price): " + "; ".join(
        f"{short(c)} {s['n']} deals, {mm.median_ratio(s['ratios'], 0):.0%}" for c, s in rows)
    return text, [r for c, _ in rows for r in stats.get("refs", {}).get(c, [])[-2:]]


def cheap_categories(stats):
    rows = sorted(((c, mm.median_ratio(s["ratios"])) for c, s in stats["categories"].items() if len(s["ratios"]) >= 5),
                  key=lambda x: x[1])[:5]
    if not rows:
        return None, []
    text = "Buyers: categories where settled prices sit lowest against list price (median of the last 30): " + \
           "; ".join(f"{short(c)} {r:.0%}" for c, r in rows)
    return text, [r for c, _ in rows for r in stats.get("refs", {}).get(c, [])[-1:]]


def honest_shops(stats):
    rows = sorted(((k, v) for k, v in stats["shops"].items() if v["claims"] >= 5),
                  key=lambda kv: kv[1]["honest"] / kv[1]["claims"], reverse=True)
    if len(rows) < 3:
        return None, []
    text = "Grade claims checked against the ledger after settle (honest / claimed): " + "; ".join(
        f"{k} {v['honest']}/{v['claims']}" for k, v in rows[:4]) + " ... lowest: " + "; ".join(
        f"{k} {v['honest']}/{v['claims']}" for k, v in rows[-2:])
    return text, []


TOPICS = (best_sellers, cheap_categories, honest_shops)


def maybe_post(md, stats, log):
    last = mm.read_json(LAST, {"ts": 0, "topic": -1})
    if time.time() - last["ts"] < POST_EVERY or stats["settles"] < MIN_SETTLES:
        return
    for i in range(1, len(TOPICS) + 1):
        topic = (last["topic"] + i) % len(TOPICS)
        text, refs = TOPICS[topic](stats)
        if text:
            break
    else:
        return
    with open(md / "outbox.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"post": text[:800], "refs": [r for r in refs if r][:8], "as": "buyer"},
                           ensure_ascii=False) + "\n")
    mm.write_json(LAST, {"ts": time.time(), "topic": topic})
    log(f"[forum] queued: {text[:120]}")
