"""Layer 2'': per-trade price history (report 5.1). Pure in-memory; only market settle events (type settle,
src market) fill it. Never wired into the decision path.

    H = Prices(catalog)                      # catalog: sku -> {category, name, list_price} (optional)
    H.ingest(ev) -> bool                     # True if a trade was added; the same event (seq, key, ledger #) once
    H.last_before("s1", "sku1", t)           # price of the last settle with ts < t, else ABSTAIN
    H.between("s1", "sku1", t0, t1)          # trades with t0 <= ts < t1, time order
    H.count("s1", "sku1", t0, t1)            # len(between(...))

Trade: {ts, price, seq, ledger, order, buyer, category}, kept per (shop, sku) sorted by (ts, seq), never edited.
ABSTAIN: last_before for an unknown pair or t at or before the pair's first trade; between/count for an unknown
pair or a window ending at or before the first settle the layer saw (before the record starts). Inside the record
an empty window is a real answer: [] / 0.
"""

import bisect

from memory import ABSTAIN
from memory.gotchas import _window, identities


class Prices:
    def __init__(self, catalog=None):
        self.catalog = catalog or {}
        self.pairs = {}  # (shop, sku) -> [trade], sorted by (ts, seq)
        self.seen = set()
        self.start = None  # ts of the earliest settle ingested

    def ingest(self, ev):
        d = ev.get("data") or {}
        ts, price = ev.get("ts"), d.get("amount")
        ids = identities(ev)
        if (
            ev.get("type") != "settle"
            or ev.get("src") != "market"
            or not isinstance(ts, (int, float))
            or not isinstance(price, int)
            or isinstance(price, bool)
            or not d.get("shop")
            or not d.get("sku")
            or any(i in self.seen for i in ids)
        ):
            return False
        self.seen.update(ids)
        tr = {
            "ts": ts,
            "price": price,
            "seq": ev.get("seq"),
            "ledger": d.get("ledger"),
            "order": d.get("order"),
            "buyer": d.get("buyer"),
            "category": d.get("category")
            or (self.catalog.get(d["sku"]) or {}).get("category"),
        }
        bisect.insort(
            self.pairs.setdefault((d["shop"], d["sku"]), []),
            tr,
            key=lambda x: (x["ts"], x["seq"] or 0),
        )
        self.start = ts if self.start is None else min(self.start, ts)
        return True

    def last_before(self, shop, sku, t):
        rows = self.pairs.get((shop, sku))
        if not rows:
            return ABSTAIN
        i = bisect.bisect_left(rows, t, key=lambda x: x["ts"])
        return rows[i - 1]["price"] if i else ABSTAIN

    def between(self, shop, sku, t0, t1):
        _window(t0, t1)
        rows = self.pairs.get((shop, sku))
        if not rows or t1 <= self.start:
            return ABSTAIN
        lo = bisect.bisect_left(rows, t0, key=lambda x: x["ts"])
        hi = bisect.bisect_left(rows, t1, key=lambda x: x["ts"])
        return [dict(r) for r in rows[lo:hi]]

    def count(self, shop, sku, t0, t1):
        rows = self.between(shop, sku, t0, t1)
        return ABSTAIN if rows == ABSTAIN else len(rows)

    def to_json(self):
        return {
            "version": 1,
            "start": self.start,
            "seen": sorted(self.seen),
            "pairs": [
                {"shop": s, "sku": k, "trades": [dict(r) for r in rows]}
                for (s, k), rows in sorted(self.pairs.items())
            ],
        }

    @classmethod
    def from_json(cls, d, catalog=None):
        h = cls(catalog)
        h.start = d.get("start")
        h.seen = set(d.get("seen") or [])
        h.pairs = {
            (p["shop"], p["sku"]): sorted(
                (dict(r) for r in p["trades"]), key=lambda x: (x["ts"], x["seq"] or 0)
            )
            for p in d.get("pairs") or []
        }
        return h
