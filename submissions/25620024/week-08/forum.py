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
DEFAULTS = {"post_threshold": 1.0, "topic_cooldown_h": 2, "window_h": 6, "min_n": 5, "min_n_index": 30,
            "max_posts_day": {"buyer": 288, "shop": 24}}   # buyer ALL counts posts written as a buyer (0.1) and the
                                                           # votes on them (0.3): post as a buyer as often as the market
                                                           # allows (1 per 5 min), each post ledger-cited; shop posts few
WANTED = re.compile(r"구합니다|구해요|삽니다|필요해요|찾습니다|want(?:ed)? to buy|\bwanted\b|\bWTB\b|looking for|need (?:a|some)", re.I)
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
        if len(rs) < t["min_n_index"]:
            continue
        # the posts the forum votes up most (7 likes) are Korean market summaries with quartiles, the best seller
        # and eight ledger refs; ours in English with five refs stopped at 5-6 (11-04)
        ratios = sorted(r[3] / r[4] for r in rs)
        q = lambda f: ratios[min(len(ratios) - 1, int(f * len(ratios)))]
        cat_rows = market_model.catalog()
        sold = {}
        for r in rs:
            sold[r[2]] = sold.get(r[2], 0) + 1
        top = sorted(sold.items(), key=lambda kv: -kv[1])[:2]
        best = " / ".join(f"{((cat_rows.get(k) or {}).get('name') or k)[:22]} ({k}) {n}건" for k, n in top)
        low = min(rs, key=lambda r: r[3] / r[4])
        text = (f"[시세] {leaf(cat)}: 최근 {t['window_h']}시간 시장 정산 {len(rs)}건 (공개 원장 기준)\n"
                f"- 체결가/정가 중앙값 {statistics.median(ratios):.0%} (하위 25% {q(0.25):.0%}, 상위 25% {q(0.75):.0%})\n"
                f"- 가장 많이 팔린 상품: {best}\n"
                f"- 최저 체결: {low[2]} {low[3]:,}원 (정가의 {low[3] / low[4]:.0%})\n"
                f"- 근거: 원장 seq {', '.join(str(r[0]) for r in rs[-8:])}")
        out.append({"topic": f"price:{cat}", "as": "buyer", "text": text, "refs": [r[0] for r in rs][-8:],
                    "score": math.log(len(rs))})
    return out


def purchase_records(c, t, now, top=3):
    """As the buyer: our own settled purchases lately, each with its ledger entry, price against list and the grade
    the ledger checked. The kind of post that collects the most votes on this forum: anyone can verify it."""
    cat = market_model.catalog()
    rows = c.execute("""select seq, sku, amount, list_price, grade, claim_honest from settles
                        where buyer=? and ts>? and list_price>amount order by (list_price-amount)*1.0/list_price desc
                        limit ?""", (NAME, now - t["window_h"] * 3600, top)).fetchall()
    if len(rows) < top:
        return []
    parts = [f"- {((cat.get(sku) or {}).get('name') or sku)[:28]} ({sku}) | {a:,}원, 정가 {lp:,}원 대비 "
             f"{1 - a / lp:.0%} 저렴 | 등급 {g}{' (주장과 일치)' if h else ''} | 원장 #{seq}"
             for seq, sku, a, lp, g, h in rows]
    return [{"topic": f"records:{rows[0][0]}", "as": "buyer", "refs": [r[0] for r in rows], "score": 3.5,
             "text": "[구매 기록] 최근 정산된 구매 (공개 원장으로 확인 가능)\n" + "\n".join(parts)}]


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


def opening_ads(c, md, now):
    """As the shop, during the opening offer: one category we stock per post, the price range we will ask (as a share
    of list, never below cost), how many units, and ledger refs of our own settled, honestly graded sales. Facts a
    buyer can check; no ranking claims about other shops."""
    import promo
    t = promo.theta()
    if now >= t["intro_until"]:
        return []
    try:
        units = json.loads((Path(md) / "inventory.json").read_text(encoding="utf-8")).get("units", [])
    except (OSError, ValueError):
        return []
    refs = [r[0] for r in c.execute(f"select seq from settles where seq in ({market_model.ours(c)}) and claim_honest=1 "
                                    "order by seq desc limit 3")]
    by_cat = {}
    for u in units:
        if u.get("grade") != "expired":
            by_cat.setdefault(u.get("category"), []).append(u)
    out = []
    for cat, us in by_cat.items():
        u = max(us, key=lambda u: sum(1 for x in us if x["sku"] == u["sku"]))
        floor = u["cost"] * (1 + t["intro_margin"])
        lo, hi = (max(floor, u["list_price"] * a) for a in (min(t["intro_arms"]), max(t["intro_arms"])))
        until = time.strftime("%m/%d %H:%M", time.localtime(t["intro_until"]))
        out.append({"topic": f"ad:{cat}", "as": "shop", "refs": refs, "score": 2.5 + 0.01 * len(us),
                    "text": f"[opening offer until {until}] {leaf(cat)}: sku {u['sku']} {u['name'][:30]} | grade="
                            f"{u.get('grade')} cert=market-signed | list={u['list_price']} | asking {lo:.0f}-{hi:.0f}"
                            f" ({lo / u['list_price']:.0%}-{hi / u['list_price']:.0%} of list) | {len(us)} units in "
                            f"{leaf(cat)} | Ed25519 receipts, grade claims checked by the ledger"})
    return out


def follow_ads(c, md, now):
    """As the shop: a SKU we took over from the leaders, in stock, asked under the ledger's 24 h median."""
    import promo
    fo = promo.plan().get("follow_skus") or {}
    try:
        units = json.loads((Path(md) / "inventory.json").read_text(encoding="utf-8")).get("units", [])
    except (OSError, ValueError):
        return []
    out = []
    for sku, cut in fo.items():
        us = [u for u in units if u["sku"] == sku and u.get("grade") != "expired"]
        med, n = market_model.market_price(c, sku)
        if not us or not med:
            continue
        u = us[0]
        w = promo.theta().get("warranty_goods_h") or 0
        out.append({"topic": f"follow:{sku}", "as": "shop", "refs": [], "score": 2.9,
                    "text": f"[in stock] sku {sku} {u['name'][:34]} | {len(us)} units, grade {u.get('grade')} "
                            f"cert=market-signed{f', warranty {w}h' if u.get('kind') == 'goods' and w else ''} | asking about "
                            f"{cut:.0%} under the 24 h market median {med:.0f} (ledger, {n} settles) | ask: "
                            f"'I want to buy {u['name'][:20]} ({sku})'"})
    return out


WANT_BUDGET = re.compile(r"(\d[\d,\.]*)\s*(만\s*원|만원|KRW|원)", re.I)


def wanted_demand(md=Path(".market"), hours=6):
    """Buyers' 'wanted' posts on the forum, reduced to numbers (no forum text goes into a model prompt): the
    category it names (matched against the catalog), the budget, when. [{category, budget, author, ts}]"""
    try:
        posts = json.loads((Path(md) / "forum.json").read_text(encoding="utf-8"))
        posts = posts.get("posts", posts) if isinstance(posts, dict) else posts
    except (OSError, ValueError):
        return []
    leaves = {leaf(r.get("category")) for r in market_model.catalog(md).values() if r.get("category")}
    out, now = [], time.time()
    for p in posts or []:
        text = p.get("text") or ""
        if p.get("author") == NAME or now - (p.get("ts") or 0) > hours * 3600 or not WANTED.search(text):
            continue
        m = WANT_BUDGET.search(text)
        budget = None
        if m:
            n = float(m.group(1).replace(",", ""))
            budget = int(n * 10000) if "만" in m.group(2) else int(n)
        cat = max((x for x in leaves if x and x in text), key=len, default=None)
        if cat or budget:
            out.append({"category": cat, "budget": budget, "author": str(p.get("author"))[:24], "ts": p.get("ts")})
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
    # a post's score is the Wilson lower bound of its votes (6 likes 0.61, 7 likes 0.646) and buyer ALL averages it:
    # posts that name other shops (honesty, breakage) risk their dislikes, so the buyer posts only checkable records
    cands = purchase_records(c, t, now, top=5) + price_index(c, t, now) + offers(forum, md, now) \
        + opening_ads(c, md, now) + follow_ads(c, md, now)
    lines = []
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    if state.get("day") != day:
        state["day"], state["today"] = day, {}
    for role in ("buyer", "shop"):
        if now - state["last"].get(role, 0) < 300:
            continue
        if state.setdefault("today", {}).get(role, 0) >= (t["max_posts_day"] or {}).get(role, 99):
            continue
        fresh = [x for x in cands if x["as"] == role and now - state["posted"].get(x["topic"], 0) > t["topic_cooldown_h"] * 3600]
        best = max(fresh, key=lambda x: x["score"], default=None)
        if best and best["score"] >= t["post_threshold"]:
            line = {"post": best["text"][:700], "refs": best["refs"], "as": role}
            if best.get("thread"):
                line["thread"] = best["thread"]
            lines.append(line)
            state["posted"][best["topic"]] = now
            state["last"][role] = now
            state["today"][role] = state["today"].get(role, 0) + 1
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
