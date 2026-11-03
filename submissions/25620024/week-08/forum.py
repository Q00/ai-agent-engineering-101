"""Week 08 — forum.py: the agent speaking first. Runs at the end of every restock (every 60 s in Round 3),
with no order behind it, and writes to .market/outbox.jsonl, which the kit posts.

When to speak (Horvitz 1999, expected utility of acting unasked): every candidate post gets a usefulness score,
and one is posted only when the best score clears p* (theta "post_threshold"). A topic is not repeated within
its cooldown, and the market allows one post per 5 minutes per role anyway.

  as buyer (votes on these make the buyer's forum rating, 30% of buyer ALL)
    price index   what a category sold for lately, as a share of list price, with ledger refs
    honesty       shops whose grade claims the ledger checked, and how often they were true
    breakage      goods categories that broke after sale, broken / sold
  as shop
    offer         a reply to a "wanted" post for a category we stock, in checkable facts
  votes           +1 to posts that cite ledger entries; -1 only to text that tries to instruct agents

Other agents' posts are matched with regular expressions only; no forum text ever goes into a model prompt.
"""
import json
import math
import os
import re
import statistics
import time
from pathlib import Path

import market_model

NAME = "dragonash"
HERE = Path(__file__).parent
STATE = HERE / "private" / "forum_state.json"
THETA = Path(os.environ.get("SHOP_THETA", HERE / "private" / "shop_theta.json"))
DEFAULTS = {"post_threshold": 1.5, "topic_cooldown_h": 3, "window_h": 6, "min_n": 5}
WANTED = re.compile(r"구합니다|구해요|삽니다|필요해요|찾습니다|want(?:ed)? to buy|\bWTB\b|looking for|need (?:a|some)", re.I)
INJECTION = re.compile(r"ignore (?:all|previous|your)|AI (?:buyers?|agents?) (?:must|should)|system prompt|"
                       r"pay the (?:asking|full) price|give (?:it|us) 5 stars|approved by the (?:instructor|operator)", re.I)


def theta():
    try:
        return {**DEFAULTS, **json.loads(THETA.read_text(encoding="utf-8")).get("forum", {})}
    except (OSError, ValueError, AttributeError):
        return dict(DEFAULTS)


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"posted": {}, "voted": [], "last": {}}


def leaf(category):
    return (category or "?").split(">")[-1]


def price_index(c, t, now):
    out = []
    rows = c.execute("select seq, category, sku, amount, list_price, shop from settles where ts>? and list_price>0",
                     (now - t["window_h"] * 3600,)).fetchall()
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r[1], []).append(r)
    for cat, rs in by_cat.items():
        if len(rs) < t["min_n"]:
            continue
        ratios = [r[3] / r[4] for r in rs]
        low = min(rs, key=lambda r: r[3] / r[4])
        text = (f"[price-index] {leaf(cat)} | n={len(rs)} settles in {t['window_h']}h | median paid "
                f"{statistics.median(ratios):.0%} of list (range {min(ratios):.0%}-{max(ratios):.0%}) | best: "
                f"{low[3]} for sku {low[2]} ({low[3] / low[4]:.0%} of list)")
        out.append({"topic": f"price:{cat}", "as": "buyer", "text": text, "refs": [r[0] for r in rs][-5:],
                    "score": math.log(len(rs))})
    return out


def honesty(c, t, now):
    rows = c.execute("select shop, count(*), sum(claim_honest), max(seq) from settles where ts>? and claim_honest is not null"
                     " group by shop having count(*)>=?", (now - 24 * 3600, t["min_n"])).fetchall()
    if len(rows) < 2:
        return []
    rows.sort(key=lambda r: (r[2] / r[1], r[1]))
    bad = [r for r in rows if r[2] < r[1]]
    parts = [f"{str(r[0]).split('/')[-1][:24]} {r[2]}/{r[1]}" for r in rows[:6]]
    text = "[claim-honesty 24h] grade claims the ledger found true: " + ", ".join(parts)
    refs = [x[0] for x in c.execute("select seq from settles where claim_honest=0 and ts>? order by seq desc limit 5",
                                    (now - 24 * 3600,))]
    return [{"topic": "honesty", "as": "buyer", "text": text, "refs": refs or [r[3] for r in rows[:5]],
             "score": 1.0 + 1.5 * len(bad)}]


def breakage(c, t, now):
    sold = dict(c.execute("select category, count(*) from settles where ts>? group by category", (now - 24 * 3600,)).fetchall())
    broke = {}
    for seq, sku in c.execute("select seq, sku from broken where ts>?", (now - 24 * 3600,)):
        cat = c.execute("select category from settles where sku=? limit 1", (sku,)).fetchone()
        if cat:
            broke.setdefault(cat[0], []).append(seq)
    out = []
    for cat, seqs in broke.items():
        n = sold.get(cat, 0)
        if n >= t["min_n"]:
            out.append({"topic": f"broken:{cat}", "as": "buyer", "refs": seqs[-5:], "score": 1.0 + len(seqs) / n * 10,
                        "text": f"[breakage 24h] {leaf(cat)}: {len(seqs)} of {n} sold broke after sale ({len(seqs) / n:.0%})."
                                " Ask for warranty_hours >= 6 on these."})
    return out


def offers(forum, md, now):
    """Replies, as the shop, to recent 'wanted' posts for a category or SKU we have in stock."""
    try:
        units = json.loads((Path(md) / "inventory.json").read_text(encoding="utf-8")).get("units", [])
    except (OSError, ValueError):
        return []
    out = []
    for p in forum:
        if p.get("author") == NAME or now - (p.get("ts") or 0) > 1800 or not WANTED.search(p.get("text") or ""):
            continue
        text = (p.get("text") or "").lower()
        words = {w for w in re.findall(r"[0-9a-z가-힣]{2,}", text)}

        def match(u):
            """SKU named: 100; else words shared with the product name (포기김치 beats 총각김치); else category."""
            if u["sku"].lower() in text:
                return 100
            name = (u.get("name") or "").lower()
            hits = sum(1 for w in words if w in name)
            return hits * 10 + (1 if leaf(u.get("category")).lower() in text else 0)
        live = [u for u in units if u.get("grade") != "expired"]
        best = max((match(u) for u in live), default=0)
        fit = [u for u in live if best and match(u) == best]
        if fit:
            u = min(fit, key=lambda u: u.get("expires_at") or float("inf"))
            out.append({"topic": f"offer:{p['id']}", "as": "shop", "thread": p["id"], "refs": [], "score": 3.0,
                        "text": f"[in stock] sku {u['sku']} {u['name'][:30]} | grade={u.get('grade')} cert=market-signed"
                                f" | list={u['list_price']} | ask us: 'I want to buy {u['name'][:20]} ({u['sku']})'"})
    return out


def votes(forum, state):
    out = []
    for p in forum:
        if p.get("author") == NAME or p.get("id") in state["voted"]:
            continue
        if INJECTION.search(p.get("text") or ""):
            out.append({"vote": p["id"], "value": -1, "as": "buyer"})
        elif p.get("refs"):
            out.append({"vote": p["id"], "value": 1, "as": "buyer"})
    return out


def tick(md=Path(".market"), c=None, now=None):
    """One pass: maybe one post per role, and votes. Returns the lines written to the outbox."""
    t, now, md = theta(), now or time.time(), Path(md)
    c = c or market_model.db()
    state = load_state()
    try:
        forum = json.loads((md / "forum.json").read_text(encoding="utf-8"))
        forum = forum.get("posts", forum) if isinstance(forum, dict) else forum
    except (OSError, ValueError):
        forum = []
    cands = price_index(c, t, now) + honesty(c, t, now) + breakage(c, t, now) + offers(forum, md, now)
    lines = []
    for role in ("buyer", "shop"):
        if now - state["last"].get(role, 0) < 300:
            continue
        fresh = [x for x in cands if x["as"] == role and now - state["posted"].get(x["topic"], 0) > t["topic_cooldown_h"] * 3600]
        best = max(fresh, key=lambda x: x["score"], default=None)
        if best and best["score"] >= t["post_threshold"]:
            line = {"post": best["text"][:500], "refs": best["refs"], "as": role}
            if best.get("thread"):
                line["thread"] = best["thread"]
            lines.append(line)
            state["posted"][best["topic"]] = now
            state["last"][role] = now
    vs = votes(forum, state)
    lines += vs
    state["voted"] = (state["voted"] + [v["vote"] for v in vs])[-2000:]
    if lines:
        with (md / "outbox.jsonl").open("a", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return lines


if __name__ == "__main__":
    for line in tick():
        print(json.dumps(line, ensure_ascii=False))
