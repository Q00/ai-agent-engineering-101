"""MBT reverse (HQ 11-07): what our buyer saw of other shops -- opening ask / list, haggle drop, no-quote rate,
reply features -- for the seller team. Run on the VPS: python mbt_sellers.py (writes /tmp/sellers.json)."""
import re, json, collections, statistics, sys, time
W = "/home/market/ai-agent-engineering-101/submissions/25620024/week-08"
lines = []
for f in ("logs/market-kit.log.1", "logs/market-kit.log"):
    lines += open(f"{W}/{f}", encoding="utf-8", errors="replace").read().splitlines()
CAND = re.compile(r"\[buyer\] \[r3\]   (\S+): .* \((G[0-9a-f]{8})\) list (\d+)")
QUOTE = re.compile(r"\[buyer\] \[quote\] (\S+): ask (\S+)")
CHOOSE = re.compile(r"\[buyer\] \[choose\] (\S+) ")
REPLY = re.compile(r"\[buyer\] <- (\S+) \| (.*)")
lp, opening = {}, collections.defaultdict(list)
quoted, none = collections.Counter(), collections.Counter()
cur_shop, replies = None, collections.defaultdict(list)
for l in lines:
    m = CAND.search(l)
    if m:
        lp[m.group(1)] = int(m.group(3)); continue
    m = QUOTE.search(l)
    if m:
        s, a = m.group(1), m.group(2); quoted[s] += 1
        if a == "None": none[s] += 1
        elif s in lp: opening[s].append(int(a) / lp[s])
        continue
    m = CHOOSE.search(l)
    if m: cur_shop = m.group(1); continue
    m = REPLY.search(l)
    if m and cur_shop: replies[cur_shop].append(m.group(2))
mem = [json.loads(x) for x in open(f"{W}/memory.jsonl") if x.strip()]
since = time.time() - 48 * 3600
deal = collections.defaultdict(lambda: {"n": 0, "deals": 0, "first": [], "agreed": [], "drop": []})
for m in mem:
    if m.get("ts", 0) < since or not m.get("list_price") or not m.get("first_ask"): continue
    d = deal[m["shop"]]; d["n"] += 1
    d["first"].append(m["first_ask"] / m["list_price"])
    if m.get("outcome") == "deal" and m.get("agreed"):
        d["deals"] += 1; d["agreed"].append(m["agreed"] / m["list_price"]); d["drop"].append(1 - m["agreed"] / m["first_ask"])
FEAT = {"json_first": r"^\s*\{", "data_part": r"data part|in the data|certificate", "grade": r"grade [ABC]",
        "shelf": r"shelf|hours? left|_left", "warranty": r"warrant", "vip/member": r"VIP|member|DRG-|code",
        "price_match": r"match", "group": r"group", "counter": r"[Cc]ounter", "korean": r"[가-힣]{6,}",
        "final": r"final|best price|last price|lowest"}
out = {"opening": {s: (len(v), round(statistics.median(v), 3)) for s, v in opening.items() if len(v) >= 10},
       "no_quote": {s: round(none[s] / quoted[s], 2) for s in quoted if quoted[s] >= 20},
       "deals": {s: {"n": d["n"], "rate": round(d["deals"] / d["n"], 2), "first": round(statistics.median(d["first"]), 3),
                     "agreed": round(statistics.median(d["agreed"]), 3) if d["agreed"] else None,
                     "drop": round(statistics.median(d["drop"]), 3) if d["drop"] else None} for s, d in deal.items() if d["n"] >= 8},
       "features": {s: {k: round(sum(bool(re.search(rx, r)) for r in v) / len(v), 2) for k, rx in FEAT.items()}
                    for s, v in replies.items() if len(v) >= 15},
       "samples": {s: v[:3] for s, v in replies.items() if len(v) >= 15}}
json.dump(out, open("/tmp/sellers.json", "w"), ensure_ascii=False)
print(len(lines), len(out["opening"]), len(out["deals"]), len(out["features"]))
