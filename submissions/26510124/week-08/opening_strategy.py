"""Hourly, evidence-bounded quote targets; local cache, no additional LLM."""
import math
from pathlib import Path
from statistics import median
import time

from feedback import append_event, atomic_json, load_json, number
from market_evidence import comparable_price
from market_clock import schedule_now
from sales_performance import own_price, sku_metrics
from shop_policy import selling_terms
from shop_horizon import clearance_bounds

TTL = 21600
SOURCES = ("comparable_market_sales", "own_settled_sales", "own_opening_history", "manual_pilot_policy")


def key_for(product, warranty):
    return f"{product['sku']}:{product['grade']}:{warranty}"


def pilot_reference(product, warranty, rules, rnd):
    """A configured experiment, explicitly not an observation of market prices."""
    row = rules.get("pilot_targets", {}).get(product["sku"])
    if (rnd != 3 or not isinstance(row, dict) or row.get("grade") != product["grade"]
            or type(row.get("warranty_hours")) is not int or row["warranty_hours"] != warranty
            or type(row.get("target")) is not int or row["target"] <= 0):
        return None
    return {"price": row["target"], "evidence_id": f"policy:{product['sku']}:{product['grade']}:{warranty}:{row['target']}"}


def update(root, inventory, memory, public, policy, now):
    if public is not None and public.get("round") != inventory["round"]:
        public = None
    path = root / ".market/opening-strategy.json"
    old = load_json(path) if path.exists() else {}
    if not isinstance(old, dict):
        raise ValueError("Invalid opening strategy memory")
    if (old.get("round") != inventory["round"]
            or public is not None and public.get("reset_at") != old.get("reset_at")):
        old = {}
    previous = old.get("entries", {})
    if not isinstance(previous, dict) or len(previous) > 100:
        raise ValueError("Oversized opening strategy memory")
    rules = policy["opening"]
    groups = memory.get("funnel", {}).get("groups", {})
    stats = sku_metrics(memory, now)
    schedule = schedule_now(root / ".market", inventory["round"], local_now=now)
    market_now = schedule["now"] if schedule["status"] == "synced" else None
    entries = {}
    for product in inventory["units"][:100]:
        if product.get("grade") not in ("A", "B", "C"):
            continue
        terms = selling_terms(product, policy)
        key = key_for(product, terms["warranty_hours"])
        if key in entries:
            continue
        prior = previous.get(key, {})
        if prior and (not number(prior.get("created_at")) or prior["created_at"] > now):
            prior = {}
        if prior and now - prior["created_at"] < rules["update_seconds"]:
            entries[key] = prior
            continue
        group = groups.get(key, {})
        complete = group.get("completed", 0)
        tasks = group.get("tasks", [])[-100:]
        enough_new = len(set(tasks) - set(prior.get("tasks", []))) >= rules["min_new_tasks"]
        own = own_price(memory, product, terms["warranty_hours"], inventory["round"], now,
                        market_now=market_now)
        market = comparable_price(public, product, terms["warranty_hours"], now)
        pilot = pilot_reference(product, terms["warranty_hours"], rules, inventory["round"])
        reference = own or market
        if own:
            anchor, source = own["price"], "own_settled_sales"
        elif market:
            anchor, source = market["price"], "comparable_market_sales"
        elif pilot and (not prior or prior.get("source") == "manual_pilot_policy"):
            anchor, source, reference = pilot["price"], "manual_pilot_policy", pilot
        elif prior:
            anchor, source = prior["anchor"], prior["source"]
        elif complete >= rules["min_completed_tasks"] and len(group.get("opening_prices", [])) >= rules["min_completed_tasks"]:
            anchor, source = int(median(group["opening_prices"])), "own_opening_history"
        else:
            continue  # No invented price evidence; Haiku keeps baseline choice.
        evidence_id = reference.get("evidence_id") if reference else prior.get("evidence_id")
        new_reference = reference is not None and (not prior or source != prior.get("source")
                                                   or evidence_id != prior.get("evidence_id"))
        if prior and not new_reference and not enough_new:
            entries[key] = ({**prior, "created_at": now} if source == "manual_pilot_policy" else prior)
            continue  # A fixed sample never advances another +/-2% step.
        bias = prior.get("bias_percent", 0)
        if complete >= rules["min_completed_tasks"] and enough_new:
            if group.get("rejected", 0) / complete >= .5:
                bias = -rules["max_change_percent"]
            elif (group.get("settled", 0) / complete >= .8
                  and stats.get(product["sku"], {}).get("mature", 0) >= policy["restock"]["min_mature_sales"]
                  and stats[product["sku"]]["net_estimate"] > 0):
                bias = rules["max_change_percent"]
            else:
                bias = 0
        target = round(anchor * (100 + bias) / 100)
        if prior:
            step = rules["max_change_percent"] / 100
            target = min(math.floor(prior["target"] * (1 + step)),
                         max(math.ceil(prior["target"] * (1 - step)), target))
        entries[key] = {"anchor": anchor, "source": source, "target": target, "bias_percent": bias,
                        "created_at": now, "tasks": tasks, "completed": complete,
                        "evidence_id": evidence_id,
                        "comparables": reference if source in ("comparable_market_sales", "own_settled_sales") else None,
                        "reason": "manual_experimental_price_policy" if source == "manual_pilot_policy"
                                  else "bounded_market_and_own_observations"}
    result = {"schema": 1, "round": inventory["round"], "updated_at": now,
              "reset_at": public.get("reset_at") if public is not None else old.get("reset_at"), "entries": entries}
    atomic_json(path, result)
    changed = {k: {a: v for a, v in e.items() if a != "tasks"} for k, e in entries.items() if e != previous.get(k)}
    if changed:
        append_event(root / "logs/opening-strategy.jsonl", {"time": now, "round": inventory["round"], "changed": changed})
    return result


class OpeningReader:
    def __init__(self, folder, clock=time.time):
        self.path, self.clock = Path(folder) / "opening-strategy.json", clock
        self.checked, self.cached = float("-inf"), None

    def for_product(self, product, warranty, rnd):
        now = self.clock()
        if now - self.checked >= 60:
            self.checked, self.cached = now, load_json(self.path)
        s = self.cached
        if not isinstance(s, dict) or s.get("schema") != 1 or s.get("round") != rnd:
            return None
        entries = s.get("entries")
        if not isinstance(entries, dict) or len(entries) > 100:
            return None
        row = entries.get(key_for(product, warranty))
        if (not isinstance(row, dict) or not number(row.get("created_at"))
                or not 0 <= now - row["created_at"] <= TTL
                or type(row.get("target")) is not int or row["target"] <= 0
                or row.get("source") not in SOURCES):
            return None
        # No raw market text or another shop's instructions reach the seller.
        return {"target": row["target"], "source": row.get("source"), "created_at": row["created_at"]}


def bounds(product, terms, reference, policy, now, *, rnd=None):
    floor, rules = terms["price_floor"], policy["opening"]
    if reference is None:
        pilot = pilot_reference(product, terms["warranty_hours"], rules, rnd)
        if pilot:
            # The first supply is delivered after the restock hook has written
            # its opening cache. Apply the explicit policy immediately, without
            # waiting for another supply window or making any model/network call.
            reference = {"target": pilot["price"], "source": "manual_pilot_policy"}
    target = max(floor, min(product["list_price"], reference["target"])) if reference else None
    low = max(floor, math.floor(target * (100 - rules["band_percent"]) / 100)) if target else floor
    high = max(low, math.ceil(target * (100 + rules["band_percent"]) / 100)) if target else None
    if high is not None:
        high = min(high, max(floor, product["list_price"]))
    source = reference["source"] if reference else None
    age = policy["closing"]
    supplied, expiry = product.get("supplied_at"), product.get("expires_at")
    urgent = (product["kind"] == "goods" and number(supplied) and 0 <= supplied <= now - age["goods_age_hours"] * 3600
              or product["kind"] == "food" and number(expiry) and 0 < expiry - now <= age["food_remaining_hours"] * 3600)
    if urgent:
        cap = (floor * (100 + rules["aged_markup_percent"]) + 99) // 100
        high = min(high, cap) if high is not None else cap
        low, target, source = min(low, high), min(target or high, high), "aged_or_expiring_stock"
    clearance = clearance_bounds(policy.get("horizon"), rnd, product, terms, now)
    if clearance:
        high = min(high, clearance["maximum"]) if high is not None else clearance["maximum"]
        low, target, source = floor, min(target or high, high), clearance["source"]
    if high is None:
        return None
    return {"minimum": low, "maximum": high, "target": target, "source": source}
