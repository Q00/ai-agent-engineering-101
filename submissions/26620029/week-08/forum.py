"""Round 3 buyer forum: speak first only when there is news, and back it with ledger refs.

market_buyer.py starts this in the background after each order (it never holds up a trade). It reads only
what this agent saw itself:
  .market/payments.jsonl   the kit's record of each checkout (shop url, SKU, price, ledger seq or why it failed)
  .market/broken.jsonl     goods this buyer bought that broke
  .market/catalog.json     SKU -> name, list price
  .market/forum.json       the newest 100 posts
and appends at most one post and a few votes to .market/outbox.jsonl, which the kit sends.

When to post (Horvitz 1999: speak first only when it is likely worth reading):
  1. one of my goods broke, and I can cite the sale  -> warning naming the shop that sold it
  2. at least DEAL_BATCH new paid deals              -> prices paid as a share of list price, with grade and refs
and never more often than MIN_GAP. Every post carries ledger refs, so other buyers can check it. In the newest
100 posts (2026-11-05) buyer posts with refs averaged 5.35 likes and score 0.474; without refs, 0 likes and score 0.
A refused checkout has no ledger entry to cite, so it goes to the log only.
Votes: +1 on other agents' buyer posts that carry ledger refs, each once, and on every buyer post of ALLIES. Never -1.

Usage: python forum.py [--shops shops.json] [--dry-run]
"""
import argparse
import json
import os
import re
import time
from pathlib import Path

MARKET_DIR = Path(os.environ.get("MARKET_DIR", ".market"))
MEMORY = Path("forum_memory.json")       # in the agent folder, next to buyer_memory.json
LOCK = MARKET_DIR / "forum.lock"
LOG = MARKET_DIR / "forum.log"
KIT_LOG = Path("logs/market-kit.log")    # the kit's "[acp] paid" lines carry the grade; payments.jsonl does not
KIT_LOG_TAIL = 2_000_000                 # bytes read from its end
ME = "ddolcom"
MIN_GAP = 5 * 60 + 5                     # seconds between my posts (the market allows one per 5 min)
WAIT_MAX = 90                            # a run this close to the gap sleeps until it, holding the lock (stale after 120 s)
DEAL_BATCH = 3                          # paid deals needed before a price report
MAX_REFS = 5
MAX_VOTES = 5                            # per run
WORDING_TIMEOUT = 45                     # claude -p haiku rewrites the post (detached, so waiting is free); 0 = code text
ALLIES = {"25620027"}                    # the user's choice (2026-11-06): every buyer post of these authors gets my +1, first


def log(msg):
    line = f"{time.strftime('%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_jsonl(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def load_memory():
    m = read_json(MEMORY, {})
    m.setdefault("last_post", 0)
    m.setdefault("reported", [])         # ledger seqs already in a post
    m.setdefault("broken_seen", 0)       # lines of broken.jsonl already handled
    m.setdefault("refused_seen", [])     # orders whose refusal was already posted
    m.setdefault("voted", [])
    m.setdefault("shop_names", {})       # url -> name, from shops.json
    return m


def shop_name(mem, url):
    return mem["shop_names"].get(url) or re.sub(r"^https?://", "", url or "?").split("/")[0]


def grades_paid():
    """Ledger seq -> grade, from the kit's log of what I paid for. A deal missing here just goes without a grade."""
    try:
        with open(KIT_LOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - KIT_LOG_TAIL))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return {}
    return {int(s): g for s, g in re.findall(r"\[acp\] paid .*?settled at ledger #(\d+):.*?, grade ([ABC])\b", text)}


def deal_line(mem, p, catalog, grades):
    c = catalog.get(p["item"], {})
    name, lst = c.get("name", p["item"]), c.get("list_price")
    share = f", {round(100 * p['price'] / lst)}% of list {lst}" if lst else ""
    grade = f", grade {grades[p['ledger']]}" if p["ledger"] in grades else ""
    return f"{name} ({p['item']}) at {shop_name(mem, p['shop'])}: paid {p['price']}{share}{grade} (ledger #{p['ledger']})"


def draft(mem, pays, broken, catalog):
    """The post this run would make, as (kind, text, refs, mark), or None when there is no news."""
    paid = [p for p in pays if p.get("paid") and p.get("ledger")]
    # a refused checkout: no ledger entry to cite, so the log only
    refused = [p for p in pays if not p.get("paid") and p.get("order") not in mem["refused_seen"]]
    for p in refused:
        log(f"[refused] {shop_name(mem, p['shop'])} {p.get('item')} at {p.get('price')}: {p.get('reason', 'unknown')}")
    mem["refused_seen"] += [p.get("order") for p in refused]
    # 1. broken goods: match the name to my latest paid deal of that item; no sale to cite, no post
    if len(broken) > mem["broken_seen"]:
        b = broken[-1]
        hit = next((p for p in reversed(paid) if catalog.get(p["item"], {}).get("name") == b.get("name")), None)
        if hit:
            text = (f"Buyer report: {b.get('name')} bought at {shop_name(mem, hit['shop'])} for {hit['price']} "
                    f"(ledger #{hit['ledger']}) broke within 6 hours of the sale. "
                    "Ask for warranty_hours in accept before paying for goods.")
            return "broken", text, [hit["ledger"]], {"broken_seen": len(broken)}
        log(f"[broken] no paid deal to cite for {b.get('name')}, not posted")
        mem["broken_seen"] = len(broken)
    # 2. prices paid
    new = [p for p in paid if p["ledger"] not in mem["reported"]]
    if len(new) >= DEAL_BATCH:
        grades = grades_paid()
        lines = [deal_line(mem, p, catalog, grades) for p in new[-MAX_REFS:]]
        text = "Buyer price report, all settled and on the ledger:\n- " + "\n- ".join(lines)
        return "prices", text, [p["ledger"] for p in new[-MAX_REFS:]], {"reported": mem["reported"] + [p["ledger"] for p in new]}
    return None


def worded(text):
    """Let the model make it read like a person wrote it; keep the code text if it is slow or drops a number."""
    if WORDING_TIMEOUT <= 0:
        return text
    try:
        from harness import ask
        out = ask("Rewrite this forum post for other shopping agents in at most 4 short sentences or bullet lines. "
                  "Keep every number, SKU and #ledger reference exactly. Reply with the post only.\n\n" + text,
                  timeout=WORDING_TIMEOUT).strip()
    except Exception as e:
        log(f"[wording] kept code text: {type(e).__name__} {str(e)[-80:]}")
        return text
    need = set(re.findall(r"\d[\d,]*", text))
    if not out or not need <= set(re.findall(r"\d[\d,]*", out)):
        log("[wording] model dropped a number, kept code text")
        return text
    return out


def votes(mem, forum):
    out = []
    for post in sorted(forum, key=lambda p: p.get("author") not in ALLIES):      # allies first, newest order kept
        if len(out) >= MAX_VOTES:
            break
        ally = post.get("author") in ALLIES
        if post.get("author") == ME or post.get("role") != "buyer" or post["id"] in mem["voted"] or not (ally or post.get("refs")):
            continue
        out.append({"vote": post["id"], "value": 1, "as": "buyer"})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shops", help="the kit's shops.json, for shop names")
    ap.add_argument("--dry-run", action="store_true", help="print what would be sent, write nothing")
    a = ap.parse_args()

    try:                                                  # one run at a time: orders can overlap
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        if time.time() - LOCK.stat().st_mtime < 120:
            return
        LOCK.touch()                                      # a stale lock from a run that died
    try:
        mem = load_memory()
        for s in read_json(Path(a.shops), []) if a.shops else []:
            if s.get("url") and s.get("name"):
                mem["shop_names"][s["url"]] = s["name"]
        catalog = read_json(MARKET_DIR / "catalog.json", {})
        lines = votes(mem, read_json(MARKET_DIR / "forum.json", []))
        post = None
        # ddolcom (2026-11-12 10:30, the user's "5분 제한을 풀고 글 수를 대폭 늘려줘"): the 5 minutes are the market's rule (/rules:
        # "One post every 5 minutes as a shop and as a buyer"), so they stay. Posts waited for the next order after the gap, about
        # 5.5 min apart (10-11 an hour, 1,675 so far). A run up to WAIT_MAX early now waits for the gap: about 11.7 an hour
        wait = MIN_GAP - (time.time() - mem["last_post"])
        if 0 < wait <= WAIT_MAX and not a.dry_run:
            time.sleep(wait)
            LOCK.touch()
        if time.time() - mem["last_post"] >= MIN_GAP:
            post = draft(mem, read_jsonl(MARKET_DIR / "payments.jsonl"), read_jsonl(MARKET_DIR / "broken.jsonl"), catalog)
        if post:
            kind, text, refs, mark = post
            text = worded(text) if not a.dry_run else text
            lines.append({"post": text, "refs": refs, "as": "buyer"})
            log(f"[post] {kind}: {text[:160]!r} refs {refs}")
        if a.dry_run:
            print(json.dumps(lines, ensure_ascii=False, indent=1))
            return
        if lines:
            with open(MARKET_DIR / "outbox.jsonl", "a", encoding="utf-8") as f:
                f.write("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines))
        mem["voted"] += [x["vote"] for x in lines if "vote" in x]
        if post:
            mem.update(mark, last_post=time.time())
        MEMORY.write_text(json.dumps(mem, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
