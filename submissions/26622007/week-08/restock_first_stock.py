"""Finite first-stock allocation trial. Does not change prices or add a SKU.

Only the nominated unlisted 48-hour food with observed zero own recent sales
and positive reachable history qualifies. B reserves at most one held/waiting/new
unit. Every existing restock guard still runs after this reduction.
"""
import json
import math
import os
import re
import time
from pathlib import Path

SKU = "Gb834bbb5"
SWITCH = "restock_first_stock.json"


def number(x):
    return type(x) in (int, float) and math.isfinite(x)


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
        return {"study": d["study"], "opens_at": opens,
                "arm": "B" if int(opens // 1200) % 2 == d["b_parity"] else "A",
                "model_active": True, "mine_rate": m["mine_rate"], "deals": m["deals"], "listed": False}
    except Exception:  # no experiment read failure can block normal supply
        return None


def quantity(on, sku, asked, held_and_waiting):
    after = min(asked, max(0, 1 - held_and_waiting)) if on and on.get("arm") == "B" and sku == SKU else asked
    # One largest single candidate evaluation, never a sum of repeated planner calls.
    # It can precede a discarded plan: record() labels it separately from the final proposal.
    try:
        if on and sku == SKU and all(type(x) is int and x >= 0 for x in (asked, after, held_and_waiting)):
            candidate = {"before_qty": asked, "after_cap_qty": after,
                         "removed_qty": asked - after, "held_waiting_prior_plan_qty": held_and_waiting}
            prior = on.get("_candidate")
            if prior is None or (candidate["removed_qty"], asked) > (prior["removed_qty"], prior["before_qty"]):
                on["_candidate"] = candidate
    except Exception:  # observation cannot change the quantity
        pass
    return after


def record(state, on, lines, inv, waiting):
    """One row per eligible main run, including A and zero final proposals.

    candidate_* describes the largest single evaluated cap, possibly from a
    discarded plan. proposed_qty is only the final KIT_LINES proposal, not a
    counterfactual difference, kit transmission, server supply or settlement.
    """
    if not on:
        return
    try:
        candidate = on.get("_candidate") or {"before_qty": 0, "after_cap_qty": 0,
                                             "removed_qty": 0, "held_waiting_prior_plan_qty": 0}
        quantities = [b["qty"] for b in lines if b.get("sku") == SKU]
        now = time.time()
        held = sum(1 for u in inv.get("units") or [] if u.get("sku") == SKU
                   and not (u.get("expires_at") and u["expires_at"] <= now))
        pending = [w["qty"] for w in waiting if w.get("sku") == SKU]
        if not all(type(x) is int and x >= 0 for x in quantities + pending + list(candidate.values())):
            return
        row = {"study": on["study"], "opens_at": on["opens_at"], "arm": on["arm"], "sku": SKU,
               "model_active": on["model_active"], "mine_rate": on["mine_rate"], "deals": on["deals"],
               "listed": on["listed"], "held_waiting_qty": held + sum(pending),
               "proposed_qty": sum(quantities), "candidate_before_qty": candidate["before_qty"],
               "candidate_after_cap_qty": candidate["after_cap_qty"],
               "candidate_removed_qty": candidate["removed_qty"],
               "candidate_held_waiting_prior_plan_qty": candidate["held_waiting_prior_plan_qty"],
               "observation": "peak_candidate_and_final_proposal"}
        encoded = json.dumps(row, sort_keys=True, allow_nan=False) + "\n"
        path = Path(state) / "measure" / "restock_first_stock.jsonl"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(encoded)
    except Exception:  # local observation failures never change buying
        pass
