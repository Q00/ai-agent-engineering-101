"""Immutable local inventory and confirmed seller settlement evidence.

Missing units are not assumed sold or spoiled. Gross spread is distinct from
net margin; unknown storage charges, disposals and refunds remain unknown.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
import uuid


def write_new(directory, value, name=None):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name or f"{time.time_ns()}-{uuid.uuid4().hex}.json")
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return path


def snapshot(state, inventory, *, reason, market_run, now=None, source_mtime=None):
    now = time.time() if now is None else now
    if inventory.get("round") != 3 or not isinstance(inventory.get("units"), list):
        raise ValueError("Round 3 inventory snapshot required")
    return write_new(Path(state) / "history/inventory", {
        "observed_at": now, "observed_at_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "source_mtime": source_mtime, "market_run": market_run, "reason": reason,
        "inventory": inventory,
    })


def completion(state, before, completed, *, market_run, now=None, task_id=None):
    if completed.get("status") != "completed":
        return None
    now = time.time() if now is None else now
    mr = completed["order"]["mandate_receipt"]
    ledger = mr.get("ledger")
    unit = mr.get("unit") or completed.get("unit")
    sku = completed["line_items"][0]["id"]
    old = next((u for u in before.get("units", []) if u.get("unit") == unit), None)
    if type(ledger) is not int or ledger < 0 or not old or old.get("sku") != sku:
        raise ValueError("settlement requires ledger and exact inventory unit/SKU match")
    price, cost = completed["totals"][0]["amount"], old.get("cost")
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in (price, cost)):
        raise ValueError("invalid price or acquisition cost")
    supplied = old.get("supplied_at")
    elapsed = now-supplied if type(supplied) in (int, float) and supplied <= now else None
    event = {"market_run": market_run, "task": task_id, "ledger": ledger, "unit": unit, "sku": sku,
             "observed_at": now, "source": "successful ACP complete response ([acp] settled)",
             "inventory_unit": old, "settled_grade": mr.get("grade"),
             "price": price, "actual_cost": cost, "gross_spread": price-cost,
             "seconds_from_supply_to_settlement_observation": elapsed,
             "timing_note": "Local completion observation, not an authoritative settlement timestamp.",
             "confirmed_storage_charge": None, "confirmed_refund": None,
             "confirmed_disposal_loss": None, "confirmed_net_margin": None}
    # One immutable record per ledger and run; conflicting repeats fail visibly.
    key = hashlib.sha256(market_run.encode()).hexdigest()[:16]
    directory = Path(state) / "history/settlements"
    name = f"{key}-{ledger}.json"
    path = directory / name
    if path.exists():
        existing = json.loads(path.read_text())
        if any(existing.get(k) != event[k] for k in ("unit", "sku", "price", "actual_cost")):
            raise ValueError("conflicting settlement evidence")
        return path
    return write_new(directory, event, name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path(".market"))
    parser.add_argument("--market-run", default="round3")
    args = parser.parse_args()
    source = args.state / "inventory.json"
    captured = snapshot(args.state, json.loads(source.read_text()), reason="manual_checkpoint",
                        market_run=args.market_run, source_mtime=source.stat().st_mtime)
    print(captured)
