"""Read-only look at shop posts by kind: reactions and our visits and deals 1 and 6 hours later, and sales of what was named.

These are concurrent observations. Stock already sells as it arrives and visits rise for many reasons, so nothing here shows a post
caused a sale. Hours compare with the same length before the post, from saved board snapshots.
"""

import argparse
from bisect import bisect_left
from collections import defaultdict
import glob
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent
KINDS = ("stock_notice", "stock_reply", "honest_record", "market_compare", "stock_promotion")


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def snapshots(root):
    points = []
    for path in sorted(glob.glob(str(Path(root) / "logs/market-intel/*.json"))):
        data = read(path, {}) or {}
        shop = ((data.get("competition") or {}).get("own") or {}).get("shops") or {}
        if data.get("observed_at") and shop.get("visits") is not None:
            points.append((data["observed_at"], shop.get("visits"), shop.get("deals")))
    return sorted(points)


def at(points, moment, slack=1800):
    """The snapshot closest to a moment, if one is near enough."""
    if not points:
        return None
    spot = bisect_left(points, (moment,))
    near = min(points[max(0, spot - 1):spot + 1], key=lambda p: abs(p[0] - moment))
    return near if abs(near[0] - moment) <= slack else None


def change(points, start, hours):
    first, last = at(points, start), at(points, start + hours * 3600)
    if not first or not last:
        return None
    return {"visits": last[1] - first[1], "deals": last[2] - first[2]}


def report(root=ROOT, now=None):
    root = Path(root)
    now = time.time() if now is None else now
    actions = read(root / ".market/forum-actions.json", {}) or {}
    effects = {r["key"]: r for r in (read(root / ".market/forum-effects.json", {}) or {}).get("actions") or []}
    catalog = read(root / ".market/catalog.json", {}) or {}
    local = (read(root / ".market/local-settlements.json", {}) or {}).get("records") or {}
    sales = list(local.values()) if isinstance(local, dict) else local
    offers = {}
    for path in glob.glob(str(root / ".market/signed-offers/*.json")):
        offer = read(path, {}) or {}
        if offer.get("unit"):
            offers[offer["unit"]] = offer.get("sku")
    points = snapshots(root)
    rows, summary = [], defaultdict(lambda: {"posts": 0, "likes": 0, "dislikes": 0, "reactions_seen": 0})
    for key, action in actions.items():
        if action.get("role") != "shop" or action.get("kind") not in KINDS:
            continue
        effect = effects.get(key, {})
        start = effect.get("window_start") or action["queued_at"]
        named = set(action.get("skus") or ([action["sku"]] if action.get("sku") else []))
        category = action.get("category")

        def sold_within(hours):
            found = [r for r in sales if start < r.get("ts", 0) <= start + hours * 3600]
            return {"named_skus": sum(offers.get(r.get("unit")) in named for r in found),
                    "category": sum((catalog.get(offers.get(r.get("unit"))) or {}).get("category") == category for r in found) if category else None}
        rows.append({"key": key, "kind": action["kind"], "posted_at": start, "status": effect.get("status"), "likes": effect.get("likes"), "dislikes": effect.get("dislikes"),
                     "change_1h": change(points, start, 1), "change_6h": change(points, start, 6), "before_1h": change(points, start - 3600, 1), "before_6h": change(points, start - 21600, 6),
                     "sold_1h": sold_within(1), "sold_6h": sold_within(6), "window_6h_complete": now >= start + 21600})
        group = summary[action["kind"]]
        group["posts"] += 1
        if effect.get("reaction_observed"):
            group["reactions_seen"] += 1
            group["likes"] += effect.get("likes") or 0
            group["dislikes"] += effect.get("dislikes") or 0
    return {"by_kind": dict(summary), "posts": rows, "note": __doc__.splitlines()[2]}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(report(), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
