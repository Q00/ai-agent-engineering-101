"""Peer benchmark for the buyer (11-12, user: 'when there is something like this, check it and apply it every time
you review the buyer').

On 11-12 SungJinho's buyer (25620027) closed the class total on us by ~.02 per 12 h with .888 per deal against our
.839 at the same volume and at the same shops (psyoung82 goods .935 at .62 of list vs ours .79 at .69), and the
tactic (first number ~.45 of list, take the counter) was visible in our own shop's logs. This file makes that
comparison every tick so the buyer strategist sees it each run:

    .market/peer_bench.json
      ours          our per-deal v over WINDOW_H (ledger settles, unit costs, breaks scored 0 / -1), deals/h
      peers         every student buyer with >= MIN_DEALS: v, deals/h, food share, share at or under cost
      better        the peers whose v beats ours by >= GAP, each with the same-shop|kind comparison (their v and
                    price/list vs ours, n >= 5 both), their goods median list price, and how they open at OUR
                    shop when our seller logged it (first number / list, numbers per talk)

    python peer_bench.py [--print]       (run by watch_round.sh every tick)
"""
import json
import sqlite3
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARCHIVE = HERE / "data" / "market_archive.db"
OUT = HERE / ".market" / "peer_bench.json"
WINDOW_H = 6
MIN_DEALS = 100
GAP = 0.02
ME = "yms979"


def build():
    import cost_model
    now = time.time()
    db = sqlite3.connect(f"file:{ARCHIVE}?mode=ro", uri=True, timeout=30)
    cat = json.loads((HERE / ".market" / "catalog.json").read_text(encoding="utf-8"))
    names, ours_urls = {}, set()
    for (d,) in db.execute("SELECT data FROM ledger WHERE kind='join'"):
        x = json.loads(d).get("data") or {}
        if x.get("url") and x.get("name"):
            names[x["url"].rstrip("/")] = x["name"]
            if x["name"] == ME:
                ours_urls.add(x["url"].rstrip("/"))
    import board_api
    b = board_api.fetch(timeout=20)
    students = {(p.get("buyer") or {}).get("name"): p["player"] for p in b["players"] if p.get("student")}
    broken = {}
    for (d,) in db.execute("SELECT data FROM ledger WHERE kind='broken' AND ts > ?", (now - (WINDOW_H + 8) * 3600,)):
        x = json.loads(d).get("data") or {}
        broken[x.get("unit")] = (x.get("refunded") or 0) > 0
    rows = [json.loads(d).get("data") or {} for (d,) in db.execute(
        "SELECT data FROM ledger WHERE kind='settle' AND ts > ?", (now - WINDOW_H * 3600,))]
    rows = [r for r in rows if r.get("buyer") in students and r.get("sku") in cat]
    uc = cost_model.unit_costs([r["unit"] for r in rows])
    per = defaultdict(list)
    for r in rows:
        lp, c = cat[r["sku"]]["list_price"], uc.get(r["unit"])
        if c is None:
            continue
        if r["unit"] in broken:
            v = 0.0 if broken[r["unit"]] else -1.0
        else:
            v = 1.0 if lp <= c else min(1.0, (lp - r["amount"]) / (lp - c))
        per[r["buyer"]].append({"shop": names.get(str(r.get("shop")).rstrip("/"), "?"),
                                "kind": "goods" if r.get("expires_at") is None else "food",
                                "v": v, "x": r["amount"] / lp, "under": r["amount"] <= c, "lp": lp,
                                "task": (r.get("task") or None)})

    def summ(L):
        n = len(L)
        return {"deals_h": round(n / WINDOW_H, 1), "v": round(sum(z["v"] for z in L) / n, 3),
                "food_share": round(sum(z["kind"] == "food" for z in L) / n, 2),
                "at_or_under_cost": round(sum(z["under"] for z in L) / n, 2),
                "goods_median_list": int(statistics.median([z["lp"] for z in L if z["kind"] == "goods"] or [0]))}
    out = {"t": now, "window_h": WINDOW_H, "ours": summ(per[ME]) if per.get(ME) else None, "peers": {}, "better": []}
    for name, L in per.items():
        if len(L) >= MIN_DEALS and name != ME:
            out["peers"][name] = dict(summ(L), player=students.get(name))
    if not out["ours"]:
        return out
    mine = defaultdict(list)
    for z in per[ME]:
        mine[(z["shop"], z["kind"])].append(z)
    openers = their_openers(db, now)
    for name, s in sorted(out["peers"].items(), key=lambda kv: -kv[1]["v"]):
        if s["v"] < out["ours"]["v"] + GAP:
            continue
        theirs = defaultdict(list)
        for z in per[name]:
            theirs[(z["shop"], z["kind"])].append(z)
        same = []
        for k, L in theirs.items():
            M = mine.get(k) or []
            if len(L) >= 5 and len(M) >= 5:
                same.append({"shop": k[0], "kind": k[1], "their_v": round(sum(z["v"] for z in L) / len(L), 3),
                             "their_x": round(statistics.median([z["x"] for z in L]), 3), "their_n": len(L),
                             "our_v": round(sum(z["v"] for z in M) / len(M), 3),
                             "our_x": round(statistics.median([z["x"] for z in M]), 3), "our_n": len(M)})
        same.sort(key=lambda e: -(e["their_v"] - e["our_v"]) * min(e["their_n"], e["our_n"]))
        out["better"].append(dict(s, name=name, v_gap=round(s["v"] - out["ours"]["v"], 3), same_shop=same[:8],
                                  opening_at_our_shop=openers.get(name)))
    return out


def their_openers(db, now):
    """buyer -> how it opened at OUR shop (first number / list, numbers per talk), from our seller's message log
    (every message since 11-12 15:52) joined with the ledger's receipts by task id."""
    tasks = {}
    for (d,) in db.execute("SELECT data FROM ledger WHERE kind='result' AND ts > ?", (now - WINDOW_H * 3600,)):
        x = json.loads(d).get("data") or {}
        rc = x.get("receipt") or {}
        if rc.get("task"):
            tasks[rc["task"]] = (x.get("buyer"), rc.get("item"))
    conv = defaultdict(list)
    try:
        with open(HERE / "seller_r3_log.jsonl", errors="ignore") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 120_000_000))
            f.readline()
            for line in f:
                if '"msg"' not in line:
                    continue
                try:
                    x = json.loads(line)
                except ValueError:
                    continue
                if x.get("task") in tasks:
                    conv[x["task"]].append(x.get("text") or "")
    except OSError:
        return {}
    cat = json.loads((HERE / ".market" / "catalog.json").read_text(encoding="utf-8"))
    by = defaultdict(list)
    for t, msgs in conv.items():
        buyer, item = tasks[t]
        lp = (cat.get(item) or {}).get("list_price")
        nums = []
        for m in msgs:
            s = m.replace(",", "").strip()
            if s.isdigit():
                nums.append(int(s))
        if lp and nums:
            by[buyer].append((nums[0] / lp, len(nums)))
    return {b: {"talks": len(L), "first_number_x": round(statistics.median([a for a, _ in L]), 3),
                "numbers_per_talk": round(statistics.mean([n for _, n in L]), 1)} for b, L in by.items() if len(L) >= 3}


def main():
    out = build()
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    if "--print" in sys.argv:
        print(json.dumps({k: v for k, v in out.items() if k != "peers"}, ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    main()
