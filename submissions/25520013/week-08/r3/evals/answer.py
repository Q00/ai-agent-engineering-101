"""v1.18 ANSWER side of evals.memory_eval: the same questions answered by querying the memory layers
(memory.graph, memory.prices, memory.gotchas), and the broken variants that each knock out one ability.

    A = Answerer(events, catalog, variant="full")
    value, payload = A.ask(q)        # payload: what the layer returned (evals.context_eff measures it)

Variants (VARIANTS: name -> the ability it is meant to break):
    no_at        TR  the graph ignores `at` and answers from the current state (swept at the last event)
    no_update    KU  the graph turns its edge UPDATE / DELETE ops into NOOPs (stale reference price, old trajectory)
    no_refund    KU  the graph ignores market refunds (refunded settles keep counting)
    no_abstain   AB  ABSTAIN is replaced by a default guess (0, {}, None, "unknown")
    first_only   IE  prices.last_before and gotchas.last_failure_before return the oldest match, not the latest
    one_session  MS  windowed queries (gotchas, prices, buyer trajectory) see only the last 24 h before the end
Collateral damage on other abilities is allowed; the eval only requires full >= variant everywhere.
"""

from memory import ABSTAIN as MEM_ABSTAIN
from memory.gotchas import Gotchas
from memory.graph import EDGE_KINDS, Graph
from memory.prices import Prices

from evals import ABSTAIN

assert ABSTAIN == MEM_ABSTAIN, "evals.ABSTAIN must equal memory.ABSTAIN"

DAY = 86400
VARIANTS = {
    "no_at": "TR",
    "no_update": "KU",
    "no_refund": "KU",
    "no_abstain": "AB",
    "first_only": "IE",
    "one_session": "MS",
}
GUESS = {"price_last": 0, "last_cat": None, "last_failure": "unknown", "top_cats": [], "fail_count": 0,
         "trade_count": 0, "taste_at": {}, "fav_at": None, "broken_at": 0, "ref_price": None}


class NoAtGraph(Graph):
    def active_edges(self, kind, at=None):
        return super().active_edges(kind)

    def buyer_taste(self, buyer, at=None):
        return super().buyer_taste(buyer)

    def buyer_trajectory(self, buyer, at=None):
        return super().buyer_trajectory(buyer)

    def shop_broken(self, at=None):
        return super().shop_broken()


class NoUpdateGraph(Graph):
    def _apply(self, op, kind, key, ts, evidence, attrs=None, reason="", overridden=False, a=None, b=None):
        if op in ("UPDATE", "DELETE") and kind in EDGE_KINDS:
            op, attrs = "NOOP", None
        return super()._apply(op, kind, key, ts, evidence, attrs, reason, overridden, a, b)


class NoRefundGraph(Graph):
    def _refund(self, ev, d):
        return False


class OneSessionGraph(Graph):
    def buyer_trajectory(self, buyer, at=None):
        rows = super().buyer_trajectory(buyer, at)
        lo = (self.t_hi if at is None else at) - DAY
        return rows if rows == ABSTAIN else [r for r in rows if r[0] >= lo]


class FirstOnlyPrices(Prices):
    def last_before(self, shop, sku, t):
        rows = [r for r in self.pairs.get((shop, sku)) or () if r["ts"] < t]
        return rows[0]["price"] if rows else ABSTAIN


class FirstOnlyGotchas(Gotchas):
    def last_failure_before(self, t, **filters):
        rows = self._match([r for r in self.records if r["ts"] < t], filters)
        return dict(rows[0]) if rows else ABSTAIN


class OneSessionPrices(Prices):
    def between(self, shop, sku, t0, t1):
        return super().between(shop, sku, max(t0, t1 - DAY), t1)


class OneSessionGotchas(Gotchas):
    def query(self, t0, t1, **filters):
        return super().query(max(t0, t1 - DAY), t1, **filters)


LAYERS = {
    "full": (Graph, Prices, Gotchas),
    "no_at": (NoAtGraph, Prices, Gotchas),
    "no_update": (NoUpdateGraph, Prices, Gotchas),
    "no_refund": (NoRefundGraph, Prices, Gotchas),
    "no_abstain": (Graph, Prices, Gotchas),
    "first_only": (Graph, FirstOnlyPrices, FirstOnlyGotchas),
    "one_session": (OneSessionGraph, OneSessionPrices, OneSessionGotchas),
}


class Answerer:
    def __init__(self, events, catalog, variant="full"):
        if variant not in LAYERS:
            raise ValueError(f"unknown variant {variant!r}; use {sorted(LAYERS)}")
        G, P, F = LAYERS[variant]
        self.variant = variant
        self.g, self.p, self.f = G(catalog, me_buyer="me"), P(catalog), F(catalog)
        for ev in events:
            for layer in (self.g, self.p, self.f):
                layer.ingest(ev)
        if events:
            self.g.sweep(max(e["ts"] for e in events))

    # --- one method per question kind: (answer, payload the layer returned)
    def price_last(self, shop, sku, t):
        v = self.p.last_before(shop, sku, t)
        return v, v

    def last_cat(self, buyer, t):
        tr = self.g.buyer_trajectory(buyer, at=t)
        return (tr if tr == ABSTAIN else (tr[-1][1] if tr else None)), tr

    def last_failure(self, shop, t):
        r = self.f.last_failure_before(t, shop=shop)
        return (r if r == ABSTAIN else r["reason"]), r

    def top_cats(self, buyer, t, k=2):
        tr = self.g.buyer_trajectory(buyer, at=t)
        if tr == ABSTAIN:
            return ABSTAIN, tr
        n = {}
        for _, c, _, _ in tr:
            n[c] = n.get(c, 0) + 1
        return [c for c, _ in sorted(n.items(), key=lambda x: (-x[1], x[0]))[:k]], tr

    def fail_count(self, shop, t0, t1):
        m = self.f.memos(t0, t1, shop=shop)
        return sum(x["count"] for x in m), m

    def trade_count(self, shop, sku, t0, t1):
        v = self.p.count(shop, sku, t0, t1)
        return v, v

    def taste_at(self, buyer, t):
        v = self.g.buyer_taste(buyer, at=t)
        return v, v

    def fav_at(self, buyer, t):
        v = self.g.buyer_taste(buyer, at=t)
        if v == ABSTAIN or not v:
            return (v if v == ABSTAIN else None), v
        return min(v.items(), key=lambda x: (-x[1], x[0]))[0], v

    def broken_at(self, shop, t):
        v = self.g.shop_broken(at=t)
        return v.get(shop, 0), v

    def ref_price(self, shop, sku, t):
        rows = self.g.active_edges("shop_sku", at=t)
        if rows == ABSTAIN:
            return ABSTAIN, rows
        row = next((r for r in rows if r["key"] == f"{shop}|{sku}"), None)
        return (row["attrs"]["ref_price"] if row else None), row

    def ask(self, q):
        v, payload = getattr(self, q["kind"])(**q["args"])
        if self.variant == "no_abstain" and v == ABSTAIN:
            v = GUESS[q["kind"]]
        return v, payload

    def answer(self, q):
        return self.ask(q)[0]
