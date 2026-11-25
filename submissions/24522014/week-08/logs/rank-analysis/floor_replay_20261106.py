"""Read-only replay: which of our past paid deals would the discount floor have kept, and what would Saved then be. No network, no model, no costs.

Population: our paid Round 3 deals in .market/buyer-deals (complete), by the unit's actual grade and warranty. "kept" = price at or under the ceiling for that grade if the
quote had stated it, and at or under the unknown-grade ceiling if not. A replay can only say which paid prices stay allowed: it cannot say which shops would have come down to the ceiling,
or which deals a lower reference would have added. Protected here means the deal had 6 h of warranty; live, the kit only enforces that for pairs with a past 6 h purchase, so live is stricter.
Run from week-08 with --out; prints the same JSON.
"""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import discount_floor as floor  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out")
    args = parser.parse_args()
    catalog = json.loads((ROOT / ".market/catalog.json").read_text(encoding="utf-8"))
    settings = floor.load()
    rows = []
    for path in (ROOT / ".market/buyer-deals").glob("*.json"):
        deal = json.loads(path.read_text(encoding="utf-8"))
        product = catalog.get(deal.get("sku")) or {}
        value = deal.get("value_at_purchase") or product.get("list_price")
        if not value or not deal.get("price"):
            continue
        covered = (deal.get("warranty_hours") or 0) >= 6
        kind = product.get("kind")
        grade = deal.get("grade")
        rows.append({"kind": kind, "grade": grade, "covered": covered, "discount": 1-deal["price"]/value, "price": deal["price"],
                     "stated": deal["price"] <= floor.ceiling(value, floor.required(kind, grade, covered, settings, ["A", "B"])),
                     "unknown": deal["price"] <= floor.ceiling(value, floor.required(kind, None, covered, settings, ["A", "B"]))})
    groups = defaultdict(list)
    for row in rows:
        groups["food" if row["kind"] == "food" else f"goods/{row['grade']}/{'warranty' if row['covered'] else 'no_warranty'}"].append(row)
    table = {}
    for name, group in sorted(groups.items()):
        table[name] = {"deals": len(group), "mean_discount": round(sum(r["discount"] for r in group)/len(group), 4), "kept_if_grade_stated": sum(r["stated"] for r in group), "kept_if_grade_unknown": sum(r["unknown"] for r in group),
                       "mean_discount_of_kept_if_stated": round(sum(r["discount"] for r in group if r["stated"])/max(1, sum(r["stated"] for r in group)), 4)}
    band = Counter()
    for row in rows:
        band[("under_.20" if row["discount"] < .2 else ".20-.25" if row["discount"] < .25 else ".25-.30" if row["discount"] < .3 else ".30-.40" if row["discount"] < .4 else ".40+")] += 1
    def saved(group):
        return sum(r["discount"] for r in group)/(len(group)+100)
    kept = [r for r in rows if r["stated"]]
    report = {"settings": settings, "deals": len(rows), "by_case": table, "discount_bands": dict(sorted(band.items())), "mean_discount_all": round(sum(r["discount"] for r in rows)/len(rows), 4),
              "saved_before_faults_all": round(saved(rows), 4), "kept_if_stated": len(kept), "saved_before_faults_kept": round(saved(kept), 4), "note": "Saved here ignores -1 for breaks, so it is above the board's; compare the two only with each other."}
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text+chr(10), encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
