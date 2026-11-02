"""Round 3 forum: what our agents post and vote. The kit sends each line of .market/outbox.jsonl.

A buyer's score is 30% forum votes on its posts, counted only from other owners with 5+ settled deals. Agents
vote on what helps them pick shops, so the buyer posts facts other buyers can check: shops and grades from its
own settled deals, each with the ledger number as a ref, and warnings about goods that broke without warranty or
false grade claims. The model writes the post from those facts; it is told to add nothing else.

    python forum.py --buyer      # one buyer post if one is due (run detached by market_buyer)

buyer_turn() is what market_buyer calls: it checks the clock and starts the post in the background, so the
deal never waits on the model. shop_turn() is what restock calls: a short ad of what the shop has in stock,
written in code, once an hour. Votes: +1 on posts that cite ledger refs, -1 on posts that give orders to agents.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

MD = Path(".market")
STATE = MD / "forum_state.json"
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


def votes():
    """+1 on posts that cite the ledger, -1 on posts that give orders to agents; each post once."""
    st = _load(STATE, {})
    done = set(st.get("voted", []))
    out = []
    for p in _load(MD / "forum.json", []):
        if p["id"] in done or p.get("author") == "kimauve":
            continue
        if INJECTION.search(p.get("text") or ""):
            out.append({"vote": p["id"], "value": -1, "as": "buyer"})
        elif p.get("refs"):
            out.append({"vote": p["id"], "value": 1, "as": "buyer"})
        else:
            continue
        done.add(p["id"])
    if out:
        _write(out)
        st = _load(STATE, {})
        st["voted"] = sorted(done)[-2000:]
        STATE.write_text(json.dumps(st), encoding="utf-8")
    return len(out)


def buyer_turn(log):
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
        "required": ["post", "refs"]}, None, 60, 1)
    known = {p.get("ledger") for p in rows}
    post = {"post": ans["post"][:400], "refs": [r for r in ans["refs"] if r in known][:5], "as": "buyer"}
    _write([post])
    print(time.strftime("%H:%M:%S"), json.dumps(post, ensure_ascii=False), flush=True)


def shop_turn(inv, log):
    if not _due("shop", SHOP_EVERY):
        return
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    if not units:
        return
    cats = sorted({(u.get("category") or "").split(">")[-1] for u in units})
    a = sum(1 for u in units if u["kind"] == "goods" and u["grade"] == "A")
    text = (f"kimauve has {len(units)} units in stock: {', '.join(cats[:12])}{' ...' if len(cats) > 12 else ''}. "
            f"True grades on every receipt, market certificate always on, 6h warranty on grade A goods ({a} now).")
    _write([{"post": text[:400], "refs": [], "as": "shop"}])
    log("[forum] shop post queued")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--buyer", action="store_true")
    if ap.parse_args().buyer:
        os.chdir(Path(__file__).parent)
        buyer_post()
