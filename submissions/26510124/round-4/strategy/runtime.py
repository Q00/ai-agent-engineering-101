"""Local policy context, not a prewritten market tool.

Forged readers normalize current market observations into state/<role>/evidence.json.
Unknown or stale observations never become zero balances or purchase permission.
"""
from collections import Counter
import json
import math
from pathlib import Path
import time

from strategy.round3_math import buying_terms, selling_terms

HERE = Path(__file__).resolve().parent


def policy():
    return json.loads((HERE / "policy.json").read_text())


def integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("Expected a verified nonnegative integer")
    return value


def seller_terms(product, fees, config=None):
    """Retain R3 grade margins, accounting for verified R4 seller fees."""
    cfg = config or policy()["shop"]
    result = selling_terms(product, cfg)
    bps = integer(fees["seller_bps"])
    fixed = integer(fees["seller_fixed"])
    if bps >= 10000:
        raise ValueError("Seller fee leaves no proceeds")
    for key in ("price_floor", "closing_price_floor"):
        # p - ceil(p*bps/10000) - fixed >= old net floor.
        result[key] = ((result[key] + fixed) * 10000 + 9999 - bps) // (10000 - bps)
    result["qualification"] = "Policy floor, not a quote or a verified warranty certificate"
    return result


def buyer_terms(budget, product, order, fees, config=None):
    cfg = config or policy()["buyer"]
    result = buying_terms(budget, product=product, order=dict(order, round=4), config=cfg)
    bps = integer(fees["buyer_bps"])
    fixed = integer(fees["buyer_fixed"])  # verified gas/other costs in the SAME unit
    cap = budget * cfg["budget_cap_percent"] // 100
    # integer p + ceil(p*bps/10000) + fixed <= cap
    affordable = max(0, (cap - fixed) * 10000 // (10000 + bps))
    result["price_limit"] = min(result["price_limit"], affordable)
    if result["price_limit"] < 1:
        raise ValueError("No affordable price after fees")
    for key in ("target_price", "completion_price", "max_concession"):
        if key in result:
            result[key] = min(result[key], result["price_limit"])
    result["total_spend_cap"] = cap
    result["is_payment_authorization"] = False
    return result


def read_evidence(root, role, now):
    path = Path(root) / "state" / role / "evidence.json"
    try:
        if path.stat().st_size > 500_000:
            raise ValueError("Evidence too large")
        data = json.loads(path.read_text())
        at = data.get("observed_at")
        if (data.get("round") != 4 or type(at) not in (int, float)
                or not math.isfinite(at) or not 0 <= now - at <= 900
                or not isinstance(data.get("source"), str) or not data["source"].startswith("https://")):
            raise ValueError("Need fresh Round 4 observations with a source URL")
        return data
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def inventory_analysis(data, cfg, now):
    """R3 inventory role: code calculates coverage; the strategist only advises.

Normalized input is deliberately distinct from old inventory/receipt schemas.
No guessed demand, Round 3 records, or market writes are used here.
"""
    if data is None:
        return {"status": "unavailable", "reason": "No fresh Round 4 evidence; forge a reader first"}
    try:
        cash = integer(data["cash"])
        units, sales = data["units"], data["sales"]
        if not isinstance(units, list) or not isinstance(sales, list):
            raise ValueError("Incomplete inventory")
        held = Counter()
        kinds = {}
        for unit in units:
            if unit["kind"] not in ("food", "goods") or not isinstance(unit["sku"], str):
                raise ValueError("Unknown inventory kind/SKU")
            kinds[unit["sku"]] = unit["kind"]
            if unit["available"] is True:
                held[unit["sku"]] += 1
        recent = Counter()
        receipts = set()
        for sale in sales:
            if (sale.get("status") == "settled" and isinstance(sale.get("receipt_id"), str)
                    and sale["receipt_id"] not in receipts and type(sale.get("at")) in (int, float)
                    and 0 <= now - sale["at"] <= 3600):
                receipts.add(sale["receipt_id"])
                recent[sale["sku"]] += integer(sale.get("units", 1), 1)
        opens = data["next_supply_at"]
        if type(opens) not in (int, float) or not math.isfinite(opens) or opens < now:
            raise ValueError("No current supply schedule")
        cover = opens - now + 1200  # R3 stock_analysis coverage rule
        restock = cfg["restock"]
        targets = []
        for sku in sorted(set(held) | set(recent))[:64]:
            kind = kinds.get(sku, data.get("catalog", {}).get(sku, {}).get("kind"))
            if kind not in ("food", "goods"):
                continue
            cap = restock["max_food_units_per_sku"] if kind == "food" else restock["max_units_per_sku"]
            target = min(cap, math.ceil(recent[sku] * cover / 3600) + int(recent[sku] > 0))
            targets.append({"sku": sku, "available": held[sku], "confirmed_sales_last_hour": recent[sku],
                            "target": target, "needed": max(0, target - held[sku])})
        return {"status": "advisory", "targets": targets, "held_units": len(units),
                "cash_reserve": (cash * restock["cash_reserve_percent"] + 99) // 100,
                "max_units_per_run": min(restock["max_units_per_run"], max(0, restock["max_stock_units"] - len(units))),
                "qualification": "Forecast only. Profitability, refunds, demand diversity, depth, budget and supply gates still apply."}
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
        return {"status": "unavailable", "reason": "Incomplete/invalid normalized inventory observations"}


def role_context(root, role, now=None):
    now = time.time() if now is None else now
    cfg = policy()
    result = {"round": 4, "objective": "credits", "policy": cfg["buyer" if role == "buyer" else "shop"]}
    data = read_evidence(root, "buyer" if role == "buyer" else "shop", now)
    if role != "buyer":
        result["inventory_analysis"] = inventory_analysis(data, cfg["shop"], now)
    result["calculated_terms"] = []
    if data:
        try:
            if role == "buyer":
                result["calculated_terms"] = buyer_terms(data["budget"], data["product"], data["order"], data["fees"], cfg["buyer"])
            else:
                result["calculated_terms"] = [{"unit_id": u["id"], **seller_terms(u, data["fees"], cfg["shop"])}
                                              for u in data["units"][:64] if u.get("available") is True]
        except (KeyError, ValueError, TypeError, AttributeError):
            result["calculated_terms"] = {"status": "unavailable", "reason": "Verified cost, grade, fees and order required"}
    result["disabled_until_revalidated"] = cfg["disabled_until_revalidated"]
    return json.dumps(result, ensure_ascii=False)
