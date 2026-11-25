"""Finite first-slot study and separately controlled adopted normal policy.

One separately guarded food unit leads finite B or the enabled normal policy.
The finite reader keeps its original S/E; the normal control is fail-closed and
has its own kill and observation file. A and absent/expired finite switches keep
the baseline unless the independent normal control is enabled. Proposals never
prove kit/server acceptance.
Switch: .market/restock_first_slot_default.json
"""
import json
import os
import re
import time
from pathlib import Path
from restock_first_stock import number

# Separate finite slot study; never retarget the expired first-stock reader.
SKU = "G022ce883"
SWITCH = "restock_first_slot.json"

def config(state, inv, model, catalog, listed, now=None):
    try:
        now = time.time() if now is None else now
        d = json.loads((Path(state) / SWITCH).read_text(encoding="utf-8"))
        start, stop = d.get("start"), d.get("stop")
        window = inv.get("restock") or {}
        opens, closes = window.get("opens_at"), window.get("closes_at")
        m = (model or {}).get("skus", {}).get(SKU) or {}
        item = catalog.get(SKU) or {}
        if (d.get("enabled") is not True or not isinstance(d.get("study"), str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", d["study"])
                or not all(number(x) for x in (now, start, stop, opens, closes))
                or not 1800 <= stop - start <= 3600 or not start <= now < stop
                or not start <= opens < closes <= stop or closes - opens > 180
                or not opens - 50 <= now < closes
                or type(d.get("b_parity")) is not int or d["b_parity"] not in (0, 1)
                or not model or model.get("active") is not True
                or not number(m.get("mine_rate")) or m["mine_rate"] != 0
                or not number(m.get("deals")) or m["deals"] < 3
                or SKU in listed or item.get("kind") != "food" or item.get("shelf_hours") != 48):
            return None
        return {"study": d["study"], "opens_at": opens, "closes_at": closes,
                "arm": "B" if int(opens // 1200) % 2 == d["b_parity"] else "A",
                "model_active": True, "mine_rate": m["mine_rate"], "deals": m["deals"], "listed": False}
    except Exception:  # no experiment read failure can block normal supply
        return None


# Adopted normal policy has a separate fail-closed control and observation file.
# The finite reader and its original S/E are never changed into a default.
DEFAULT_SWITCH = "restock_first_slot_default.json"
DEFAULT_POLICY = "first-slot-default-v1"

def default_config(state, inv, model, catalog, listed, now=None):
    try:
        now = time.time() if now is None else now
        d = json.loads((Path(state) / DEFAULT_SWITCH).read_text(encoding="utf-8"))
        if (not isinstance(d, dict) or set(d) != {"enabled", "policy", "sku"}
                or d.get("enabled") is not True or d.get("policy") != DEFAULT_POLICY or d.get("sku") != SKU):
            return None
        window = inv.get("restock") or {}
        opens, closes = window.get("opens_at"), window.get("closes_at")
        m = (model or {}).get("skus", {}).get(SKU) or {}
        item = catalog.get(SKU) or {}
        if (not all(number(x) for x in (now, opens, closes))
                or not 0 < closes - opens <= 180 or not opens - 50 <= now < closes
                or not model or model.get("active") is not True
                or not number(m.get("mine_rate")) or m["mine_rate"] != 0
                or not number(m.get("deals")) or m["deals"] < 3
                or SKU in listed or item.get("kind") != "food" or item.get("shelf_hours") != 48):
            return None
        return {"study": DEFAULT_POLICY, "opens_at": opens, "closes_at": closes,
                "arm": "B", "mode": "default", "model_active": True,
                "mine_rate": m["mine_rate"], "deals": m["deals"], "listed": False}
    except Exception:  # unreadable/malformed normal control cannot enable supply
        return None


def select(on, buy, inv, waiting, keep):
    """Recheck the entire proposed portfolio in the existing guard, target first.

    No held/pending target can be replenished. Failures retain the baseline.
    The caller must still apply late_lines and final_buy, and preserve queued
    agent quantities displaced by this one-slot allocation.
    """
    if not on:
        return buy
    try:
        now = time.time()
        window_open = (inv.get("restock") or {}).get("open")
        # The kit accepts plans within the reader/opening's 50 s lead and waits
        # until_open before RPC only while opens_at is still ahead. A stale False
        # after opens_at also permits planning; native wait is zero then, and
        # its existing pacing/cash/429 checks govern sending. A known closed
        # snapshot is not a closed plan.
        if (not on["opens_at"] - 50 <= now < on["closes_at"]
                or type(window_open) is not bool or window_open and now < on["opens_at"]):
            on["guard"] = "outside_open_window"
            return buy
        held = [u for u in inv.get("units") or [] if u.get("sku") == SKU
                and not (u.get("expires_at") and u["expires_at"] <= now)]
        pending = [w["qty"] for w in waiting if w.get("sku") == SKU]
        if not all(type(q) is int and q >= 0 for q in pending):
            on["guard"] = "unknown_pending"
            return buy
        on["held_waiting"] = len(held) + sum(pending)
        on["guard"] = "held_or_pending" if on["held_waiting"] else "eligible"
        if on["arm"] != "B" or on["held_waiting"]:
            return buy
        guarded = keep([{"sku": SKU, "qty": 1}] + [b for b in buy if b.get("sku") != SKU])
        if not guarded or guarded[0] != {"sku": SKU, "qty": 1}:
            on["guard"] = "blocked"
            return buy
        on["guard"] = "passed"
        return guarded
    except Exception:
        on["guard"] = "error"
        return buy


def record(state, on, lines):
    if not on:
        return
    try:
        positions = [i for i, b in enumerate(lines) if b.get("sku") == SKU]
        qty = sum(b["qty"] for b in lines if b.get("sku") == SKU)
        now = time.time()
        if type(qty) is not int or qty < 0 or not number(now):
            return
        row = {"study": on["study"], "opens_at": on["opens_at"], "arm": on["arm"],
               "at": now, "sku": SKU, "model_active": True, "mine_rate": on["mine_rate"],
               "deals": on["deals"], "listed": False, "held_waiting": on.get("held_waiting"),
               "guard": on.get("guard", "not_selected"), "proposed_qty": qty,
               "position": positions[0] if positions else None}
        encoded = json.dumps(row, sort_keys=True, allow_nan=False) + "\n"
        name = "restock_first_slot_default.jsonl" if on.get("mode") == "default" else "restock_first_slot.jsonl"
        path = Path(state) / "measure" / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(encoded)
    except Exception:
        pass  # local observation cannot change the proposal
