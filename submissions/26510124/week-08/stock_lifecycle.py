"""Bounded actual-unit memory, without model calls or network requests.

Missing stock is an unresolved acquisition cost, never a proven sale or
spoilage. A matching confirmed settlement removes that estimate. Held costs
are reported separately, and the official wallet remains total-profit truth.
The restock lock serializes this writer. Capacity/corruption fails closed;
unresolved units and settled identities are never silently discarded.
"""
import copy
import json
from pathlib import Path

from feedback import append_event, atomic_json, number

MAX_BYTES = 800_000
MAX_UNITS = 2000
MAX_SALES = 1000


def _text(value, limit=128):
    return isinstance(value, str) and 0 < len(value) <= limit


def _timestamp(value):
    return number(value) and value >= 0


def _unit(raw, now, *, settled=False):
    if (not isinstance(raw, dict) or not _text(raw.get("unit"))
            or not _text(raw.get("sku")) or not _text(raw.get("category"), 256)
            or raw.get("kind") not in ("goods", "food")
            or type(raw.get("cost")) is not int or not 0 < raw["cost"] <= 10**15
            or not _timestamp(raw.get("supplied_at")) or raw["supplied_at"] > now + 5):
        raise ValueError("Incomplete actual stock unit; acquisition cost cannot be inferred")
    expiry = raw.get("expires_at")
    if ((raw["kind"] == "food" and not settled and not _timestamp(expiry))
            or expiry is not None and not _timestamp(expiry)):
        raise ValueError("Invalid actual stock expiry")
    return {k: raw[k] for k in ("sku", "category", "kind", "cost", "supplied_at")} | {
        "expires_at": expiry}


def _load(path, now):
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Lifecycle memory exceeds 800KB; refusing to discard identities")
    state = json.loads(raw)
    if (not isinstance(state, dict) or state.get("schema") != 1
            or type(state.get("round")) is not int or state["round"] < 3
            or not isinstance(state.get("units"), dict) or len(state["units"]) > MAX_UNITS
            or not _timestamp(state.get("updated_at")) or state["updated_at"] > now + 5
            or state.get("reset_at") is not None and not _timestamp(state["reset_at"])
            or state.get("injected") is not None and not _timestamp(state["injected"])):
        raise ValueError("Invalid lifecycle memory; refusing to reset it")
    ledgers = set()
    for uid, row in state["units"].items():
        if not isinstance(row, dict):
            raise ValueError("Invalid lifecycle unit")
        _unit({**row, "unit": uid}, now, settled=row.get("state") == "settled")
        if (row.get("state") not in ("held", "missing", "settled")
                or not _timestamp(row.get("first_seen")) or not _timestamp(row.get("last_seen"))
                or not row["first_seen"] <= row["last_seen"] <= now + 5):
            raise ValueError("Invalid lifecycle observation")
        if row["state"] == "missing" and (
                not _timestamp(row.get("missing_at")) or row["missing_at"] > now + 5
                or row.get("missing_reason") not in ("unconfirmed", "reset_or_seizure_suspected")):
            raise ValueError("Invalid unresolved lifecycle observation")
        if row["state"] == "settled":
            if (type(row.get("ledger")) is not int or row["ledger"] <= 0
                    or row["ledger"] in ledgers or not _timestamp(row.get("settled_at"))
                    or row["settled_at"] > now + 5):
                raise ValueError("Invalid settled lifecycle identity")
            ledgers.add(row["ledger"])
    return state


def update(root, inventory, sales_memory, now):
    """Persist current-round actual observations and matched settlement IDs.

    sales_memory is sales_performance.update's confirmed-sale output. Optional
    reset_at is sticky within a round; a newer explicit reset starts an epoch.
    An injection change only marks simultaneous disappearances as suspected
    seizure. Neither a balance change nor an attempted buy creates a unit.
    """
    if (not _timestamp(now) or not isinstance(inventory, dict)
            or type(inventory.get("round")) is not int or inventory["round"] < 3
            or not isinstance(inventory.get("units"), list) or len(inventory["units"]) > MAX_UNITS
            or not isinstance(sales_memory, dict) or sales_memory.get("round") != inventory["round"]
            or not isinstance(sales_memory.get("sales"), list) or len(sales_memory["sales"]) > MAX_SALES):
        raise ValueError("Incomplete or mismatched lifecycle input")
    rnd = inventory["round"]
    reset = sales_memory.get("reset_at")
    injected = inventory.get("injected")
    if ((reset is not None and (not _timestamp(reset) or reset > now + 5))
            or injected is not None and not _timestamp(injected)):
        raise ValueError("Invalid inventory epoch evidence")
    path = Path(root) / ".market/stock-lifecycle.json"
    old = _load(path, now) if path.exists() else None
    if old and old["round"] > rnd:
        raise ValueError("Stale inventory round cannot reset lifecycle memory")
    if old and old["round"] == rnd and old.get("reset_at") is not None:
        if reset is not None and reset < old["reset_at"]:
            raise ValueError("Stale lifecycle reset evidence")
        if reset is None:
            reset = old["reset_at"]
    new_epoch = not old or old["round"] != rnd or old.get("reset_at") != reset
    if new_epoch:
        state = {"schema": 1, "round": rnd, "reset_at": reset, "updated_at": now,
                 "injected": injected, "units": {}}
    else:
        state = copy.deepcopy(old)
    previous_injected = state.get("injected")
    injection_changed = (injected is not None and previous_injected is not None
                         and injected != previous_injected)
    if injected is not None:
        state["injected"] = injected
    state["updated_at"] = now
    rows, events, present = state["units"], [], set()
    if old and new_epoch:
        events.append({"event": "epoch_changed", "previous_round": old["round"], "reset_at": reset})
    for raw in inventory["units"]:
        unit = _unit(raw, now)
        uid = raw["unit"]
        if uid in present:
            raise ValueError("Duplicate actual unit in inventory")
        present.add(uid)
        if reset is not None and unit["supplied_at"] < reset:
            continue  # Old cached inventory cannot restart acquisition counts.
        previous = rows.get(uid)
        if previous and any(previous[k] != unit[k] for k in unit
                            if not (k == "expires_at" and previous["state"] == "settled"
                                    and previous[k] is None)):
            raise ValueError("Actual unit identity changed")
        if not previous:
            rows[uid] = {**unit, "state": "held", "first_seen": now, "last_seen": now}
            events.append({"event": "observed", "unit": uid, "sku": unit["sku"], "cost": unit["cost"]})
        elif previous["state"] != "settled":
            if previous["state"] == "missing":
                events.append({"event": "reappeared", "unit": uid, "sku": unit["sku"]})
            previous.update(state="held", last_seen=now)
            previous.pop("missing_at", None)
            previous.pop("missing_reason", None)
    ledger_units = {r["ledger"]: uid for uid, r in rows.items() if r["state"] == "settled"}
    for sale in sales_memory["sales"]:
        if not isinstance(sale, dict):
            raise ValueError("Invalid confirmed sales memory")
        # Some older receipt records have no acquisition facts. They cannot
        # clear an unresolved cost by guessing a SKU's typical supply price.
        if (not _text(sale.get("unit")) or not _text(sale.get("sku"))
                or type(sale.get("cost")) is not int or not 0 < sale["cost"] <= 10**15
                or type(sale.get("ledger")) is not int or sale["ledger"] <= 0
                or not _timestamp(sale.get("receipt_time")) or sale["receipt_time"] > now + 5):
            continue
        if reset is not None and sale["receipt_time"] < reset:
            continue
        uid, ledger = sale["unit"], sale["ledger"]
        previous = rows.get(uid)
        if previous and sale["receipt_time"] < previous["supplied_at"]:
            raise ValueError("Settlement predates observed acquisition")
        if previous and (previous["sku"] != sale["sku"] or previous["cost"] != sale["cost"]):
            raise ValueError("Settlement does not match observed unit, SKU and cost")
        if ledger in ledger_units and ledger_units[ledger] != uid:
            raise ValueError("Settlement ledger identity belongs to another unit")
        if previous and previous["state"] == "settled":
            if previous["ledger"] != ledger:
                raise ValueError("One actual unit has two settlements")
            continue
        if not previous:
            # A unit may be supplied and sold between inventory observations.
            # Receipt facts are actual observations, not restock intentions.
            if (sale.get("kind") not in ("goods", "food") or not _text(sale.get("category"), 256)
                    or not _timestamp(sale.get("supplied_at")) or sale["supplied_at"] > now + 5
                    or reset is not None and sale["supplied_at"] < reset):
                continue
            # Food receipts need not contain expiry. It is no longer needed
            # for settled units; retain a nullable value, never invent expiry.
            previous = {k: sale[k] for k in ("sku", "category", "kind", "cost", "supplied_at")}
            previous.update(expires_at=sale.get("expires_at"), first_seen=now, last_seen=now)
            _unit({**previous, "unit": uid}, now, settled=True)
            rows[uid] = previous
        previous.update(state="settled", ledger=ledger, settled_at=now)
        previous.pop("missing_at", None)
        previous.pop("missing_reason", None)
        ledger_units[ledger] = uid
        events.append({"event": "settlement_confirmed", "unit": uid, "sku": sale["sku"], "ledger": ledger})
    for uid, row in rows.items():
        if row["state"] == "held" and uid not in present:
            reason = "reset_or_seizure_suspected" if injection_changed else "unconfirmed"
            row.update(state="missing", missing_at=now, missing_reason=reason)
            events.append({"event": "missing_unconfirmed", "unit": uid, "sku": row["sku"],
                           "cost_estimate": row["cost"], "reason": reason})
    if len(rows) > MAX_UNITS or len(json.dumps(state, ensure_ascii=False).encode()) + 1 > MAX_BYTES:
        raise ValueError("Lifecycle capacity reached; refusing buys instead of pruning unresolved units or IDs")
    atomic_json(path, state)  # Durable dedup precedes append-only diagnostic events.
    for event in events:
        append_event(Path(root) / "logs/stock-lifecycle.jsonl", {"time": now, "round": rnd, **event})
    return state


def metrics(state, now):
    """Per-SKU acquisition and unresolved-cost observations, not wallet profit.

    held_cost and unresolved_cost are disjoint. expired_unconfirmed_cost and
    seized_suspected_cost are subsets of unresolved_cost; never add them again.
    Expired-but-held food is a risk, not an observed disposal. Counters retain
    the whole current epoch, unlike sales_performance's seven-day window.
    """
    result = {}
    for row in state["units"].values():
        g = result.setdefault(row["sku"], {
            "category": row["category"], "kind": row["kind"],
            "observed_units": 0, "observed_purchase_cost": 0, "settled_count": 0, "settled_cost": 0,
            "held_count": 0, "held_cost": 0, "unresolved_count": 0, "unresolved_cost": 0,
            "expired_unconfirmed_count": 0, "expired_unconfirmed_cost": 0,
            "held_expired_cost": 0, "held_expiring_cost": 0, "seized_suspected_cost": 0,
            "pending_count": 0})
        cost = row["cost"]
        g["observed_units"] += 1
        g["observed_purchase_cost"] += cost
        expiry = row.get("expires_at")
        expired = row["kind"] == "food" and number(expiry) and expiry <= now
        if row["state"] == "settled":
            g["settled_count"] += 1
            g["settled_cost"] += cost
        elif row["state"] == "held":
            g["held_count"] += 1
            g["held_cost"] += cost
            if expired:
                g["held_expired_cost"] += cost
            elif row["kind"] == "food" and number(expiry) and expiry <= now + 7200:
                g["held_expiring_cost"] += cost
        else:
            g["unresolved_count"] += 1
            g["pending_count"] += 1
            g["unresolved_cost"] += cost
            if row["missing_reason"] == "reset_or_seizure_suspected":
                g["seized_suspected_cost"] += cost
            elif expired:
                g["expired_unconfirmed_count"] += 1
                g["expired_unconfirmed_cost"] += cost
    return result
