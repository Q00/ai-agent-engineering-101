"""Finite, default-off own-sales quantity comparison for one already selected food SKU.

The existing model's mine_rate includes observed sales, not mature net profit. This is
an opportunity trial, not a profit claim. It never adds/reorders a line; the original
rank, cover, stock/share caps, shelf cover and downstream guards still apply.
"""
import json
import math
import os
import re
import time
from pathlib import Path

SKU = "G5f5f42e0"
SWITCH = "restock_demand_catchup.json"


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def config(state, inv, now=None):
    """A malformed/expired switch or unavailable window is off; no I/O beyond local reads."""
    try:
        now = time.time() if now is None else now
        d = json.loads((Path(state) / SWITCH).read_text(encoding="utf-8"))
        start, stop = d.get("start"), d.get("stop")
        window = inv.get("restock") or {}
        opens, closes = window.get("opens_at"), window.get("closes_at")
        if (d.get("enabled") is not True or not isinstance(d.get("study"), str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", d["study"])
                or not all(number(x) for x in (now, start, stop, opens, closes))
                or not 1800 <= stop - start <= 3600 or not start <= now < stop
                or not start <= opens < closes <= stop or closes - opens > 180
                or not opens - 50 <= now < closes
                or type(d.get("b_parity")) is not int or d["b_parity"] not in (0, 1)):
            return None
        return {"study": d["study"], "opens_at": opens,
                "arm": "B" if int(opens // 1200) % 2 == d["b_parity"] else "A"}
    except Exception:  # a local experiment must never prevent supply
        return None


def adjust(ranked, model, catalog, have, balance, cfg, hours_left, factors, worst):
    """Raise only an existing line's quantity, with the same target caps and score."""
    try:
        on = cfg.get("demand_catchup")
        if not on or on.get("arm") != "B":
            return ranked
        item, m = catalog.get(SKU) or {}, model.get("skus", {}).get(SKU) or {}
        own, base = m.get("mine_rate"), m.get("rate", 0) * m.get("my_share", 0)
        shelf = item.get("shelf_hours")
        if item.get("kind") != "food" or not number(shelf) or shelf < 168:
            return ranked
        if not number(own) or not number(base) or not own > base >= 0:
            return ranked
        cover = min(cfg["cover_hours"], hours_left * cfg["end_cover"], shelf * 0.45)
        cost = worst(SKU, 1, catalog, factors)
        if not number(cost) or cost <= 0 or cover <= 0:
            return ranked
        target = min(cfg["max_units_per_sku"], math.ceil(own * cover),
                     int(balance * cfg["max_sku_share"] / cost))
        qty = min(20, target - have[SKU])
        return [(score, sku, max(old, qty) if sku == SKU else old) for score, sku, old in ranked]
    except Exception:  # the original planner remains the fallback
        return ranked


def record(state, on, lines):
    """Best-effort allocation/final proposal only; no task, account, cost or response data."""
    if not on:
        return
    try:
        row = {"study": on["study"], "opens_at": on["opens_at"], "arm": on["arm"],
               "sku": SKU, "proposed_qty": sum(b["qty"] for b in lines if b["sku"] == SKU)}
        path = Path(state) / "measure" / "restock_demand_catchup.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
    except Exception:
        pass
