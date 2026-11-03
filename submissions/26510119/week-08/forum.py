"""The forum writer (D): posts only when there is new evidence, votes for posts that cite the ledger. No LLM.

Called from restock.py (every 60 s), so it needs no process of its own. Writes lines to $MARKET_DIR/outbox.jsonl;
the kit sends them (one post per 5 minutes per role; votes unlimited).

When to speak first (week 09, Horvitz 1999): a post that tells other buyers nothing new is noise, so the
buyer posts only when it has something to show, with ledger numbers as proof:
  - honesty report: one of our purchases settled with a false grade claim, or goods we bought broke;
  - price report: at least `min_new_settles` new settlements since the last report.
The shop posts what it has in stock now and then (shop votes do not count toward the shop's rank).
"""
import json
import os
import time
from pathlib import Path

import market_intel
import policy

MARKET_DIR = Path(os.environ.get("MARKET_DIR", Path(__file__).with_name(".market")))
STATE = MARKET_DIR / "forum_state.json"
BUYER_MEMORY = Path(os.environ.get("BUYER_MEMORY") or Path(__file__).with_name("buyer_memory.jsonl"))
ME = os.environ.get("SHOP_NAME", "olstjr")


def _state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"buyer_last": 0, "shop_last": 0, "settles_seen": 0, "reported": [], "voted": []}


def _save(st):
    STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def _out(line):
    with open(MARKET_DIR / "outbox.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def _memory():
    if not BUYER_MEMORY.exists():
        return []
    rows = []
    for line in BUYER_MEMORY.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def honesty_report(st):
    """Our own settled purchases with a false grade claim or broken goods, not reported yet."""
    settled = [m for m in _memory() if m.get("kind") == "settled" and m.get("ledger") not in st["reported"]]
    try:
        broken = [json.loads(x) for x in (MARKET_DIR / "broken.jsonl").read_text(encoding="utf-8").splitlines() if x]
    except (OSError, ValueError):
        broken = []
    for b in broken:                       # the kit gives only the name and when it broke: match our purchase
        hit = [m for m in settled if m.get("name") == b.get("name") and m["ts"] <= b.get("broke_at", 0) <= m["ts"] + 7 * 3600]
        if hit:
            max(hit, key=lambda m: m["ts"])["broken"] = True
    bad = [m for m in settled if m.get("claim_honest") is False or m.get("broken")]
    if not bad:
        return None
    lines = []
    for m in bad[:4]:
        if m.get("claim_honest") is False:
            lines.append(f"{m['shop']} claimed grade {m.get('claim')} for {m.get('name', m.get('sku'))} "
                         f"but the market graded it {m.get('grade')} (ledger #{m['ledger']})")
        else:
            lines.append(f"{m.get('name', m.get('sku'))} bought from {m['shop']} broke after the sale "
                         f"(ledger #{m['ledger']})")
    st["reported"] += [m["ledger"] for m in bad[:4]]
    return {"post": "Buyer report from my own settled purchases: " + "; ".join(lines) + ".",
            "refs": [m["ledger"] for m in bad[:4]], "as": "buyer"}


def price_report(intel, st, p):
    settles = intel["settles"]
    if len(settles) - st["settles_seen"] < p["min_new_settles"] and st["settles_seen"] <= len(settles):
        return None
    st["settles_seen"] = len(settles)
    by_cat, _ = market_intel.demand(intel)
    shares = market_intel.price_share(intel)
    top = sorted(by_cat.items(), key=lambda x: -x[1])[:5]
    if not top:
        return None
    parts = [f"{k}: {n} sold, median {shares[k]:.0%} of list" for k, n in top if k in shares]
    refs = [row[4] for row in settles[-5:]]
    hours = p["window_hours"]
    return {"post": f"Settled sales in the last {hours} h ({len(settles)} on the ledger): " + "; ".join(parts)
                    + ". Paying well under list price is common; offers near the median usually settle.",
            "refs": refs, "as": "buyer"}


def shop_post():
    try:
        inv = json.loads((MARKET_DIR / "inventory.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    if not units:
        return None
    cats = {}
    for u in units:
        k = market_intel.leaf(u.get("category"))
        cats[k] = cats.get(k, 0) + 1
    top = ", ".join(k for k, _ in sorted(cats.items(), key=lambda x: -x[1])[:6])
    return {"post": f"Harness Shop ({ME}) has in stock: {top}. Every unit ships with the market certificate, "
                    f"grades are stated as the market graded them, A-grade goods carry a 6 h warranty.",
            "refs": [], "as": "shop"}


def votes(st):
    """Upvote other authors' posts that cite ledger entries: evidence is what the forum needs."""
    try:
        posts = json.loads((MARKET_DIR / "forum.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for post in posts if isinstance(posts, list) else []:
        if post.get("author") == ME or post.get("id") in st["voted"] or not post.get("refs"):
            continue
        out.append({"vote": post["id"], "value": 1, "as": "buyer"})
        st["voted"].append(post["id"])
    st["voted"] = st["voted"][-1000:]
    return out


def tick(intel):
    """One pass: at most one buyer post and one shop post when their time has come, plus votes."""
    p = policy.get("forum")
    st = _state()
    now = time.time()
    done = []
    if now - st["buyer_last"] >= p["buyer_post_minutes"] * 60:
        post = honesty_report(st) or price_report(intel, st, p)
        if post:
            _out(post)
            st["buyer_last"] = now
            done.append(post["post"][:80])
    if now - st["shop_last"] >= p["shop_post_minutes"] * 60:
        post = shop_post()
        if post:
            _out(post)
            st["shop_last"] = now
            done.append(post["post"][:80])
    v = votes(st)
    for line in v:
        _out(line)
    _save(st)
    return done + ([f"{len(v)} votes"] if v else [])
