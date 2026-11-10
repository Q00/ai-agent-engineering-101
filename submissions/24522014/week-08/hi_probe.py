"""1M+ probe, approved by the user on 11-10: a few 1M+ goods SKUs we have never sold get one place each in restock. Off unless
hi_probe.json says so.

At most 3 SKUs and 1 unit each, at most 3 probe units held plus planned. Their places sit next to shop b's (hi_band.py): outside the
total cap and the run's units and lines, paid from the same run budget at worst cost (.75 x list x surge), and counted in the band's
held plus planned 1M+ worst-cost cap. A probe SKU skips the median test unless surged (then it keeps the test). It needs shop b's
band to be valid, since the band's floor and cap bound it. A SKU leaves the probe as soon as we have any completed sale of it this
round, and from then on it can earn a band place in the ordinary way. The seller never sells below cost, so a probe unit is never
sold below cost either. Read on every restock run; anything missing, unreadable or malformed means off, which is today's plan.
"""

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "hi_probe.json"
SKU = re.compile(r"^G[0-9a-f]{8}$")
MAX_SKUS, MAX_UNITS = 3, 3
ROUND_WINDOW = 30 * 86400  # any of our completed sales this round takes a SKU out of the probe


def settings(path=None):
    try:
        value = json.loads(Path(CONFIG if path is None else path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("enabled") is not True:
        return None
    skus, units = value.get("skus"), value.get("max_units")
    if (not isinstance(skus, list) or not 1 <= len(skus) <= MAX_SKUS or len(set(skus)) != len(skus)
            or not all(isinstance(s, str) and SKU.match(s) for s in skus)
            or type(units) is not int or not 1 <= units <= MAX_UNITS):
        return None
    return {"skus": list(skus), "max_units": units}


def for_run(md, now, catalog, path=None):
    """{"skus": [probe SKUs still never sold, in the file's order], "max_units"} for one restock run, or None when off or on any error."""
    try:
        chosen = settings(path)
        if not chosen:
            return None
        import median_bypass
        counts = median_bypass.confirmed_counts(md, now, window=ROUND_WINDOW)
        if counts is None:
            return None
        sold = {sku for sku, (n, _) in counts.items() if n > 0}
        skus = [s for s in chosen["skus"] if s not in sold and (catalog.get(s) or {}).get("kind") == "goods"]
        return {"skus": skus, "max_units": chosen["max_units"]}
    except Exception:
        return None
