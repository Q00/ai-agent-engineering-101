"""Read-only, no network, no model: numbers behind the improvement candidates, and a pace projection to the end of the round.

Replays use saved records and cannot say what a seller would do with an offer never made. Projections carry each player's pace forward.
Shop text is only searched for price shapes; none of it is printed.
"""
import ctypes
from collections import Counter, defaultdict
from datetime import datetime, timezone
import glob
import json
import os
from pathlib import Path
import re
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import rank_analysis as ra

ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00100000)
SHOP = {"book": .5, "revenue": .3, "stars_rank": .2}
BUYER = ra.BUYER_WEIGHTS
SINCE = "20261104T100000"
paid = {}
for line in (ROOT / ".market/payments.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
    try:
        p = json.loads(line)
    except ValueError:
        continue
    if p.get("paid"):
        paid[p.get("order")] = p

rows = []
for name in sorted(os.listdir(ROOT / "logs/market-buyer")):
    if name >= SINCE:
        try:
            rows.append(json.loads((ROOT / "logs/market-buyer" / name).read_text(encoding="utf-8"))["record"])
        except (OSError, ValueError, KeyError):
            pass
names = sorted(n for n in os.listdir(ROOT / "logs/market-buyer") if n >= SINCE)
first, last = names[0][:15], names[-1][:15]
hours = (datetime.strptime(last, "%Y%m%dT%H%M%S") - datetime.strptime(first, "%Y%m%dT%H%M%S")).total_seconds() / 3600
print(f"records {len(rows)} from {first[:19]} to {last[:19]}, {hours:.1f} h, paid {sum(r.get('order') in paid for r in rows)}")

print("\n== rule counters: outcome by ask/cap, all checks passed")
bins = defaultdict(lambda: [0, 0, 0])
for rec in rows:
    for step in rec.get("decision_trail") or []:
        if isinstance(step, dict) and step.get("reason_code") == "rule_counter_offer":
            check = step.get("counter_check") or {}
            ratio = check.get("ask_per_cap")
            if ratio:
                band = "1.00-1.05" if ratio <= 1.05 else "1.05-1.10" if ratio <= 1.10 else "1.10-1.18"
                bins[band][0] += 1
                bins[band][1] += rec.get("order") in paid
                bins[band][2] += (paid.get(rec.get("order")) or {}).get("price") or 0
            break
for band, (n, k, _) in sorted(bins.items()):
    print(f"  {band}: counters {n} paid {k} rate {k/n:.2f}")

print("\n== last-step stops, per hour, and what is known of the ask")
stops = Counter()
detail = defaultdict(list)
for rec in rows:
    trail = [s for s in rec.get("decision_trail") or [] if isinstance(s, dict)]
    if not trail or trail[-1].get("reason_code") != "model_quota_hour":
        continue
    step = trail[-1]
    check = step.get("counter_check") or {}
    reason = step.get("parser_reason")
    stops[(reason, check.get("reason_code"))] += 1
    detail[(reason, check.get("reason_code"))].append((rec, check))
for key, n in stops.most_common():
    items = detail[key]
    ratios = [c["ask_per_cap"] for _, c in items if c.get("ask_per_cap")]
    surplus = [c["surplus_per_list"] for _, c in items if c.get("surplus_per_list") is not None]
    list_values = [c["list_price"] for _, c in items if c.get("list_price")]
    budget_ok = sum(bool(c.get("ask") and c["ask"] <= (r.get("order_budget") or r.get("budget"))) for r, c in items)
    print(f"  {key}: {n} ({n/hours:.1f}/h) ask/cap median {median(ratios) if ratios else None} surplus/list median {median(surplus) if surplus else None} ask within order budget {budget_ok}")
wide = [(r, c) for items in detail.values() for r, c in items if c.get("ask") and c.get("cap") and c.get("reason_code") == "counter_ratio_exceeded"]
for limit in (1.25, 1.3, 1.4):
    hit = [(r, c) for r, c in wide if c["ask_per_cap"] <= limit and (c.get("surplus_per_list") or 0) >= .10]
    gain = [c["list_price"] - c["ask"] for _, c in hit if c.get("list_price")]
    print(f"  ratio limit {limit}: over limit today but inside {len(hit)} ({len(hit)/hours:.1f}/h), surplus at the full ask, median {median(gain) if gain else None}, sum {sum(gain)}")

print("\n== no_labeled_price: are the replies priced at all? Counts only.")
SHAPES = {"credits_number": re.compile(r"\d[\d,]*\s*(?:credits?|크레딧)", re.I), "price_word_number": re.compile(r"(?:price|quote|cost|가격|호가)\D{0,20}\d", re.I),
          "any_number_3plus": re.compile(r"\d[\d,]{2,}")}
shape_count = Counter()
by_shop = Counter()
fit = Counter()
for rec in rows:
    trail = [s for s in rec.get("decision_trail") or [] if isinstance(s, dict)]
    if not trail or trail[-1].get("parser_reason") != "no_labeled_price" or trail[-1].get("reason_code") != "model_quota_hour":
        continue
    by_shop[rec.get("shop_name")] += 1
    folder = Path(str(rec.get("buyer_log_dir") or "").replace("\\", "/"))
    text = ""
    for path in sorted(glob.glob(str(ROOT / "logs/buyer" / folder.name / "*.response.txt"))):
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    found = [n for n, p in SHAPES.items() if p.search(text)]
    shape_count[tuple(found)] += 1
    number = SHAPES["credits_number"].search(text)
    if number:
        value = int(re.sub(r"\D", "", number.group()))
        fit["within_budget" if value <= (rec.get("order_budget") or rec.get("budget") or 0) else "over_budget"] += 1
print("  shapes", shape_count.most_common(6))
print("  first credits figure against budget", dict(fit))
print("  shops", len(by_shop), "top share", [n for _, n in by_shop.most_common(5)])

print("\n== all last stops per hour since", SINCE)
all_stops = Counter()
for rec in rows:
    trail = [s for s in rec.get("decision_trail") or [] if isinstance(s, dict)]
    all_stops[(trail[-1].get("reason_code") if trail else (rec.get("failure_reason") or rec.get("outcome")))] += 1
for code, n in all_stops.most_common(16):
    print(f"  {n:5} {n/hours:6.1f}/h {str(code)[:70]}")
surplus_paid = []
catalog = json.loads((ROOT / ".market/catalog.json").read_text(encoding="utf-8"))

print("\n== projection to 11-17 23:59 KST: every player keeps its pace from the 11-04 20:49 board to now")
board_files = sorted(glob.glob(str(ROOT / "logs/rank-analysis/board-2026*.json")))
then = json.loads(Path(board_files[0]).read_text(encoding="utf-8"))
now = json.loads(Path(board_files[-1]).read_text(encoding="utf-8"))
t0 = datetime.strptime(Path(board_files[0]).stem[6:], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
t1 = datetime.strptime(Path(board_files[-1]).stem[6:], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
end = datetime(2026, 11, 17, 14, 59, tzinfo=timezone.utc)
span, left = (t1 - t0).total_seconds() / 3600, (end - t1).total_seconds() / 3600
print(f"  pace window {span:.1f} h, left {left:.0f} h. Long extrapolation: for size only.")


def project(group, keys, own_name, multiplier=None):
    past = {r["name"]: r for r in then[group]}
    result = []
    for row in ra.eligible(now, group):
        new = dict(row)
        before = past.get(row["name"], {})
        for key in keys:
            rate = (ra.number(row.get(key)) - ra.number(before.get(key))) / span
            factor = (multiplier or {}).get(key, 1) if row["name"] == own_name else 1
            new[key] = ra.number(row.get(key)) + rate * left * factor
        result.append(new)
    return result


players = sorted(now["players"], key=lambda p: -p["total"])
me = next(p for p in players if ra.login_of(p) == ra.LOGIN)
shop_name, buyer_name = me["shop"]["name"], me["buyer"]["name"]


def totals(shop_mult=None, buyer_mult=None):
    shops = project("shops", ("book", "revenue"), shop_name, shop_mult)
    buyers = project("buyers", ("surplus", "deals", "posts"), buyer_name, buyer_mult)
    s = {r["name"]: ra.computed_all(r, shops, SHOP) for r in shops}
    b = {r["name"]: ra.computed_all(r, buyers, BUYER) for r in buyers}
    out = {p["player"]: s.get((p.get("shop") or {}).get("name"), 0) + b.get((p.get("buyer") or {}).get("name"), 0) for p in now["players"]}
    return out, s.get(shop_name), b.get(buyer_name)


def rank(table):
    mine = table[me["player"]]
    return 1 + sum(v > mine for v in table.values()), round(mine, 3)


base, s, b = totals()
print("  projected at current pace: rank, total", rank(base), "shop", round(s, 3), "buyer", round(b, 3))
values = sorted(base.values(), reverse=True)
print("  projected totals at ranks 3 10 15 20:", [round(values[i - 1], 3) for i in (3, 10, 15, 20)])
for factor in (1.5, 2, 3, 4, 5, 7, 10):
    table, _, bb = totals(buyer_mult={"surplus": factor, "deals": factor})
    print(f"  buyer pace x{factor}: rank {rank(table)} buyer {bb:.3f}")
for factor in (1.5, 2, 3, 5, 10):
    table, ss, _ = totals(shop_mult={"book": factor, "revenue": factor})
    print(f"  shop pace x{factor}: rank {rank(table)} shop {ss:.3f}")
for factor in (2, 3, 5):
    table, ss, bb = totals(shop_mult={"book": factor, "revenue": factor}, buyer_mult={"surplus": factor, "deals": factor})
    print(f"  both pace x{factor}: rank {rank(table)} shop {ss:.3f} buyer {bb:.3f}")
own_past = next(r for r in then["buyers"] if r["name"] == buyer_name)
own_now = next(r for r in now["buyers"] if r["name"] == buyer_name)
print("  our buyer pace per hour", {k: round((ra.number(own_now.get(k)) - ra.number(own_past.get(k))) / span, 1) for k in ("surplus", "deals", "posts", "orders")})
own_past = next(r for r in then["shops"] if r["name"] == shop_name)
own_now = next(r for r in now["shops"] if r["name"] == shop_name)
print("  our shop pace per hour", {k: round((ra.number(own_now.get(k)) - ra.number(own_past.get(k))) / span, 1) for k in ("book", "profit", "revenue", "deals", "visits")})

print("\n== component tables with the formula that fits (book)")
shops, buyers = ra.eligible(now, "shops"), ra.eligible(now, "buyers")
sr, br = ra.rows_for(now, me)
print("  shop computed", round(ra.computed_all(sr, shops, SHOP), 4), "official", sr["all"], "buyer computed", round(ra.computed_all(br, buyers, BUYER), 4), "official", br["all"])
for key, row in ra.breakdown(sr, shops, SHOP).items():
    print("  shop", key, row)
for key, row in ra.breakdown(br, buyers, BUYER).items():
    print("  buyer", key, row)
print("  book for +.05:", ra.value_for_gain(sr, shops, SHOP, "book", .05), "revenue for +.05:", ra.value_for_gain(sr, shops, SHOP, "revenue", .05), "stars_rank for +.05:", ra.value_for_gain(sr, shops, SHOP, "stars_rank", .05))
contract = {"surplus": .5, "forum": .3, "deals": .2}
errors = [abs(ra.computed_all(r, buyers, contract) - r["all"]) for r in buyers]
print("  buyer formula as written in the contract (.5 .3 .2): mean error", round(sum(errors) / len(errors), 5), "against .5 .3 .1 .1:", ra.formula_check(now)["buyers"])

print("\n== buyer forum value against posts, saved snapshots from 11-05")
last_seen = None
for point in ra.history(ROOT):
    values = point["values"]
    key = (values.get("posts"), values.get("forum"))
    if point["observed_at"] >= 1793786400 and key != last_seen:
        print("  ", datetime.fromtimestamp(point["observed_at"], timezone.utc).strftime("%m-%d %H:%MZ"), "posts", key[0], "forum", key[1])
        last_seen = key
values = sorted(ra.number(r.get("forum")) for r in buyers)
print("  field forum p50 p75 p90", values[len(values) // 2], values[int(len(values) * .75)], values[int(len(values) * .9)], "ours", br["forum"], "what if forum .57:", round(ra.what_if(br, buyers, BUYER, "forum", .57), 4))
for posts in (40, 100, 200, 350):
    print(f"  what if posts {posts}: {ra.what_if(br, buyers, BUYER, 'posts', posts):+.4f}")
