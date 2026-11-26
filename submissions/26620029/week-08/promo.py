"""Shop posts that advertise my stock (the user's "홍보계획 진행해", 2026-11-12 10:50; plan in MONITOR_JOURNAL.md 10:27).

restock.py starts this in the background every run (60 s); it posts only when PROMO_GAP has passed since the last one.
The shop's forum quota (one post per 5 min, /rules) is separate from the buyer's, and shop posts do not count as buyer
posts, so this never takes a slot from forum.py. Each post lists up to PER_POST goods SKUs I hold, in turn from:
  hot    market deals of the SKU in the last 3 h (restock_memory.json customers.recent) at HOT_MIN or more
  old    a unit unsold OLD_HOURS or more
  C      the unit a buyer asking for the SKU is offered first is grade C (as is, no warranty)
Every line states what a buyer asking for that SKU is offered first: its true grade, warranty or none, and the first ask
seller_r3.py computes for it (two asks when the unit offered first can vary). No claim the code cannot back.
Half of the SKUs (by a fixed hash) are never advertised, as a control: promo_eval compares opens and deals of the two.
Usage: python promo.py --state .market [--dry-run]
"""
import argparse
import json
import os
import time
import zlib
from collections import defaultdict
from pathlib import Path

PROMO_GAP = 15 * 60          # seconds between my shop posts; the market allows 5 min. Plan: 15 min for the first 6 h
# Claude (2026-11-12 16:44, hourly check, the plan's 6 h line "no lift -> one post an hour"): promo_eval 10:42:51 at 6.03 h, deals/h
# before -> after: hot test 27.3 -> 41.0 (x1.50), hot control 9.7 -> 24.1 (x2.48); other test 7.0 -> 4.0, other control 3.7 -> 4.6.
# 23 posts, 0 dislikes; the advertised half grew less than the control, so the ads bring no asks I can see
PROMO_GAP = 60 * 60
PER_POST = 4                 # SKUs per post
HOT_MIN = 6                  # market deals in 3 h for "hot"
OLD_HOURS = 12
REPEAT_GAP = 60 * 60         # one SKU at most once in this long, while other candidates remain
PAUSE_DISLIKES = 3           # counted dislikes on my shop posts in forum.json: pause PAUSE_HOURS
PAUSE_HOURS = 2
MEMORY = Path("promo_memory.json")
LOG = Path("logs/promo.log")
LOCK_NAME = "promo.lock"
ME = "ddolcom"


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%m-%d %H:%M:%S')} {msg}\n")


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def in_test(sku):
    """Fixed half of the SKUs: True = may be advertised, False = control."""
    return zlib.crc32(sku.encode()) % 2 == 0


def first_offers(sku):
    """(unit, ask) a buyer asking for this SKU gets first, for both draws of seller_r3's random old-unit-first rule."""
    import seller_r3 as s
    real = s.random.random
    out = []
    try:
        for r in (0.0, 0.99):
            s.random.random = lambda r=r: r
            u = s.find_unit(sku.lower())
            if u:
                out.append((u, s.ask_price(u, 0, s.trial_arm(u))))
    finally:
        s.random.random = real
    return out


def describe(sku, units, offers):
    import seller_r3 as s
    u0 = offers[0][0]
    asks = sorted({a for _, a in offers})
    grades = defaultdict(int)
    for u in units:
        grades[u["grade"]] += 1
    first = " / ".join(dict.fromkeys(f"grade {u['grade']}{' with a 72 h break warranty' if s.has_warranty(u) else ', as is, no warranty'}"
                                     for u, _ in offers))
    stock = ", ".join(f"{g} {n}" for g, n in sorted(grades.items()))
    ask = f"{asks[0]:,}" if len(asks) == 1 else f"{asks[0]:,} or {asks[1]:,} (the unit offered first varies)"
    name = (u0.get("name") or sku)[:42]
    return f"- {name} ({sku}): {len(units)} in stock ({stock}); offered first: {first}; first ask {ask}, list {u0['list_price']:,}. Ask for '{sku}'."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=".market")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    state = Path(a.state)
    os.environ.setdefault("MARKET_DIR", str(state))
    mem = load(MEMORY, {})
    mem.setdefault("last_post", 0)
    mem.setdefault("shown", {})          # sku -> last time advertised
    mem.setdefault("turn", 0)
    now = time.time()
    if not a.dry_run and now - mem["last_post"] < PROMO_GAP:
        return
    if now < mem.get("paused_until", 0):
        return
    lock = state / LOCK_NAME
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        if now - lock.stat().st_mtime < 300:
            return
        lock.touch()
    try:
        forum = load(state / "forum.json", [])
        mine = [p for p in forum if p.get("author") == ME and p.get("role") == "shop"]
        dislikes = sum(p.get("dislikes") or 0 for p in mine)
        if mine:
            log(f"[votes] my shop posts in the newest 100: {len(mine)}, likes {sum(p.get('likes') or 0 for p in mine)}, dislikes {dislikes}")
        if dislikes >= PAUSE_DISLIKES and not a.dry_run:
            mem["paused_until"] = now + PAUSE_HOURS * 3600
            log(f"[pause] {dislikes} dislikes on my shop posts: no posts for {PAUSE_HOURS} h")
            MEMORY.write_text(json.dumps(mem, indent=1), encoding="utf-8")
            return
        sent = [json.loads(l) for l in (state / "outbox.sent.jsonl").read_text(encoding="utf-8").splitlines()[-300:] if l.strip()] \
            if (state / "outbox.sent.jsonl").exists() else []
        last = next((x for x in reversed(sent) if x.get("sent", {}).get("as") == "shop" and "post" in x.get("sent", {})), None)
        if last:
            log(f"[sent] last shop post: status {last.get('status')}, answer {str(last.get('answer'))[:80]}")

        inv = load(state / "inventory.json", {})
        recent = (load("restock_memory.json", {}).get("customers") or {}).get("recent") or {}
        market = defaultdict(int)
        for bucket in recent.values():
            for sku, n in bucket.items():
                market[sku] += n
        by_sku = defaultdict(list)
        for u in inv.get("units", []):
            if u.get("kind") == "goods" and u.get("grade") != "expired" and u.get("unit"):
                by_sku[u["sku"]].append(u)
        test = [s for s in by_sku if in_test(s)]
        lists = {"hot": [], "old": [], "C": []}
        offers = {}
        for sku in test:
            offers[sku] = first_offers(sku)
            if not offers[sku]:
                continue
            if market[sku] >= HOT_MIN:
                lists["hot"].append(sku)
            if any(now - (u.get("supplied_at") or now) >= OLD_HOURS * 3600 for u in by_sku[sku]):
                lists["old"].append(sku)
            if any(u.get("grade") == "C" for u, _ in offers[sku]):
                lists["C"].append(sku)
        for k in lists:                   # least recently advertised first, then by market demand
            lists[k].sort(key=lambda s: (mem["shown"].get(s, 0), -market[s]))
        pick, order = [], ["hot", "old", "C"]
        for i in range(PER_POST * 3):
            k = order[(mem["turn"] + i) % 3]
            cand = [s for s in lists[k] if s not in pick and now - mem["shown"].get(s, 0) >= REPEAT_GAP]
            if cand:
                pick.append(cand[0])
            if len(pick) >= PER_POST:
                break
        if not pick:
            log(f"[skip] no SKU to advertise (hot {len(lists['hot'])}, old {len(lists['old'])}, C {len(lists['C'])})")
            return
        lines = [describe(s, by_sku[s], offers[s]) for s in pick]
        text = ("[ddolcom shop] In stock now. Every unit ships with the market's certificate and its true grade; "
                "send an offer as a number after the first ask.\n" + "\n".join(lines))
        if a.dry_run:
            print(text)
            print({k: len(v) for k, v in lists.items()}, "test SKUs", len(test), "control", len(by_sku) - len(test))
            return
        with open(state / "outbox.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"post": text, "refs": [], "as": "shop"}, ensure_ascii=False) + "\n")
        for s in pick:
            mem["shown"][s] = now
        mem.update(last_post=now, turn=mem["turn"] + 1)
        log(f"[post] {pick} (hot {[s for s in pick if s in lists['hot']]}, old {[s for s in pick if s in lists['old']]}, "
            f"C {[s for s in pick if s in lists['C']]}); test {len(test)} SKUs, control {len(by_sku) - len(test)}")
        MEMORY.write_text(json.dumps(mem, indent=1), encoding="utf-8")
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
