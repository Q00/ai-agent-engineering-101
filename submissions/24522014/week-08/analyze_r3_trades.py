"""Snapshot settled R3 purchases, sales and stock costs from existing local evidence."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

from market_state import read, write
from settlement_memory import payment_outcomes

ROOT = Path(__file__).resolve().parent


def main():
    captured_at = datetime.now(timezone.utc).isoformat()
    md = ROOT / ".market"
    catalog = read(md / "catalog.json", {})
    inv = read(md / "inventory.json", {})
    intel = read(md / "market-intel.json", {})
    payments = list(payment_outcomes(md / "payments.jsonl").values())
    paid = [p for p in payments if p.get("paid") is True and p.get("ledger")]
    portfolios = {r["ledger"]: r for r in (read(p) for p in (md/"buyer-deals").glob("*.json"))}
    buyers = {}
    for path in (ROOT/"logs/settlement-reviews").glob("*.json"):
        row = read(path)
        source = row.get("source") or {}
        if (source.get("order") or {}).get("round") == 3:
            buyers[(source.get("settled") or {}).get("ledger")] = source
    purchases = []
    for p in paid:
        source = buyers.get(p["ledger"], {})
        portfolio = portfolios.get(p["ledger"], {})
        settled = source.get("settled") or {}
        product = catalog.get(p["item"], {})
        value = settled.get("value", portfolio.get("value_at_purchase"))
        purchases.append({"ledger": p["ledger"], "sku": p["item"], "name": product.get("name", p["item"]),
                          "kind": product.get("kind"), "shop": (source.get("receipt") or {}).get("shop", p["shop"]),
                          "price": p["price"], "value_at_purchase": value,
                          "initial_surplus": value-p["price"] if value is not None else None,
                          "grade": settled.get("grade", portfolio.get("grade")),
                          "unit": settled.get("unit", portfolio.get("unit")),
                          "warranty_hours": settled.get("warranty_hours") or 0})
    costs = {}
    for path in (ROOT/"logs/pricing").glob("*.json"):
        row = read(path)
        if row.get("unit"):
            costs[row["unit"]] = row
    rows_by_seq = {r["seq"]: r for r in intel.get("rows", [])}
    text = (ROOT/"logs/market-kit.log").read_text(encoding="utf8", errors="replace")
    sales_by_seq = {}
    for name, price, seq, unit, grade in re.findall(
            r"\[acp\] settled (.+) at (\d+), ledger #(\d+), unit (\S+) grade (\S+)", text):
        pricing = costs.get(unit, {})
        data = rows_by_seq.get(int(seq), {}).get("data", {})
        sku = pricing.get("sku", data.get("item"))
        cost = pricing.get("cost")
        sales_by_seq[int(seq)] = {"ledger": int(seq), "unit": unit, "sku": sku, "name": name,
                                  "price": int(price), "cost": cost, "grade": grade,
                                  "gross_margin": int(price)-cost if cost is not None else None,
                                  "buyer": data.get("buyer")}
    sales = sorted(sales_by_seq.values(), key=lambda r: r["ledger"])
    refused = [p for p in payments if p.get("paid") is False]
    reasons = Counter("remaining_life" if "h of shelf life" in p.get("reason", "") else
                      "grade" if "grade " in p.get("reason", "") else
                      "stock_changed" if "not in stock" in p.get("reason", "") else "other" for p in refused)
    stock = [{k: u.get(k) for k in ("unit", "sku", "name", "cost", "grade", "kind", "list_price")}
             for u in inv.get("units", [])]
    totals = {"purchase_count": len(purchases), "purchase_paid": sum(p["price"] for p in purchases),
              "initial_purchase_surplus": sum(p["initial_surplus"] or 0 for p in purchases),
              "sales_count": len(sales), "sales_revenue": sum(s["price"] for s in sales),
              "known_sold_cost": sum(s["cost"] or 0 for s in sales),
              "known_gross_margin": sum(s["gross_margin"] or 0 for s in sales),
              "missing_sale_costs": sum(s["cost"] is None for s in sales),
              "stock_cost": sum(u["cost"] for u in stock), "balance": inv.get("balance"),
              "bankruptcies": inv.get("bankruptcies"), "payment_refusals": len(refused),
              "refusal_categories": dict(reasons)}
    result = {"captured_at": captured_at, "purchases": purchases, "sales": sales, "stock": stock,
              "totals": totals, "board_observed_at": intel.get("observed_at"),
              "board_own": intel.get("competition", {}).get("own"),
              "notes": ["R3 settled records only; payment refusals are excluded from purchases.",
                        "Gross margin excludes storage and refunds; purchase surplus is provisional.",
                        "Live local files and cached board were observed at different times."]}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = ROOT/"logs/trade-analysis"/(stamp+"-"+uuid4().hex[:8]+".json")
    write(path, result)
    print(json.dumps({"saved": str(path), **result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
