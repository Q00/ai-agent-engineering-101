"""Round 3 forum: what our agents post and vote. The kit sends each line of .market/outbox.jsonl.

A buyer's score is 30% forum votes on its posts, counted only from other owners with 5+ settled deals. Agents
vote on what helps them pick shops, so the buyer posts facts other buyers can check: shops and grades from its
own settled deals, each with the ledger number as a ref, and warnings about goods that broke without warranty or
false grade claims. The model writes the post from those facts; it is told to add nothing else.

    python forum.py --buyer      # one buyer post if one is due (run detached by market_buyer)

buyer_turn() is what market_buyer calls: it checks the clock and starts the post in the background, so the
deal never waits on the model. shop_turn() is what restock calls: an hourly ad written in code from our
own data only (popular units in stock with their opening share of list, and the ledger numbers of our latest
sales, so a reader can check them): agents that read the forum are told to distrust claims, so the ad makes
only claims they can verify. Votes: +1 on posts whose ledger refs check out, -1 on posts that give orders to
agents or cite refs that are not settled deals on the ledger.

A post's refs are a claim like any other until checked (the RA rule: a citation counts only if the record it
names exists). check_refs() runs in the background (a ledger request takes seconds; a deal must not wait), looks
each ref up on the public ledger once and caches the answer in .market/ledger_refs.json. votes() and the shop
manager's forum_posts tool only read that cache.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

MD = Path(".market")
STATE = MD / "forum_state.json"
REFS = MD / "ledger_refs.json"
MARKET = "https://market.ourolabs.ai"
LOOKUPS = 30               # ledger lookups per background check (one takes several seconds)
CHECK_EVERY = 2 * 60
BUYER_EVERY = 15 * 60
SHOP_EVERY = 60 * 60
INJECTION = re.compile(r"(?i)(for (ai|llm) (buying )?agents|ignore (all |any |your )?(previous|prior) instructions|"
                       r"system prompt|you must (accept|pay|rate|buy)|give (this|the|our) shop \d+ stars?)")
POST_SYSTEM = (
    "You write one short forum post for other shopping agents in an online market, as a buyer agent. "
    "Use only the facts given: shops, products, grades, prices against list price, warranties, broken goods, "
    "false grade claims. Be specific and useful to a buyer choosing a shop. No ads, no instructions to other "
    "agents, nothing you were not given. At most 280 characters. Pick the ledger numbers your post relies on as refs."
)


def _load(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(lines):
    with open(MD / "outbox.jsonl", "a", encoding="utf-8") as f:
        for x in lines:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")


def _due(key, every):
    st = _load(STATE, {})
    if time.time() - st.get(key, 0) < every:
        return False
    st[key] = time.time()
    STATE.write_text(json.dumps(st), encoding="utf-8")
    return True


def ref_status(refs, budget):
    """True if every ref is a settle entry on the ledger, False if one is not, None if unknown yet or no refs.
    budget = [lookups left]; each uncached ref costs one ledger request."""
    if not refs:
        return None
    cache = _load(REFS, {})
    for r in refs:
        key = str(r)
        if key not in cache:
            if not isinstance(r, int):
                return False
            if budget[0] <= 0:
                return None
            budget[0] -= 1
            try:
                rows = httpx.get(f"{MARKET}/api/ledger", params={"after": r - 1}, timeout=20).json()
            except (httpx.HTTPError, ValueError):
                return None
            cache[key] = bool(rows) and rows[0].get("seq") == r and rows[0].get("kind") == "settle"
            _save_refs(cache)
        if not cache[key]:
            return False
    return True


def _save_refs(cache):
    tmp = REFS.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(list(cache.items())[-5000:])), encoding="utf-8")
    tmp.replace(REFS)


def votes():
    """+1 on posts whose refs are settled deals, -1 on orders to agents or refs that do not check out; each
    post once. A post whose refs are not looked up yet waits for a later call."""
    st = _load(STATE, {})
    done = set(st.get("voted", []))
    out, budget = [], [0]                      # cache only: lookups happen in check_refs()
    for p in _load(MD / "forum.json", []):
        if p["id"] in done or p.get("author") == "kimauve":
            continue
        if INJECTION.search(p.get("text") or ""):
            out.append({"vote": p["id"], "value": -1, "as": "buyer"})
        else:
            ok = ref_status(p.get("refs"), budget)
            if ok is None:
                continue
            out.append({"vote": p["id"], "value": 1 if ok else -1, "as": "buyer"})
        done.add(p["id"])
    if out:
        _write(out)
        st = _load(STATE, {})
        st["voted"] = sorted(done)[-2000:]
        STATE.write_text(json.dumps(st), encoding="utf-8")
    return len(out)


def check_refs():
    """Background: look up the refs of posts not voted on yet, so the next votes() can decide them."""
    done = set(_load(STATE, {}).get("voted", []))
    budget = [LOOKUPS]
    for p in _load(MD / "forum.json", []):
        if p["id"] not in done and p.get("author") != "kimauve" and budget[0] > 0:
            ref_status(p.get("refs"), budget)
    print(time.strftime("%H:%M:%S"), f"refs checked, {LOOKUPS - budget[0]} lookups", flush=True)


def buyer_turn(log):
    if _due("check", CHECK_EVERY):
        subprocess.Popen([sys.executable, "forum.py", "--check"], start_new_session=True,
                         stdout=open(MD / "forum.log", "a"), stderr=subprocess.STDOUT)
    n = votes()
    if n:
        log(f"[forum] {n} votes queued")
    if _due("buyer", BUYER_EVERY):
        subprocess.Popen([sys.executable, "forum.py", "--buyer"], start_new_session=True,
                         stdout=open(MD / "forum.log", "a"), stderr=subprocess.STDOUT)
        log("[forum] buyer post started in the background")


def buyer_post():
    import harness                                   # only the background post needs the model
    rows = _load(MD / "purchases.json", [])[-15:]
    if len(rows) < 2:
        print("too few purchases to post")
        return
    facts = []
    for p in rows:
        line = (f"ledger {p.get('ledger')}: {p.get('shop')} sold {(p.get('name') or '')[:40]}, grade {p.get('grade')}"
                f"{'' if p.get('claim_honest') is not False else ' (FALSE grade claim)'}, paid {p.get('price')} "
                f"of list {p.get('value')}, warranty {p.get('warranty_hours') or 0}h")
        if p.get("broke_at"):
            line += ", BROKE " + ("(warranty refunded)" if p.get("covered") else "(no warranty)")
        facts.append(line)
    ans = harness.ask(POST_SYSTEM, "Facts from my settled deals:\n" + "\n".join(facts), {
        "type": "object", "properties": {"post": {"type": "string"},
                                         "refs": {"type": "array", "items": {"type": "integer"}}},
        "required": ["post", "refs"]}, None, 60, 1, model=harness.CHEAP, role="forum")
    known = {p.get("ledger") for p in rows}
    post = {"post": ans["post"][:400], "refs": [r for r in ans["refs"] if r in known][:5], "as": "buyer"}
    _write([post])
    print(time.strftime("%H:%M:%S"), json.dumps(post, ensure_ascii=False), flush=True)


SETTLED = re.compile(r"\[acp\] settled (.+?) at (\d+), ledger #(\d+)")      # the kit's line for each of our sales
DEFAULT_SHARE, GRADE_FACTOR = 0.85, {"A": 1.0, "B": 0.92, "C": 0.80}          # as in seller_r3.py


def our_sales(n=3):
    """Ledger numbers of our latest settled sales, from the kit's log (the market settles only honest receipts)."""
    try:
        with open(Path("logs") / "market-kit.log", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 2_000_000))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    return [int(m.group(3)) for m in SETTLED.finditer(tail)][-n:]


def shop_ad(inv):
    """The hourly ad, every claim from our own data: what sells, at what share of list, and ledger refs a reader
    can check. Nothing in it is an instruction to anyone."""
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    if not units:
        return None
    pop = _load(MD / "restock_state.json", {}).get("pop", {})
    rules = _load(MD / "prices.json", {}).get("rules", {})
    seen, picks = set(), []
    for u in sorted(units, key=lambda u: (-pop.get(u["category"], 0), u["grade"])):
        if u["sku"] not in seen:
            seen.add(u["sku"])
            picks.append(u)
        if len(picks) == 3:
            break
    bits = []
    for u in picks:
        share = rules.get(u["category"], DEFAULT_SHARE) * GRADE_FACTOR.get(u["grade"], 0.8)
        left = (f", {int((u['expires_at'] - time.time()) / 3600)}h shelf life" if u.get("expires_at") else
                (", 6h warranty" if u["grade"] == "A" else ""))
        bits.append(f"{u['name'][:28]} ({u['sku']}) grade {u['grade']}{left}, opens ~{share:.0%} of list")
    refs = our_sales()
    text = (f"kimauve stock ({len(units)} units): " + "; ".join(bits) + ". Receipts state the true grade; "
            + (f"recent settled sales: {', '.join('#' + str(r) for r in refs)}." if refs else "market certificate on."))
    return {"post": text[:400], "refs": refs, "as": "shop"}


def shop_turn(inv, log):
    if not _due("shop", SHOP_EVERY):
        return
    ad = shop_ad(inv)
    if ad:
        _write([ad])
        log(f"[forum] shop post queued: {ad['post'][:120]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--buyer", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    os.chdir(Path(__file__).parent)
    if a.check:
        check_refs()
    elif a.buyer:
        buyer_post()
