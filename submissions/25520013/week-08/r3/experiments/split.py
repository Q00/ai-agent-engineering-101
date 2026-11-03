"""Category-hash A/B split: split(categories, salt) -> (A, B), both sorted.

A category goes to B when the first byte of sha256(f"{salt}\\x00{category}") is odd, else to A. The hash is stable
across processes and Python versions (no hash() randomisation), so the same salt always gives the same arms.
catalog_kinds(path=None) -> {category: kind} from a market catalog.json (sku -> {category, kind, ...}; a list of such
rows is accepted too; keys starting with "_" are skipped). path default: $R3_CATALOG, else experiments/state/catalog.json.
"""

import hashlib
import json
import os
from pathlib import Path

CATALOG = Path(__file__).resolve().parent / "state" / "catalog.json"


def arm(category, salt):
    h = hashlib.sha256(f"{salt}\x00{category}".encode("utf-8")).digest()
    return "B" if h[0] & 1 else "A"


def split(categories, salt):
    a, b = [], []
    for c in sorted(set(categories)):
        (b if arm(c, salt) == "B" else a).append(c)
    return a, b


def catalog_kinds(path=None):
    p = Path(path or os.environ.get("R3_CATALOG") or CATALOG)
    cat = json.loads(p.read_text(encoding="utf-8"))
    rows = [v for k, v in cat.items() if not str(k).startswith("_")] if isinstance(cat, dict) else cat
    out = {}
    for r in rows:
        if (
            isinstance(r, dict)
            and r.get("category")
            and r.get("kind") in ("food", "goods")
        ):
            out[r["category"]] = r["kind"]
    return dict(sorted(out.items()))
