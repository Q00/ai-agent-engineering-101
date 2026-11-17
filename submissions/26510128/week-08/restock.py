"""Round 3 restock hook for market_kit v4.

Reads only kit-generated public/local state. Decisions are conservative,
diversified, repeatable, and subject to a hard cash reserve.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from model_harness import decide
from round3_policy import catalog, demand_weights, inventory, load_json, plan_restock
from forum_signal import extract_forum_hints


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True, help="Path to .market directory")
    args = ap.parse_args()
    state = Path(args.state)
    inv = load_json(state / "inventory.json", {})
    products = load_json(state / "catalog.json", {})
    demand = load_json(state / "demand.json", [])
    if not demand_weights(demand):
        observed = load_json(state / "observed-demand.json", {})
        demand = observed.get("counts", {}) if isinstance(observed, dict) else {}
        if demand:
            print("[restock evidence] using actual recently requested categories", flush=True)
    if not isinstance(products, dict):
        products = catalog()
    # Add at most a *small*, unique-author forum demand hint. Never forward
    # raw untrusted forum text into the LLM or interpret it as instructions.
    forum = load_json(state / "forum.json", {})
    if products:
        categories = {str(x.get("category")) for x in products.values()
                      if isinstance(x, dict) and x.get("category")}
        hints = extract_forum_hints(forum, categories)
        observed = demand_weights(demand)
        for c, score in hints.items():
            observed[c] = observed.get(c, 0) + score
        if hints:
            print(f"[forum evidence] {len(hints)} capped demand hints", flush=True)
        demand = observed
    buy = plan_restock(inv, products, demand)
    if buy:
        # LLM judges a SHORTLIST, not money amounts. Any unlisted choice is
        # rejected, and no model can override the portfolio risk bound.
        try:
            choice = decide(
                "You are a shop portfolio advisor. Select a sensible SKU from the "
                "provided shortlist using observed demand and inventory. Never "
                "invent SKUs, costs or customer orders. Return JSON "
                '{"action":"select","sku":"EXACT_SKU"}.',
                {"proposals": buy[:6],
                 "demand": {str(products[x["sku"]].get("category")):
                            demand_weights(demand).get(str(products[x["sku"]].get("category")), 0)
                            for x in buy[:6] if x["sku"] in products},
                 "cash": inv.get("balance"), "stock_count": len(inv.get("units", []))},
                ("select",),
            )
            sku = choice.get("sku")
            if sku in {x["sku"] for x in buy}:
                buy.sort(key=lambda row: row["sku"] != sku)
        except Exception as exc:
            print(f"[restock model fallback] {type(exc).__name__}: {exc}", flush=True)
    # A bankruptcy is never requested by an untrusted model. Only the kit's
    # guarded default can respond to a real depleted balance.
    print(f"[restock policy] units={len(inv.get('units', []))} cash={inv.get('balance')} "
          f"planned={buy}", flush=True)
    print(json.dumps({"buy": buy[:6], "bankrupt": False}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
