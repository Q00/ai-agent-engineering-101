"""Read-only, no network, no model: break-rate sensitivity for over-cap asks, the share from beds, and refused-at-open rates before and after the listed-stock preference."""
import json
import os
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[2]
SINCE, UNTIL = "20261104T100000", "20261105T051100"
STOCK_PREFERENCE_INSTALLED = "20261105T005500"  # About 09:55 KST, from logs/stock-preference-staged-tests-20261105T095549.log.
catalog = json.loads((ROOT / ".market/catalog.json").read_text(encoding="utf-8"))
rows = []
for name in sorted(n for n in os.listdir(ROOT / "logs/market-buyer") if SINCE <= n < UNTIL):
    data = json.loads((ROOT / "logs/market-buyer" / name).read_text(encoding="utf-8"))
    rows.append((name, data["record"], data.get("selection_evidence") or []))
hours, RATE = 19.2, 187 / 223
SCENARIOS = {"own buyer record, 11 of 831": {"A": .013, "B": .013, None: .013}, "report assumption": {"A": .03, "B": .06, None: .12},
             "joined floors divided by the 42% join rate": {"A": .028, "B": .10, None: .30}}
inside = []
for _, rec, _ in rows:
    trail = [s for s in rec.get("decision_trail") or [] if isinstance(s, dict)]
    if not trail or trail[-1].get("parser_reason") != "over_budget" or trail[-1].get("reason_code") not in ("model_quota_hour", "model_quota_day"):
        continue
    check = trail[-1].get("counter_check") or {}
    ask, cap, value, budget = check.get("ask"), check.get("cap"), check.get("list_price"), rec.get("order_budget")
    if ask and cap and value and budget and ask <= budget and ask < value:
        inside.append(dict(ask=ask, cap=cap, value=value, ratio=ask / cap, grade=(trail[-1].get("quote_check") or {}).get("grade"), kind=(catalog.get(rec.get("item")) or {}).get("kind"), category=str(rec.get("category")).split(">")[-1]))
print("over-cap asks inside the order budget and under list:", len(inside), "grade by kind", sorted({(r["kind"], r["grade"]) for r in inside}, key=str))
for label, rates in SCENARIOS.items():
    print(" ", label, rates)
    for limit in (1.1, 1.2, 1.25, 1.3, 1.4):
        hit = [r for r in inside if r["ratio"] <= limit]
        net = sum(r["value"] - r["ask"] - (rates.get(r["grade"], rates[None]) * r["value"] if r["kind"] == "goods" else 0) for r in hit)
        worse = sum(r["kind"] == "goods" and r["value"] - r["ask"] - rates.get(r["grade"], rates[None]) * r["value"] < 0 for r in hit)
        print(f"    {limit}: n {len(hit)} expected {net:,.0f}, per hour at rule rate {net*RATE/hours:,.0f}, negative deals {worse}")
print("by category within 1.25, surplus before breakage:")
totals = {}
for r in inside:
    if r["ratio"] <= 1.25:
        row = totals.setdefault(r["category"], [0, 0])
        row[0] += 1
        row[1] += r["value"] - r["ask"]
for category, (n, gain) in sorted(totals.items(), key=lambda kv: -kv[1][1])[:5]:
    print(f"  {category}: n {n} surplus {gain:,.0f}")
print("refused at open, by whether the listed-stock preference was installed:")
for label, test in (("before", lambda n: n < STOCK_PREFERENCE_INSTALLED), ("after", lambda n: n >= STOCK_PREFERENCE_INSTALLED)):
    picked = [(n, r, e) for n, r, e in rows if test(n)]
    tried = [x for x in picked if x[1].get("buyer_log_dir")]
    refused = [x for x in tried if x[1].get("failure_reason") == "seller_rejected"]
    known = []
    for _, rec, evidence in refused:
        chosen = next((e for e in evidence if e.get("shop") == rec.get("shop_name") and e.get("sku") == rec.get("item")), {})
        known.append(chosen.get("listed_qty"))
    print(f"  {label}: tried {len(tried)} refused {len(refused)} rate {len(refused)/max(1,len(tried)):.3f}; refused with a known listed qty {sum(q is not None for q in known)}")
