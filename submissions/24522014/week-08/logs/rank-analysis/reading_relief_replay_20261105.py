"""Read-only, no network, no model. Group B against money actually settled, and the relief conditions on saved stops. Uses the installed code. Shop text is not printed.

Own check: the ask we read from a reply against the price settled when we then offered exactly that reply's last figure or the shop closed at it.
Public check: the read figure against public settlements of the same shop and SKU within an hour, which are other buyers' deals, some negotiated.
"""
import ctypes
from collections import Counter, defaultdict
from datetime import datetime
import glob
import json
import os
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import buyer_fast as fast
import reference_relief as rr

ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00100000)
A, B = {"group_a": True, "group_b": False}, {"group_a": False, "group_b": True}
SINCE, UNTIL = "20261103T120000", "20261105T060000"
SURVEY_SINCE, SURVEY_UNTIL = "20261104T100000", "20261105T051100"
catalog = json.loads((ROOT / ".market/catalog.json").read_text(encoding="utf-8"))
paid = {}
for line in (ROOT / ".market/payments.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
    try:
        row = json.loads(line)
    except ValueError:
        continue
    if row.get("paid"):
        paid[row.get("order")] = row


def message_of(task):
    """(text, metadata) of the seller's current message, the way the buyer sees it."""
    if isinstance(task, dict) and isinstance(task.get("task"), dict):
        task = task["task"]
    message = ((task or {}).get("status") or {}).get("message") or {}
    text = " ".join(p.get("text", "") for p in message.get("parts", []) if isinstance(p, dict))
    meta = dict(message.get("metadata") or {}) if isinstance(message.get("metadata"), dict) else {}
    for part in message.get("parts", []):
        if isinstance(part, dict) and isinstance(part.get("metadata"), dict):
            meta.update(part["metadata"])
    return text, meta


def replies(record):
    folder = Path(str(record.get("buyer_log_dir") or "").replace("\\", "/")).name
    found = []
    for path in sorted(glob.glob(str(ROOT / "logs/buyer" / folder / "*.response.txt"))):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            continue
        found.append((message_of(data.get("result")), (((data.get("result") or {}).get("task") or data.get("result") or {}).get("status") or {}).get("state")))
    return found


records = []
for name in sorted(n for n in os.listdir(ROOT / "logs/market-buyer") if SINCE <= n < UNTIL):
    try:
        data = json.loads((ROOT / "logs/market-buyer" / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        continue
    records.append((name, data["record"], data.get("selection_evidence") or []))
print("records", len(records), "paid in them", sum(r.get("order") in paid for _, r, _ in records))

public = defaultdict(list)
seen = set()
intel = json.loads((ROOT / ".market/market-intel.json").read_text(encoding="utf-8")).get("rows", [])
rows_iter = list(intel)
for path in ("logs/star-audit/20261105/rows.jsonl", "logs/star-audit/20261105/rows-tail.jsonl"):
    with open(ROOT / path, encoding="utf-8") as handle:
        for line in handle:
            try:
                rows_iter.append(json.loads(line))
            except ValueError:
                pass
for row in rows_iter:
    data = row.get("data") or {}
    if row.get("kind") == "settle" and row.get("seq") not in seen:
        seen.add(row.get("seq"))
        public[data.get("shop"), data.get("item")].append((row["ts"], data.get("amount")))

print("\n== reading on every saved reply, own settled deals")
own = defaultdict(Counter)
detail = defaultdict(list)
public_check = defaultdict(Counter)
reach = Counter()
for name, rec, _ in records:
    if not rec.get("buyer_log_dir"):
        continue
    conversation = replies(rec)
    at = datetime.strptime(name[:15], "%Y%m%dT%H%M%S").timestamp() - 32400 + 32400
    settled = paid.get(rec.get("order"))
    for (text, meta), state in conversation:
        if state and state != "TASK_STATE_INPUT_REQUIRED":
            continue
        for group, reading in (("A", A), ("B", B)):
            price, code, how = fast.read_ask(text, meta, reading)
            if not how or code != "single_ask":
                continue
            reach[group, how, rec.get("shop_name")] += 1
            near = [amount for ts, amount in public.get((rec.get("url_at_time"), rec.get("item")), []) if abs(ts - at) <= 3600 and amount]
            if near:
                public_check[how]["matches_a_public_price" if price in near else "above_all_public" if price > max(near) else "other"] += 1
            if settled and settled.get("item") == rec.get("item"):
                final = settled.get("price")
                if final == price:
                    own[how]["equal"] += 1
                elif price > final:
                    own[how]["read_above_settled"] += 1
                else:
                    own[how]["read_below_settled"] += 1
                detail[how].append((rec.get("shop_name"), price, final, rec.get("order")))
for how in ("metadata_ask", "my_price", "final_price", "lowest_price", "colon_krw"):
    print(f"  {how}: own settled {dict(own[how])}; public within an hour {dict(public_check[how])}")
for how, rows in detail.items():
    for shop, price, final, order in rows[:0]:
        print(how, shop, price, final)
print("  top shops per reading:")
by = defaultdict(Counter)
for (group, how, shop), n in reach.items():
    by[how][shop] += n
for how, table in by.items():
    print("   ", how, sum(table.values()), table.most_common(4))
for shop in ("gom31-certified-goods", "ggari-shop"):
    rows = [(how, x) for how, items in detail.items() for x in items if x[0] == shop]
    print(f"  {shop}: settled deals with a reading {len(rows)}", Counter(how for how, _ in rows).most_common(), "equal", sum(x[1] == x[2] for _, x in rows))
named = Counter(r.get("shop_name") for _, r, _ in records if r.get("order") in paid)
print("  our paid deals at those shops, whatever the wording:", {s: named[s] for s in ("gom31-certified-goods", "ggari-shop")})
print("  mismatch sizes, read vs settled (ratio), by reading:")
for how, rows in detail.items():
    gaps = sorted(round(x[1] / x[2], 3) for x in rows if x[2] and x[1] != x[2])
    print("   ", how, "n", len(gaps), gaps[:6], "...", gaps[-6:])

print("\n== relief conditions on the survey's window")
hours = (datetime.strptime(SURVEY_UNTIL, "%Y%m%dT%H%M%S") - datetime.strptime(SURVEY_SINCE, "%Y%m%dT%H%M%S")).total_seconds() / 3600
hit, refused = [], Counter()
for name, rec, _ in records:
    if not SURVEY_SINCE <= name[:15] < SURVEY_UNTIL:
        continue
    trail = [s for s in rec.get("decision_trail") or [] if isinstance(s, dict)]
    if not trail or trail[-1].get("parser_reason") != "over_budget" or trail[-1].get("reason_code") not in ("model_quota_hour", "model_quota_day"):
        continue
    step = trail[-1]
    check = step.get("counter_check") or {}
    item = catalog.get(rec.get("item")) or {}
    grade = (step.get("quote_check") or {}).get("grade")
    kind = "food" if (rec.get("accept_conditions") or {}).get("min_hours_left") else "goods"
    allowed, why = rr.eligible(check.get("ask"), cap=check.get("cap"), list_price=item.get("list_price"), order_budget=rec.get("order_budget"), grade=grade, kind=kind, catalog_kind=item.get("kind"), counter_reason=check.get("reason_code"))
    if allowed:
        hit.append(dict(ask=check["ask"], cap=check["cap"], value=item["list_price"], turn=step.get("turn"), reason=check["reason_code"], grade=grade, category=str(rec.get("category")).split(">")[-1], shop=rec.get("shop_name")))
    else:
        refused[why] += 1
total = sum(r["value"] - r["ask"] for r in hit)
print(f"  allowed {len(hit)} ({len(hit)/hours:.1f}/h of {hours:.1f} h); refused {refused.most_common()}")
print(f"  by reason {Counter(r['reason'] for r in hit).most_common()}; by turn {Counter(r['turn'] for r in hit).most_common()}; grade {Counter(r['grade'] for r in hit).most_common()}")
beds = sum(r["value"] - r["ask"] for r in hit if r["category"] == "침대/매트리스")
print(f"  surplus before breakage {total:,}; beds and mattresses {beds:,} ({beds/max(1,total):.0%}); by category {Counter(r['category'] for r in hit).most_common(5)}; shops {Counter(r['shop'] for r in hit).most_common(5)}")
print(f"  ask/cap median {median(r['ask']/r['cap'] for r in hit) if hit else None}; surplus/list median {median((r['value']-r['ask'])/r['value'] for r in hit) if hit else None}")
for label, rates in (("own record 1.3%", {"A": .013, "B": .013}), ("assumed A3 B6", {"A": .03, "B": .06}), ("pessimistic A2.8 B10", {"A": .028, "B": .10})):
    net = sum(r["value"] - r["ask"] - rates[r["grade"]] * r["value"] for r in hit)
    print(f"  {label}: expected {net:,.0f}; at the rule paid rate .84, per hour {net*.84/hours:,.0f}")
