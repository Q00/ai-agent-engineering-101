"""Week 08 wanted posts: forum posts in which a buyer asks for something, and the category it asks for.

Seen on the first hour of Round 3 (11-03): the market's buyers post "신선육류 구합니다. 예산은 2만 원대이고, 18시간 안에
먹을 예정입니다" and students' buyers post "Wanted: G0011e806, ...; category=식품>계란/유제품>우유/두유; ...". So a post
is a wanted post when it has a wanting word, and its category is, in this order: an explicit `category=`, the
category of a SKU it names, or the longest demand.json `item` (e.g. 신선육류, 소스/양념/조미료) in its text.
Code only, no model: the rule is easy to check against the forum and costs nothing per post.

Three readers: buyer_forum.py replies with the shops that hold the category now, forum.py replies when our shop
holds it, restock.py buys one unit of a wanted category the shop does not hold.
"""
import json
import re

WANT = re.compile(r"구합니다|구해요|구함|찾습니다|찾아요|사고 싶|wanted|looking for|\bwtb\b|need to buy|anyone (?:have|sell)",
                  re.I)
SKU = re.compile(r"\bG[0-9a-f]{8}\b")
CATEGORY = re.compile(r"category\s*[=:]\s*([^;,\n]+)")
HANGUL = re.compile(r"[가-힣]")


def items(demand):
    """demand.json item -> category, longest item first (so 신선육류 wins over a shorter item inside it)."""
    out = {d["item"]: d["category"] for d in demand if d.get("item") and len(d["item"]) >= 2}
    return dict(sorted(out.items(), key=lambda kv: -len(kv[0])))


def parse(text, cat, by_item):
    """{"category", "skus"} for a wanted post, None for any other post."""
    if not text or not WANT.search(text):
        return None
    cats = set(by_item.values())
    m = CATEGORY.search(text)
    c = m.group(1).strip() if m and m.group(1).strip() in cats else None
    skus = [s for s in SKU.findall(text) if s in cat]
    if not c and skus:
        c = cat[skus[0]]["category"]
    if not c:
        c = next((v for k, v in by_item.items() if k in text), None)
    return {"category": c, "skus": [s for s in skus if cat[s]["category"] == c]} if c else None


def recent(db, cat, demand, since, skip_author=("inextro",)):
    """Wanted posts (not replies, not ours) since a time, oldest first, from the collector's forum_posts."""
    by_item = items(demand)
    out = []
    for r in db.execute("SELECT id, ts, author, role, thread, text FROM forum_posts WHERE ts >= ? AND thread IS NULL "
                        "ORDER BY ts", (since,)):
        if str(r["author"]).lower() in skip_author:
            continue
        w = parse(r["text"], cat, by_item)
        if w:
            out.append({"id": r["id"], "ts": r["ts"], "author": r["author"], "role": r["role"], "text": r["text"],
                        "korean": bool(HANGUL.search(r["text"] or "")), **w})
    return out


def item_name(demand, category):
    return next((d["item"] for d in demand if d["category"] == category), category.split(">")[-1])


def stock_by_shop(shops, cat, category, skip=("inextro",)):
    """GET /api/shops `stock` (SKU -> units) -> [(shop, units in the category)], most units first."""
    out = []
    for s in shops:
        if s.get("name") in skip:
            continue
        n = sum(k for sku, k in (s.get("stock") or {}).items() if k and cat.get(sku, {}).get("category") == category)
        if n:
            out.append((s["name"], n))
    return sorted(out, key=lambda x: -x[1])


def settles_in(db, cat, category, since):
    """(seq, % of list paid) of the market's settles in a category since a time."""
    out = []
    for r in db.execute("SELECT seq, data FROM ledger WHERE round >= 3 AND kind='settle' AND ts >= ?", (since,)):
        d = json.loads(r["data"])
        x = cat.get(d.get("item") or d.get("sku")) or {}
        price = d.get("amount") or d.get("price")
        if x.get("category") == category and price and x.get("list_price"):
            out.append((r["seq"], round(100 * price / x["list_price"])))
    return out
