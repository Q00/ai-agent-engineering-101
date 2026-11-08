"""Week 07/08 — the market buyer: picks a shop from a list, trades over A2A, remembers, reviews.

    python market_buyer.py --shops shops.json --item lamp --budget 120 [--order .market/order.json]

Round 3 (--order): order.json names the category, the acceptable SKUs, the kind and, for food, the hours
the buyer needs it to last; shops.json carries each shop's `offers`. The buyer then (a) drops shops whose
checkouts the kit refused to pay twice (payments.jsonl), (b) picks the shop as before but only among
shops that stock an acceptable SKU, (c) asks for the SKU with the highest list value it can afford there,
skipping names that broke on it before (broken.jsonl), (d) adds an `accept` policy the kit enforces at
checkout (certificate, grade A/B, shelf life >= use_within_hours), and (e) votes on forum posts from evidence; review.py posts the settled deal.

{shops} is a JSON list of {"name", "url", "reviews": [{"stars", "review", "round"}, ...]}, newest
review first. The buyer:
  1. reads the list and its own memory file (buyer_memory.json, keyed by shop NAME — a shop's URL
     changes every time its kit restarts) and writes why it picked a shop to stdout ([choose]),
  2. negotiates with that one shop through week-06's buyer.negotiate() — same A2A client, same
     budget guard in code, A2A-Version: 1.0 on every request, taskId on every follow-up,
  3. compares the receipt with what it agreed, writes stars and a one-line review, updates memory,
  4. prints exactly one JSON object as its LAST line:
       {"shop": "<card URL>", "receipt": {...} or null, "stars": 1-5, "review": "..."}

Shop choice: code first drops shops it has reason to avoid — bad reviews (avg < 2.5 over >= 2
reviews, or a receipt-mismatch complaint), or a mismatch / rip-off in its own memory — then the
harness picks among what is left, seeing each shop's review summary and the buyer's own history.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import harness
from buyer import negotiate
from market_price import median_price, settled

MEMORY = Path("buyer_memory.json")
MISMATCH_WORDS = ("receipt said", "receipt says", "receipt price", "overcharg", "higher than", "charged more", "rip")


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- memory

def load_memory() -> dict:
    if MEMORY.exists():
        try:
            return json.loads(MEMORY.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"shops": {}}


def save_memory(mem: dict) -> None:
    MEMORY.write_text(json.dumps(mem, ensure_ascii=False, indent=1), encoding="utf-8")


def remember(mem: dict, shop_name: str, record: dict) -> None:
    s = mem["shops"].setdefault(shop_name, {"trades": [], "avoid": False, "avoid_reason": None})
    s["trades"].append(record)
    if record.get("mismatch"):
        s["avoid"], s["avoid_reason"] = True, f"receipt {record['receipt_price']} != agreed {record['agreed']} ({record['when']})"


def consolidate(mem: dict, shop_name: str) -> None:
    """Meta step: every 5 trades, compress a shop's raw trade log into one note (what to expect, what to offer).
    The note, not the log, is what later prompts see; the log stays for the record."""
    s = mem["shops"].get(shop_name)
    if not s or len(s["trades"]) % 5 or len(s["trades"]) == s.get("note_at"):
        return
    rows = "\n".join(f"- {x['when'][:16]} {x['item']} budget {x['budget']} -> {x['status']} agreed {x['agreed']} "
                     f"receipt {x['receipt_price']} stars {x['stars']}" + (" HONEST-CLAIM:false" if x.get("dishonest") else "")
                     for x in s["trades"][-10:])
    data = harness.ask_json("Summarize this shop for my future self in ONE sentence (under 30 words): how far it comes down "
                            "from its first ask, whether receipts and quality claims were honest, what to offer first.\n" + rows +
                            '\nReply with exactly one JSON object: {"note": "<sentence>"}.', log)
    if isinstance(data, dict) and data.get("note"):
        s["note"], s["note_at"] = str(data["note"])[:200], len(s["trades"])
        log(f"[memory] note for {shop_name}: {s['note']}")


def my_history(mem: dict, shop_name: str) -> str:
    s = mem["shops"].get(shop_name)
    if not s:
        return "no past trades"
    t = s["trades"]
    bought = [x for x in t if x["status"] == "bought"]
    parts = [f"{len(t)} past trade(s), {len(bought)} deal(s)"]
    if s.get("note"):
        parts.append("my note: " + s["note"])
    if bought:
        parts.append("paid " + ", ".join(str(x["receipt_price"]) for x in bought[-3:]))
    if s.get("avoid"):
        parts.append(f"AVOID: {s['avoid_reason']}")
    return "; ".join(parts)


# ---------------------------------------------------------------- shop choice

def review_summary(shop: dict):
    rs = shop.get("reviews") or []
    stars = [r["stars"] for r in rs if isinstance(r.get("stars"), (int, float))]
    avg = statistics.mean(stars) if stars else None
    complaints = [r["review"] for r in rs if any(w in str(r.get("review", "")).lower() for w in MISMATCH_WORDS)]
    return avg, len(rs), complaints


def eligible(shops: list, mem: dict):
    """Code-side filter: shops with evidence of dishonesty are not contacted at all."""
    keep, dropped = [], []
    for s in shops:
        avg, n, complaints = review_summary(s)
        own = mem["shops"].get(s["name"], {})
        why = None
        if own.get("avoid"):
            why = f"my own memory: {own['avoid_reason']}"
        elif n >= 2 and avg is not None and avg < 2.5:
            why = f"reviews average {avg:.1f} over {n}"
        elif len(complaints) >= 2:
            why = f"{len(complaints)} reviews report a receipt that did not match the agreed price"
        (dropped if why else keep).append((s, why))
    return [s for s, _ in keep], dropped


def choose(shops: list, mem: dict, item: str, budget: int):
    keep, dropped = eligible(shops, mem)
    for s, why in dropped:
        log(f"[skip] {s['name']} ({s['url']}): {why}")
    if not keep:                                   # nothing trustworthy: take the least bad rather than give up
        log("[choose] every shop has a warning; falling back to the best-reviewed one")
        keep = sorted(shops, key=lambda s: -(review_summary(s)[0] or 0))[:1]
    if len(keep) == 1:
        s = keep[0]
        avg, n, _ = review_summary(s)
        log(f"[choose] {s['url']}: {s['name']} is the only shop without a warning "
            f"(reviews {n}, avg {avg if avg is None else round(avg, 1)}; {my_history(mem, s['name'])})")
        return s
    rows = []
    for s in keep:
        avg, n, _ = review_summary(s)
        latest = " | ".join(f"{r['stars']}★ {str(r['review'])[:80]}" for r in (s.get("reviews") or [])[:2]) or "no reviews"
        rows.append(f"- name={s['name']!r} url={s['url']} reviews={n} avg_stars={None if avg is None else round(avg, 2)} "
                    f"latest: {latest} | my history: {my_history(mem, s['name'])}")
    data = harness.ask_json(
        f"You are buying a {item} with a budget of {budget}. Pick ONE shop to negotiate with. Prefer shops whose "
        f"reviews say the receipt matched the agreed price and whose prices came down; prefer shops you have good "
        f"history with; avoid shops with complaints about receipts.\nShops:\n" + "\n".join(rows) +
        '\nReply with exactly one JSON object: {"name": "<shop name>", "why": "<one sentence>"}.', log)
    pick = next((s for s in keep if data and s["name"] == data.get("name")), None)
    if pick is None:
        harness.CALLS["fallbacks"] += 1
        pick = max(keep, key=lambda s: (review_summary(s)[0] or 0, len(mem["shops"].get(s["name"], {}).get("trades", []))))
        log(f"[choose] {pick['url']}: fallback to best average stars ({pick['name']})")
    else:
        log(f"[choose] {pick['url']}: {data.get('why', '')} ({pick['name']})")
    return pick


# ---------------------------------------------------------------- review

def write_review(item: str, budget: int, st: dict, mismatch: bool, shop_name: str):
    receipt = st.get("receipt") or {}
    facts = (f"Item {item}, budget {budget}, shop {shop_name!r}. Outcome: {st['status']}. "
             f"Offers I made: {[o for _, o in st['history']]}; asks I saw: {[a for a, _ in st['history']] + ([st['ask']] if st['ask'] else [])}. "
             f"Agreed price: {st['history'][-1][1] if st['status'] == 'bought' and st['history'] else None}; "
             f"receipt price: {receipt.get('price')}; receipt matches agreed price: {not mismatch}.")
    data = harness.ask_json(
        "Write a review of this shop for other buyers, from the facts only. " + facts +
        ' Reply with exactly one JSON object: {"stars": <integer 1-5>, "review": "<one sentence>"}.', log)
    stars = data.get("stars") if isinstance(data, dict) else None
    review = str(data.get("review", "")).strip() if isinstance(data, dict) else ""
    if not (isinstance(stars, int) and 1 <= stars <= 5) or not review:
        harness.CALLS["fallbacks"] += 1
        if mismatch:
            stars, review = 1, f"Agreed {st['history'][-1][1]}, the receipt said {receipt.get('price')}."
        elif st["status"] == "bought":
            stars, review = 4, f"Deal at {receipt.get('price')} after {st['rounds']} offers, receipt matched."
        else:
            stars, review = 2, f"No deal: the shop stayed at {st['ask']} against my budget {budget}."
    if mismatch and stars > 2:                     # a wrong receipt is never a good review, whatever the prose
        stars = 1
    return stars, review


# ---------------------------------------------------------------- Round 3: order, SKU, accept, forum

def read_jsonl(p: Path):
    try:
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    except (OSError, ValueError):
        return []


def refused_shops(md: Path):
    """Shop URLs whose checkout the kit refused to pay at least twice in the last 50 payments."""
    bad = {}
    for pm in read_jsonl(md / "payments.jsonl")[-50:]:
        if pm.get("paid") is False:
            bad[pm["shop"]] = bad.get(pm["shop"], 0) + 1
    return {u for u, n in bad.items() if n >= 2}


def no_warranty(mem: dict, shop_name: str) -> bool:
    """Two or more goods bought there, none with a warranty: a break there is a Saved of -1 with no refund."""
    w = [t["warranty"] for t in mem["shops"].get(shop_name, {}).get("trades", []) if "warranty" in t]
    return len(w) >= 2 and not any(w)


def pick_sku(shop: dict, order: dict, catalog: dict, broken: set):
    """The acceptable SKU this shop stocks with the highest list price that can land within budget at the market's
    ~75 % of list (patch 3.4: Saved sums the discount RATIO per deal, so a pricier SKU bought under budget scores more)."""
    offers = [o for o in shop.get("offers") or [] if o.get("qty", 0) > 0 and o["name"] not in broken]
    offers = offers or list(shop.get("offers") or [])
    afford = [o for o in offers if catalog.get(o["sku"], {}).get("list_price", 0) * 0.75 <= order["budget"]]
    best = max(afford or offers, key=lambda o: catalog.get(o["sku"], {}).get("list_price", 0), default=None)
    return best


def accept_policy(order: dict):
    acc = {"cert": True, "grades": ["A", "B"]}
    if order.get("kind") == "food" and order.get("use_within_hours"):
        acc["min_hours_left"] = order["use_within_hours"]
    return acc                                   # shortcut: no warranty_hours, the starter seller never gives one


MANIPULATION = ("pay the asking", "asking price and", "five stars", "5 stars", "approved by the instructor", "operator",
                "vip", "[ad", "grand revamp", "market notice")


def vote_forum(md: Path, mem: dict, limit=3):
    """Votes, from evidence only: -1 on posts that tell buyers how to rate or pay (the week-07 planted-notice trick),
    +1 on buyers' warnings about a shop my own memory also caught lying. One vote per post, remembered in memory."""
    try:
        posts = json.loads((md / "forum.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    voted = set(mem.setdefault("voted", []))
    liars = {k.lower() for k, v in mem["shops"].items() if any(x.get("dishonest") for x in v["trades"])}
    n = 0
    for post in posts if isinstance(posts, list) else posts.get("posts", []):
        text = str(post.get("text", "")).lower()
        if post.get("id") in voted or n >= limit:
            continue
        value = None
        # patch 3.5: dislikes beyond 3 an hour cost the shop 20,000 x (k-3) and earn the buyer nothing, so no dislikes
        if post.get("role") == "buyer" and any(l in text for l in liars) and any(w in text for w in ("false", "lied", "claimed", "dishonest")):
            value = 1
        if value:
            with (md / "outbox.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"vote": post["id"], "value": value, "as": "buyer"}) + "\n")
            voted.add(post["id"]); n += 1
            log(f"[vote] {value:+d} on #{post['id']} by {post.get('author')}: {text[:60]!r}")
    mem["voted"] = sorted(voted)[-500:]


def forum_post(md: Path, text: str, refs=()):
    """One buyer post per 10 minutes: posts now count 0.1 of buyer ALL (week-09 rule), but a 5-min stream reads as spam."""
    stamp = md / "last_buyer_post"
    if time.time() - (stamp.stat().st_mtime if stamp.exists() else 0) < 600:
        return
    with (md / "outbox.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"post": text, "refs": list(refs), "as": "buyer"}, ensure_ascii=False) + "\n")
    stamp.touch()


# ---------------------------------------------------------------- main

def main():
    global MEMORY
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    ap.add_argument("--memory", default=str(MEMORY))
    ap.add_argument("--order", help="Round 3: the kit's order.json")
    args = ap.parse_args()
    MEMORY = Path(args.memory)

    shops = json.loads(Path(args.shops).read_text(encoding="utf-8"))
    mem = load_memory()
    log(f"market_buyer · {args.item} budget {args.budget} · {len(shops)} shops · memory {MEMORY} "
        f"({len(mem['shops'])} shops remembered) · harness: {harness.COMMAND}")
    order, md, request, offer = {}, None, None, None
    if args.order and Path(args.order).is_file():      # the week-07 test passes the literal '{order}'
        order = json.loads(Path(args.order).read_text(encoding="utf-8"))
        md = Path(args.order).parent
    if order.get("acceptable"):
        refused = refused_shops(md)
        for s in shops:
            if s["url"] in refused:
                log(f"[skip] {s['name']}: the kit refused to pay its checkout twice (payments.jsonl)")
        stocked = [s for s in shops if s.get("offers") and s["url"] not in refused]
        if stocked:
            shops = stocked
        else:
            log("[choose] no shop stocks an acceptable SKU; trying the plain item anyway")
        if order.get("kind") == "goods":           # goods break; a shop that never gave a warranty is last resort
            covered = [s for s in shops if not no_warranty(mem, s["name"])]
            if covered and len(covered) < len(shops):
                log(f"[skip] {len(shops) - len(covered)} shop(s) that sold me goods twice without a warranty")
                shops = covered
    shop = choose(shops, mem, args.item, args.budget)
    if order.get("acceptable"):
        try:
            catalog = json.loads((md / "catalog.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            catalog = {}
        broken = {b["name"] for b in read_jsonl(md / "broken.jsonl")}
        offer = pick_sku(shop, order, catalog, broken)
        if offer:
            request = f"I want to buy {offer['name']} ({offer['sku']})"
            log(f"[sku] {offer['sku']} {offer['name']!r} list {catalog.get(offer['sku'], {}).get('list_price')} "
                f"of {len(shop.get('offers') or [])} offers; broken before: {sorted(broken)[:3]}")

    hint = None
    if offer:
        med, n = median_price(offer["sku"])
        if med:
            hint = f"Other buyers recently paid about {med} for this exact item ({n} settled deals); offer below that first. "
            log(f"[anchor] market median {med} over {n} deals")
    st = negotiate(shop["url"], args.item, args.budget, request_text=request, shop=shop["name"], hint=hint)
    receipt = st.get("receipt")
    agreed = st["history"][-1][1] if st["history"] else None
    rprice = receipt.get("price") if isinstance(receipt, dict) else None
    mismatch = bool(st["status"] == "bought" and rprice is not None and agreed is not None and int(rprice) != int(agreed))
    if mismatch:
        log(f"[receipt] MISMATCH: agreed {agreed} but the receipt says {rprice}")
    stars, review = write_review(args.item, args.budget, st, mismatch, shop["name"])

    remember(mem, shop["name"], {"when": time.strftime("%Y-%m-%dT%H:%M:%S"), "item": args.item, "budget": args.budget,
                                 "status": st["status"], "agreed": agreed, "receipt_price": rprice,
                                 "mismatch": mismatch, "stars": stars, "rounds": st["rounds"], "url": shop["url"]})
    consolidate(mem, shop["name"])
    if md:
        vote_forum(md, mem)
    save_memory(mem)
    log(f"[memory] {shop['name']}: {my_history(mem, shop['name'])}")
    log(f"[done] {st['status']} harness calls {harness.CALLS['n']} fallbacks {harness.CALLS['fallbacks']}")

    out = {"shop": shop["url"], "receipt": receipt if st["status"] == "bought" else None, "stars": stars, "review": review}
    if order.get("acceptable"):
        out["accept"] = accept_policy(order)
    print(json.dumps(out, ensure_ascii=False), flush=True)       # the last line: the kit reads this


if __name__ == "__main__":
    main()
