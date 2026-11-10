"""Hourly local demand research; no model, network, supply or price authority.

Called under restock.lock by the existing 60-second kit hook. Cache collection
keeps its current cadence. Ledger time and cache/stock time use different clocks.
"""
import hashlib
import json
import re

from feedback import atomic_json, append_event, load_json, number
from market_clock import schedule_now
from market_evidence import demand_profiles

SKU = re.compile(r"[GF][0-9a-f]{8}\Z")


def configuration(cfg):
    rules = cfg.get("hourly_demand", {"enabled": False})
    if not isinstance(rules, dict) or type(rules.get("enabled")) is not bool:
        raise ValueError("Invalid hourly demand policy")
    if not rules["enabled"]:
        return None
    for key, low, high in (("interval_seconds", 3600, 3600), ("max_ranked", 1, 32),
                           ("priority_units_per_run", 1, 2), ("minimum_sales", 3, 100),
                           ("minimum_buyers", 2, 20), ("minimum_shops", 2, 20)):
        value = rules.get(key)
        if type(value) is not int or not low <= value <= high:
            raise ValueError("Invalid hourly demand policy: " + key)
    return rules


def build(public, catalog, rnd, now, clock, rules):
    """Snapshot the preceding market hour, keeping sampling gaps explicit."""
    if (not isinstance(public, dict) or public.get("round") != rnd
            or public.get("schema") != 1 or not number(public.get("updated_at"))
            or not 0 <= now - public["updated_at"] <= 900
            or not isinstance(public.get("events"), list) or len(public["events"]) > 2000
            or not isinstance(catalog, dict) or len(catalog) > 20000
            or clock.get("status") != "synced" or not number(clock.get("now"))
            or not number(clock.get("uncertainty_seconds"))
            or not 0 <= clock["uncertainty_seconds"] <= 6):
        return None
    stock = public.get("market_stock")
    if (not isinstance(stock, dict) or stock.get("round") != rnd
            or not number(stock.get("observed_at")) or not 0 <= now-stock["observed_at"] <= 900):
        return None
    market_now = clock["now"]
    offset = now - market_now
    adjusted = {**public, "updated_at": public["updated_at"]-offset,
                "market_stock": {**stock, "observed_at": stock["observed_at"]-offset},
                "events": [e for e in public["events"] if isinstance(e, dict)
                           and number(e.get("ts")) and 0 <= market_now-e["ts"] <= 3600
                           and e.get("claim_honest") is not False]}
    profiles = demand_profiles(adjusted, market_now)
    rows = []
    for sku, profile in profiles.items():
        product = catalog.get(sku)
        if (not isinstance(sku, str) or not SKU.fullmatch(sku) or not isinstance(product, dict)
                or product.get("kind") not in ("goods", "food")
                or not isinstance(product.get("category"), str)
                or profile["sales"] < rules["minimum_sales"]
                or profile["buyers"] < rules["minimum_buyers"]
                or profile["named_shops"] < rules["minimum_shops"]):
            continue
        pressure = profile["sample_to_stock_ratio"]
        rows.append({"sku": sku, "name": str(product.get("name", sku))[:100],
                     "kind": product["kind"], "category": product["category"],
                     **{k: profile[k] for k in ("sales", "buyers", "named_shops", "median_price",
                         "max_buyer_share", "observed_stock", "sample_to_stock_ratio", "evidence_id")},
                     "measured_stock": number(pressure) and pressure >= 0})
    # Diverse actual buyers precede stock pressure. High unit price is not a
    # profitability signal. Missing stock is unknown, never zero competition.
    rows.sort(key=lambda r: (-min(r["buyers"], 10), -int(r["measured_stock"]),
                             -(r["sample_to_stock_ratio"] if r["measured_stock"] else 0),
                             -r["sales"], -r["named_shops"], r["sku"]))
    rows = rows[:rules["max_ranked"]]
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    result = {"schema": 1, "round": rnd, "status": "ready", "generated_at": now,
              "market_window_start": market_now-3600, "market_window_end": market_now,
              "source_updated_at": public["updated_at"], "source_newest_seq": public.get("newest_seq"),
              "clock_offset_seconds": -offset, "rows": rows,
              "observed_event_rows": len(adjusted["events"]),
              "source_sample": public.get("sample"),
              "coverage": "Bounded settlement sample, not all orders or unmet demand; ranking is not profit or a sales forecast."}
    result["report_id"] = hashlib.sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()[:20]
    return result


def ranks(report, cfg, rnd, now):
    """Expired, malformed or wrong-round research cannot affect spending order."""
    rules = configuration(cfg)
    if (not rules or not isinstance(report, dict) or report.get("schema") != 1
            or report.get("round") != rnd or report.get("status") != "ready"
            or not number(report.get("generated_at"))
            or not 0 <= now-report["generated_at"] < rules["interval_seconds"]
            or not isinstance(report.get("rows"), list) or len(report["rows"]) > rules["max_ranked"]):
        return {}
    result = {}
    for row in report["rows"]:
        if (not isinstance(row, dict) or not isinstance(row.get("sku"), str)
                or not SKU.fullmatch(row["sku"]) or row["sku"] in result
                or type(row.get("rank")) is not int or row["rank"] != len(result)+1):
            return {}
        result[row["sku"]] = row["rank"]
    return result


def refresh(root, inv, cfg, now, *, public=None):
    """At most one successful report per hour; unavailable inputs never mean zero demand.

    Failures retry from local files on later hooks. Error logs are bounded to
    once per five minutes. A successful empty sample is retained for its hour.
    """
    rules = configuration(cfg)
    if not rules:
        return None
    md = root / ".market"
    path = md / "hourly-demand.json"
    try:
        previous = load_json(path)
        if (isinstance(previous, dict) and previous.get("schema") == 1
                and previous.get("round") == inv["round"] and previous.get("status") == "ready"
                and number(previous.get("generated_at"))
                and 0 <= now-previous["generated_at"] < rules["interval_seconds"]
                and (ranks(previous, cfg, inv["round"], now) or previous.get("rows") == [])):
            return previous
        public = load_json(md / "market-evidence.json") if public is None else public
        report = build(public, load_json(md / "catalog.json"), inv["round"], now,
                       schedule_now(md, inv["round"], local_now=now), rules)
        if report is None:
            marker = load_json(md / "hourly-demand-attempt.json")
            marker = marker if isinstance(marker, dict) else {}
            last = marker.get("time")
            if marker.get("round") != inv["round"] or not number(last) or not 0 <= now-last < 300:
                event = {"time": now, "round": inv["round"], "status": "unavailable",
                         "reason": "missing_stale_or_unsynced_inputs; not zero demand"}
                atomic_json(md / "hourly-demand-attempt.json", event)
                append_event(root / "logs/hourly-demand.jsonl", event)
            return None
        atomic_json(path, report)
        append_event(root / "logs/hourly-demand.jsonl", report)
        return report
    except (ValueError, OSError, KeyError, TypeError, OverflowError):
        # Optional research never disables existing supply handling.
        return None
