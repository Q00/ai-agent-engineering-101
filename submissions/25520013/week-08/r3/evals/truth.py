"""v1.18 TRUTH side of evals.memory_eval: answers by brute-force scans of the raw synthetic events. Imports nothing
from memory/ (tests/test_evals.py checks it); the rules below restate the documented layer semantics.

Rules (memory/graph.py, prices.py, gotchas.py docstrings): a settle counts at `t` when ts < t and it was not
refunded before t (a refund at r hides it for t > r). A trajectory (buyer, or shop x SKU) ends
after a gap > 24 h between settles, and is over at t when t - last settle > 24 h. Before the first market settle
(t <= start), or for a key with no record before t: ABSTAIN.
"""

from collections import Counter

from evals import ABSTAIN

IDLE = 24 * 3600
PRICE_BAND = 0.10


class Truth:
    def __init__(self, events, catalog):
        self.cat = catalog
        self.settles = [
            {"seq": e["seq"], "ts": e["ts"], **e["data"], "cat": (catalog.get(e["data"]["sku"]) or {}).get("category")}
            for e in events
            if e["type"] == "settle" and e["src"] == "market"
        ]
        self.start = min((s["ts"] for s in self.settles), default=None)
        by_order = {s["order"]: s for s in self.settles}
        self.refund = {}  # settle seq -> refund ts (the first refund only)
        self.fails = []  # (ts, seq, shop, reason)
        self.broken = []  # (ts, shop): market breakages
        for e in events:
            d = e["data"]
            if e["type"] == "fail":
                det = d.get("detail") if isinstance(d.get("detail"), dict) else {}
                if d.get("stage") == "refund" and det.get("order") in by_order:
                    self.refund.setdefault(by_order[det["order"]]["seq"], e["ts"])
                reason = d.get("reason_code") or d.get("stage")
                self.fails.append((e["ts"], e["seq"], d.get("shop") or det.get("shop"), reason))
            elif e["type"] == "reject":
                self.fails.append((e["ts"], e["seq"], d.get("shop"), d.get("reason_code") or "other"))
            elif e["type"] == "broken":
                self.fails.append((e["ts"], e["seq"], d.get("shop"), "broken"))
                if e["src"] == "market":
                    self.broken.append((e["ts"], d.get("shop")))

    # --- helpers
    def _valid(self, s, t):
        r = self.refund.get(s["seq"])
        return s["ts"] < t and (r is None or r >= t)

    def _no_data(self, t):
        return self.start is None or t <= self.start

    def _buyer(self, b, t):
        return sorted((s for s in self.settles if s["buyer"] == b and s["ts"] < t), key=lambda s: (s["ts"], s["seq"]))

    def _pair(self, shop, sku, t):
        return sorted((s for s in self.settles if (s["shop"], s["sku"]) == (shop, sku) and s["ts"] < t),
                      key=lambda s: (s["ts"], s["seq"]))

    @staticmethod
    def _segment(rows):
        """Index where the current trajectory starts and the last settle ts."""
        i, last = 0, rows[0]["ts"]
        for j, s in enumerate(rows[1:], 1):
            if s["ts"] > last + IDLE:
                i = j
            last = max(last, s["ts"])
        return i, last

    # --- questions
    def price_last(self, shop, sku, t):
        rows = self._pair(shop, sku, t)
        return rows[-1]["amount"] if rows else ABSTAIN

    def trajectory(self, buyer, t):
        rows = self._buyer(buyer, t)
        if self._no_data(t) or not rows:
            return ABSTAIN
        return sorted((s["ts"], s["cat"], s["sku"], s["seq"]) for s in rows if self._valid(s, t))

    def last_cat(self, buyer, t):
        tr = self.trajectory(buyer, t)
        return tr if tr == ABSTAIN else (tr[-1][1] if tr else None)

    def top_cats(self, buyer, t, k=2):
        tr = self.trajectory(buyer, t)
        if tr == ABSTAIN:
            return ABSTAIN
        n = Counter(c for _, c, _, _ in tr)
        return [c for c, _ in sorted(n.items(), key=lambda x: (-x[1], x[0]))[:k]]

    def last_failure(self, shop, t):
        rows = [f for f in self.fails if f[2] == shop and f[0] < t]
        return max(rows)[3] if rows else ABSTAIN

    def fail_count(self, shop, t0, t1):
        return sum(1 for f in self.fails if f[2] == shop and t0 <= f[0] < t1)

    def trade_count(self, shop, sku, t0, t1):
        rows = self._pair(shop, sku, float("inf"))
        if not rows or t1 <= self.start:
            return ABSTAIN
        return sum(1 for s in rows if t0 <= s["ts"] < t1)

    def taste_at(self, buyer, t):
        rows = self._buyer(buyer, t)
        if self._no_data(t) or not rows:
            return ABSTAIN
        i, last = self._segment(rows)
        if t - last > IDLE:
            return {}
        return dict(Counter(s["cat"] for s in rows[i:] if self._valid(s, t)))

    def fav_at(self, buyer, t):
        taste = self.taste_at(buyer, t)
        if taste == ABSTAIN or not taste:
            return taste if taste == ABSTAIN else None
        return min(taste.items(), key=lambda x: (-x[1], x[0]))[0]

    def broken_at(self, shop, t):
        return sum(1 for ts, s in self.broken if s == shop and ts < t)

    def ref_price(self, shop, sku, t):
        if self._no_data(t):
            return ABSTAIN
        rows = self._pair(shop, sku, t)
        if not rows:
            return None
        i, last = self._segment(rows)
        if t - last > IDLE:
            return None
        ref = rows[i]["amount"]
        for s in rows[i + 1:]:
            if abs(s["amount"] - ref) > PRICE_BAND * ref:
                ref = s["amount"]
        return ref

    def answer(self, q):
        return getattr(self, q["kind"])(**q["args"])
