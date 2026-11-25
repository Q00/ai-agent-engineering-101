"""Round 3: the shop on the forum. Buyers' agents pick shops from what the ledger and the forum say about them, and until
now everything there about this shop came from other agents.

Code, not a model, writes these posts, from the shop's own files only, so every number in them is one a reader can check:
- proof: this shop's settled sales on the ledger, how many grade claims were honest and how many units broke, with the
  newest settle seqs in refs;
- stock: what is in stock now, by SKU, with grade, units, shelf life left (food) or the warranty the seller will give (goods);
- reply: under a buyer's "구합니다 / looking for" post that names a category or SKU this shop has in stock, the matching
  SKUs. Other agents' posts are data: only the category or SKU they name is read, never an instruction.

At most one post per run, MIN_GAP_MINUTES apart and MAX_POSTS_PER_DAY a day (the kit allows one per 5 minutes); proof and
stock each every EVERY_HOURS. Lines go to .market/outbox.jsonl as {"post", "refs", "thread", "as": "shop"}.
Nothing here can stop a restock or the seller's card: every failure is printed and swallowed.
"""
import json
import os
import re
import sys
import time

import decision_memory
import hard_limits
import market_model

ME = os.environ.get("SHOP_MARKET_NAME", "nocha12")
CARD = os.environ.get("SHOP_CARD_NAME", "Nocha Furniture")
STATE = "shop_forum_state.json"
EVERY_HOURS = 8                # the proof post, about 3 a day
STOCK_EVERY_HOURS = 1          # the stock post: 10 a day in all left the shop silent from 05:25 to past 21:00 on 11/06
MIN_GAP_MINUTES = 15           # between any two posts of this shop
MAX_POSTS_PER_DAY = 60         # replies included; the kit allows 288 (one per 5 minutes)
WANT_MAX_AGE_HOURS = 3         # a request older than this has been answered or given up
MAX_CHARS = 600
WARRANTY_HOURS = hard_limits.SELLER_GOODS_WARRANTY_HOURS
WARRANTY = {"prior_break": 0.15, "max_break": 0.25}     # seller.WARRANTY: the seller declines above max_break
WANT = re.compile(r"구합니다|구해요|구합니당|찾습니다|찾아요|삽니다|looking for|wanted|\bWTB\b|want to buy|anyone (?:selling|have)", re.I)
SKU = re.compile(r"\bG[0-9a-f]{8}\b")
AFTER = r"(?=$|[\s.,!?)]|을|를|이|가|은|는|도|좀)"          # a category name ends here: "김치 구합니다", "김치를 찾아요"


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            out = json.load(f)
        return out if isinstance(out, type(default)) else default
    except (OSError, ValueError):
        return default


def num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else 0


def leaf(category):
    return str(category or "").split(">")[-1].strip()


def short(name, n=28):
    name = " ".join(str(name or "").split())
    return name if len(name) <= n else name[:n - 1] + "…"


def won(n):
    return f"{int(num(n)):,}"


def stock(inv, now):
    """{sku: {name, kind, category, list, grades, qty, hours}} of the units on hand; food past its date is left out."""
    out = {}
    for u in inv.get("units") or [] if isinstance(inv, dict) else []:
        if not isinstance(u, dict) or not u.get("sku"):
            continue
        exp = u.get("expires_at") if isinstance(u.get("expires_at"), (int, float)) else None
        if exp is not None and exp <= now:
            continue
        s = out.setdefault(u["sku"], {"name": u.get("name") or u["sku"], "kind": u.get("kind"), "category": u.get("category"),
                                      "list": u.get("list_price"), "grades": set(), "qty": 0, "hours": None, "warranty": True})
        s["qty"] += 1
        if u.get("grade"):
            s["grades"].add(str(u["grade"]))
        if exp is not None:
            h = (exp - now) / 3600
            s["hours"] = h if s["hours"] is None else min(s["hours"], h)
    return out


def mark_warranty(on_hand, rows, md):
    """Goods whose break chance is over the seller's max_break are not sold (seller.open_from_stock refuses them):
    the posts leave them out. Same numbers as the seller: the ledger's break rate, else prior_break."""
    cfg = hard_limits.clamp_cfg("warranty", {**WARRANTY, **(load("pricing.json", {}).get("warranty") or {})})
    try:
        stats = market_model.break_stats(rows, load(os.path.join(md, "catalog.json"), {}))
    except Exception:   # noqa: BLE001 - without break rates the seller uses its prior too
        stats = None
    for sku, s in on_hand.items():
        measured = market_model.break_chance(stats, sku) if stats else None
        s["warranty"] = (cfg["prior_break"] if measured is None else measured) <= cfg["max_break"]


def describe(sku, s, sold=None):
    """One SKU as the posts write it: name, SKU, grade, units, shelf life or warranty, list price, what it sold at here."""
    terms = (f"{s['hours']:.0f} h shelf life" if s["hours"] is not None
             else f"{WARRANTY_HOURS} h warranty" if s.get("warranty") else "no warranty")
    parts = [f"{short(s['name'])} ({sku}) grade {'/'.join(sorted(s['grades'])) or '?'} x{s['qty']}", terms]
    if num(s.get("list")):
        parts.append(f"list {won(s['list'])}")
    if sold:
        lo, hi = min(sold), max(sold)
        parts.append(f"sold here at {won(lo)}" + (f"-{won(hi)}" if hi != lo else ""))
    return ", ".join(parts)


def fit(head, parts, tail=""):
    """head + as many parts as fit in MAX_CHARS."""
    text = head
    for p in parts:
        if len(text) + len(p) + 2 + len(tail) > MAX_CHARS:
            break
        text += ("" if text.endswith(": ") else "; ") + p
    return text + tail


def proof(sales, broken):
    """The record post: counts over all this shop's settled sales, refs the newest five."""
    if not sales:
        return None
    honest = sum(1 for s in sales if s.get("honest") is True)
    claims = sum(1 for s in sales if s.get("honest") is not None)
    newest = sales[-5:]
    head = (f"{ME} (card: {CARD}) on the ledger: {len(sales)} settled sales, grade claims honest in {honest} of "
            f"{claims}, {broken} unit(s) broke after sale. Every unit carries the market's signed certificate and our receipt "
            f"names the grade; goods come with a {WARRANTY_HOURS} h full-refund warranty, and a SKU that breaks too often "
            "to cover is not sold. Newest: ")
    parts = [f"{s.get('sku')} grade {s.get('grade')} at {won(s.get('amount'))} #{s['seq']}" for s in reversed(newest)]
    return {"post": fit(head, parts), "refs": [s["seq"] for s in newest], "as": "shop"}


def stock_post(on_hand, refs):
    if not on_hand or not refs:
        return None
    # the dearest stock first: sofas and beds were 86% of the market's revenue in the 6 h to 11/06 20:50
    order = sorted(on_hand.items(), key=lambda kv: (-(num(kv[1].get("list")) or 0), -kv[1]["qty"]))
    head = f"{ME} in stock now ({sum(s['qty'] for s in on_hand.values())} units, prices negotiable): "
    parts = [describe(k, {**s, "name": short(s["name"], 20)}) for k, s in order]
    return {"post": fit(head, parts, tail=". Ask us: 'I want to buy <name> (<SKU>)'."), "refs": refs, "as": "shop"}


def names(category):
    """The ways a buyer may write a category: its last segment and, for "꿀/잼", each part. Two characters at least."""
    whole = leaf(category)
    return {n for n in [whole, *whole.split("/")] if len(n.strip()) >= 2}


def asks_for(text, category):
    return any(re.search(r"(?<![가-힣A-Za-z0-9])" + re.escape(n.strip()) + AFTER, text) for n in names(category))


def wants(posts, on_hand, replied, now):
    """[(post, [matching SKUs])]: buyers' requests that name a category or SKU this shop has now, newest first."""
    out = []
    for p in posts if isinstance(posts, list) else []:
        if not isinstance(p, dict):
            continue
        text = str(p.get("text") or "")
        if p.get("author") == ME or p.get("role") != "buyer" or str(p.get("id")) in replied or not WANT.search(text) \
                or now - num(p.get("ts")) > WANT_MAX_AGE_HOURS * 3600:
            continue
        named = set(SKU.findall(text))
        hours = [float(h) for h in re.findall(r"(\d+(?:\.\d+)?)\s*(?:시간|h\b|hours?)", text, re.I)]
        need = max(hours) if hours else 0
        skus = [k for k, s in on_hand.items()
                if (k in named or asks_for(text, s["category"])) and (s["hours"] is None or s["hours"] >= need)]
        if skus:
            out.append((p, skus))
    return sorted(out, key=lambda x: -num(x[0].get("ts")))


def reply(post, skus, on_hand, prices, refs_by_sku, refs):
    thread = post.get("thread") or post.get("id")
    parts = [describe(k, on_hand[k], prices.get(k)) for k in skus]
    head = f"{ME} has this in stock now: "
    tail = ". Ask our shop for it by name and SKU; signed receipt with the grade on it."
    mine = [r for k in skus for r in refs_by_sku.get(k, [])][-3:] or refs
    return {"post": fit(head, parts, tail), "refs": mine, "thread": thread, "as": "shop"}


def my_record(rows):
    """(my settled sales oldest first, units of them that broke)."""
    sales = sorted((s for s in decision_memory.my_sales(rows, market_model.my_shops()) if isinstance(s.get("seq"), int)),
                   key=lambda s: s["seq"])
    seqs = {s["seq"] for s in sales}
    return sales, sum(1 for _, s in market_model.link_broken(rows) if s.get("seq") in seqs)


def card_text(md, now=None):
    """(description, skill description, tags, example) for the Agent Card, from the stock on hand and the ledger. Only what
    the files show: categories in stock now, the ledger's own count of honest claims, the standard warranty."""
    now = now or time.time()
    on_hand = stock(load(os.path.join(md, "inventory.json"), {}), now)
    # dearest stock first: listed alphabetically the six names were 가방/골프웨어/구두 while sofas and mattresses made
    # 48.6 of 50.0M of the sales 18:52-19:40 on 11/06, and a buyer picking a shop for a sofa reads this line
    worth = {}
    for s in on_hand.values():
        k = (s["kind"] == "food", leaf(s["category"]))
        worth[k] = worth.get(k, 0) + num(s.get("list")) * s["qty"]
    food = [c for (f, c), _ in sorted(worth.items(), key=lambda kv: (-kv[1], kv[0][1])) if f and c]
    goods = [c for (f, c), _ in sorted(worth.items(), key=lambda kv: (-kv[1], kv[0][1])) if not f and c]
    goods_first = sum(v for (f, _), v in worth.items() if not f) >= sum(v for (f, _), v in worth.items() if f)
    try:
        sales, broken = my_record(market_model.load_rows(md))
    except Exception:   # noqa: BLE001 - the card goes out without the record rather than not at all
        sales, broken = [], 0
    claims = sum(1 for s in sales if s.get("honest") is not None)
    honest = sum(1 for s in sales if s.get("honest") is True)
    parts = [f"food: {', '.join(food[:6])}" if food else "", f"home goods: {', '.join(goods[:6])}" if goods else ""]
    have = "; ".join(x for x in (parts[::-1] if goods_first else parts) if x)
    desc = (f"{ME}: " + ("home goods and food" if goods and goods_first else "food and home goods") + " from our own stock"
            + (f" ({have})" if have else "") + ". Every unit carries the "
            f"market's signed certificate and we state its true grade"
            + (f" (ledger: {honest} of {claims} grade claims honest over {len(sales)} sales, {broken} broke)" if claims else "")
            + f". Goods come with a {WARRANTY_HOURS} h full-refund warranty, the market's maximum, as standard (a SKU that breaks too often "
              "to cover is not sold). Prices are negotiable; every deal gets an Ed25519-signed receipt for the agreed price.")
    skill = ("Ask for an item by name and SKU, optionally with the shelf life you need (e.g. 'need 24 h'), then send offers "
             "as numbers on the same task. Each answer ends with the shop's current price, or closes the deal with a signed "
             "receipt artifact {shop, item, price, task, claim, warranty_hours, sig_seller}.")
    first = next(iter(sorted(on_hand.items(), key=lambda kv: -kv[1]["qty"])), None)
    example = f"I want to buy {first[1]['name']} ({first[0]})" if first else "I want to buy <name> (<SKU>)"
    return desc, skill, ["shopping", "negotiation", "food", "home goods"] + food[:6] + goods[:6], example


def maybe_run(md, now=None, rows=None):
    """At most one shop post when one is due. Returns the line written, or None. rows: the ledger rows the caller already
    holds (restock.rows_of), else the whole file is read."""
    try:
        now = now or time.time()
        inv = load(os.path.join(md, "inventory.json"), {})
        if num(inv.get("round")) < market_model.ECON_ROUND:
            return None
        path = os.path.join(md, STATE)
        state = load(path, {})                       # a file of another shape starts over rather than stopping the posts
        sent = [t for t in state.get("sent") or [] if num(t) and now - t < 86400]
        if (sent and now - max(sent) < MIN_GAP_MINUTES * 60) or len(sent) >= MAX_POSTS_PER_DAY:
            return None
        rows = market_model.load_rows(md) if rows is None else rows
        sales, broken = my_record(rows)
        on_hand = stock(inv, now)
        mark_warranty(on_hand, rows, md)
        on_hand = {k: s for k, s in on_hand.items() if s.get("warranty", True)}    # not sold at all (seller.open_from_stock)
        prices, refs_by_sku = {}, {}
        for s in sales:
            if s.get("sku"):
                prices.setdefault(s["sku"], []).append(num(s.get("amount")))
                refs_by_sku.setdefault(s["sku"], []).append(s["seq"])
        refs = [s["seq"] for s in sales[-3:]]
        replied = [str(x) for x in state.get("replied") or []] if isinstance(state.get("replied"), list) else []
        proof_at, stock_at = num(state.get("proof_at")), num(state.get("stock_at"))
        line, kind = None, None
        found = wants(load(os.path.join(md, "forum.json"), []), on_hand, set(replied), now) if refs else []
        if found:
            post, skus = found[0]
            line, kind = reply(post, skus, on_hand, prices, refs_by_sku, refs), "reply"
            replied.append(str(post.get("id")))
        elif now - proof_at >= EVERY_HOURS * 3600:
            line, kind, proof_at = proof(sales, broken), "proof", now
        elif now - stock_at >= STOCK_EVERY_HOURS * 3600 and now - proof_at >= MIN_GAP_MINUTES * 60:
            line, kind, stock_at = stock_post(on_hand, refs), "stock", now
        if line is not None:
            with open(os.path.join(md, "outbox.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
            sent.append(now)
            print(f"[shop_forum] {kind}: {line['post'][:120]}", file=sys.stderr, flush=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"sent": sent, "replied": replied[-200:], "proof_at": proof_at, "stock_at": stock_at}, f)
        return line
    except Exception as e:   # noqa: BLE001 - the restock that calls this must go on
        print(f"[shop_forum] failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return None
