"""Layer 3: Mem0-style relation graph with rule-chosen ops (report 4.1). Pure in-memory; memory.run feeds it.

    G = Graph(catalog)                         # catalog: sku -> {category, name, list_price}
    G.ingest(ev) -> bool                       # one events.jsonl row; each seq once, in seq order
    G.sweep(now)                               # DELETE the trajectories idle for more than inactive_h
    G.propose(p, lookup=None, now=None)        # an op proposed by memo_proposer (or anyone) through the rule gate
    G.active_edges(kind, at=None)              # [{kind, key, a, b, valid_from, valid_to, attrs}]
    G.buyer_taste(buyer, at=None)              # {category: settles in the buyer's current trajectory}
    G.active_buyers(since_ts, at=None)         # {buyer: settles with since_ts <= ts < at}
    G.buyer_trajectory(buyer, at=None)         # [(ts, category, sku, seq)], time order
    G.shop_broken(at=None)                     # {shop: broken units}, the decision path's reputation count
    G.flush_ops(path); G.to_json(); Graph.from_json(d, catalog=None)

Edges, keyed "<a>|<b>": "buyer_cat" (buyer x category, from market settles; category from the catalog, else the
settle's own) and "shop_sku" (shop x SKU: settles, broken units, refunds, last and reference price); "memo" holds
evidence memos "<subject>|<predicate>" -> value. Each key keeps a list of versions {from, to, attrs, ev}; at most
one is open (to None). Nothing is removed: UPDATE and DELETE set `to` on the open version, UPDATE then opens a new
one with from = the same ts. Settle observations are invalidated (to = refund ts), never dropped, by a market
refund or an attributed breakage under warranty; that is logged as DELETE of kind "settle_obs".

Op rule, fixed priority, the same for ingest and for proposals:
 1 NOOP, rejected: evidence missing, not ledger seqs, outside the seqs seen, or not about the key (proposals)
 2 DELETE: the open version's trajectory ended, no settle for more than inactive_h (24 h) (buyer_cat: no settle
   of that buyer in any category; shop_sku: of that shop x SKU). to = last settle ts + inactive_h, so the result
   does not depend on when the sweep ran. A buyer who comes back gets a fresh ADD.
 3 ADD: no open version: the first settle of the buyer in that category (shop x SKU) in the current trajectory.
   Edges are ADDed by ingest only; a proposal cannot create one.
 4 UPDATE: a semantic change. buyer_cat: the favourite flag flips (fav: count >= fav_min and count >= fav_share of
   the buyer's settles in the current trajectory; checked for all the buyer's edges on every settle and refund).
   shop_sku: a settle price outside +-price_band of the version's reference price. memo: the value changed.
 5 NOOP: anything else. Counters change in place (refunded settles are not counted) and the evidence seq goes to
   the version's bounded list (EV_KEEP); in-place NOOPs are not written to the op log.
A proposal whose op differs from the rule's is applied as the rule's op and logged overridden: true. A memo about
"buyer:X" / "shop:X" is DELETEd (not ADDed) when X has no open edge. A memo about "exp:..." / "policy:..." (icrl
lessons, memory/lessons.py) cites lesson ids (< 0), never ledger seqs, resolved through `lookup`.

Queries with `at` use only what has ts < at: a version is open at `at` when from < at <= to, an observation counts
when ts < at and it was not refunded before at, and a trajectory idle for more than inactive_h before `at` counts
as ended even if no sweep has run yet. at=None: the current state. Before the first settle seen (at <= start), or
for a buyer with no settle before `at`: ABSTAIN.

shop_broken() follows reputation.py exactly (same broken_pair pairing, dedupe keys, aliases and own-shop skip), so it
equals Reputation's per-shop broken counts for the same events (tests/test_memory_graph.py).
Op log row: {ts, op, kind, key, evidence, overridden, reason}; reason starts with "proposal:" for proposals.
"""

import json
import math
import os

from owner import norm_name
from reputation import KEEP, broken_pair

from memory import ABSTAIN
from memory.lessons import KINDS as LESSON_KINDS

EDGE_KINDS = ("buyer_cat", "shop_sku")
KINDS = EDGE_KINDS + ("memo",)
OPS = ("ADD", "UPDATE", "DELETE", "NOOP")
EV_KEEP = 20
DEFAULTS = {"inactive_h": 24, "fav_min": 3, "fav_share": 0.2, "price_band": 0.10}
INF = float("inf")


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _seq(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _bounded(d):
    while len(d) > KEEP:
        d.pop(next(iter(d)))


class Graph:
    def __init__(self, catalog=None, me_buyer=None, own_names=(), **cfg):
        self.catalog = catalog or {}
        self.cfg = dict(DEFAULTS, **cfg)
        self.me_buyer = (
            norm_name(me_buyer or os.environ.get("R3_BUYER_NAME") or "") or None
        )
        self.own = {norm_name(n) for n in own_names} - {""}
        self.edges = {k: {} for k in KINDS}  # kind -> key -> {a, b, v: [versions]}
        self.obs = {}  # str(seq) -> market settle observation
        self.brk = []  # counted breakages [ts, shop, sku, seq]
        self.rep = {
            "seen": {},
            "alias": {},
            "bseen": {},
            "bpair": {},
            "broken": {},
        }  # reputation.py mirror
        self.buyers = {}  # buyer -> {last, seq}: last settle
        self.seq_lo, self.seq_hi, self.t_hi, self.start = None, 0, None, None
        self.ops = []  # op log rows not yet written
        self._index()

    @property
    def idle(self):
        return self.cfg["inactive_h"] * 3600

    # --- indexes (rebuilt on load, not stored)
    def _index(self):
        self._by_buyer, self._by_ss, self._by_ledger, self._by_order, self._bedges = (
            {},
            {},
            {},
            {},
            {},
        )
        for o in self.obs.values():
            self._add_index(o)
        for key, e in self.edges["buyer_cat"].items():
            self._bedges.setdefault(e["a"], set()).add(key)

    def _add_index(self, o):
        self._by_buyer.setdefault(o["buyer"], []).append(o)
        self._by_ss.setdefault(f"{o['shop']}|{o['sku']}", []).append(o)
        if o["ledger"] is not None:
            self._by_ledger[str(o["ledger"])] = o
        if o["order"] is not None:
            self._by_order[str(o["order"])] = o

    def _canon(self, shop):
        return self.rep["alias"].get(shop, shop)

    # --- ops
    def _open(self, kind, key):
        e = self.edges[kind].get(key)
        return e["v"][-1] if e and e["v"][-1]["to"] is None else None

    def _apply(
        self,
        op,
        kind,
        key,
        ts,
        evidence,
        attrs=None,
        reason="",
        overridden=False,
        a=None,
        b=None,
    ):
        v = self._open(kind, key) if kind in KINDS else None
        if op in ("UPDATE", "DELETE") and v is not None:
            v["to"] = ts
        if op in ("ADD", "UPDATE"):
            e = self.edges[kind].setdefault(key, {"a": a, "b": b, "v": []})
            e["v"].append(
                {
                    "from": ts,
                    "to": None,
                    "attrs": attrs,
                    "ev": list(evidence)[-EV_KEEP:],
                }
            )
            if kind == "buyer_cat":
                self._bedges.setdefault(e["a"], set()).add(key)
        row = {
            "ts": ts,
            "op": op,
            "kind": kind,
            "key": key,
            "evidence": list(evidence or [])[:EV_KEEP],
            "overridden": overridden,
            "reason": reason,
        }
        self.ops.append(row)
        return row

    @staticmethod
    def _note(v, seq):
        v["ev"] = (v["ev"] + [seq])[-EV_KEEP:]

    # --- ingest
    def ingest(self, ev):
        s = ev.get("seq")
        if not _seq(s) or s <= self.seq_hi:
            return False
        self.seq_lo = s if self.seq_lo is None else self.seq_lo
        self.seq_hi = s
        ts, t, src, d = (
            ev.get("ts"),
            ev.get("type"),
            ev.get("src"),
            ev.get("data") or {},
        )
        if _num(ts):
            self.t_hi = ts if self.t_hi is None else max(self.t_hi, ts)
        shop = d.get("shop")
        if shop and norm_name(shop) in self.own:
            return False
        if t == "settle" and src in ("market", "deal") and shop:
            self._rep_settle(d)
            return self._observe(ev, d) if src == "market" else True
        if t == "broken":
            return self._broken(ev, d)
        if t == "fail" and d.get("stage") == "refund":
            return self._refund(ev, d)
        return False

    def _observe(self, ev, d):
        ts, sku = ev.get("ts"), d.get("sku")
        if not _num(ts) or not sku:
            return False
        self.start = ts if self.start is None else min(self.start, ts)
        o = {
            "seq": ev["seq"],
            "ts": ts,
            "buyer": d.get("buyer"),
            "sku": sku,
            "shop": self._canon(d["shop"]),
            "cat": (self.catalog.get(sku) or {}).get("category") or d.get("category"),
            "ledger": d.get("ledger"),
            "order": d.get("order"),
            "amount": d.get("amount") if _seq(d.get("amount")) else None,
            "to": None,
        }
        self.obs[str(o["seq"])] = o
        self._add_index(o)
        self._shop_sku(o)
        if o["buyer"] and o["cat"]:
            self._buyer_cat(o)
        return True

    def _end_buyer(self, buyer, reason="trajectory ended", overridden=False, only=None):
        b = self.buyers[buyer]
        for key in sorted(self._bedges.get(buyer, ())):
            if self._open("buyer_cat", key) is not None and only in (None, key):
                self._apply(
                    "DELETE",
                    "buyer_cat",
                    key,
                    b["last"] + self.idle,
                    [b["seq"]],
                    reason=f"{reason}: no settle for {self.cfg['inactive_h']} h",
                    overridden=overridden,
                )

    def _buyer_cat(self, o):
        buyer, ts = o["buyer"], o["ts"]
        b = self.buyers.get(buyer)
        if b and ts > b["last"] + self.idle:
            self._end_buyer(buyer)
        key = f"{buyer}|{o['cat']}"
        v = self._open("buyer_cat", key)
        if v is None:
            self._apply(
                "ADD",
                "buyer_cat",
                key,
                ts,
                [o["seq"]],
                {"since": ts, "n": 1, "fav": False},
                "first settle of the buyer in this category",
                a=buyer,
                b=o["cat"],
            )
        else:
            v["attrs"]["n"] += 1
            self._note(v, o["seq"])
        self.buyers[buyer] = {"last": max(ts, b["last"]) if b else ts, "seq": o["seq"]}
        self._refav(buyer, ts, o["seq"])

    def _refav(self, buyer, ts, seq):
        vs = [
            (k, v)
            for k in sorted(self._bedges.get(buyer, ()))
            if (v := self._open("buyer_cat", k))
        ]
        total = sum(v["attrs"]["n"] for _, v in vs)
        for k, v in vs:
            n = v["attrs"]["n"]
            fav = n >= self.cfg["fav_min"] and n >= self.cfg["fav_share"] * total
            if fav != v["attrs"]["fav"]:
                self._apply(
                    "UPDATE",
                    "buyer_cat",
                    k,
                    ts,
                    [seq],
                    dict(v["attrs"], fav=fav),
                    f"favourite set changed: {'now' if fav else 'no longer'} a favourite ({n}/{total})",
                )

    def _shop_sku(self, o):
        key, ts, amt = f"{o['shop']}|{o['sku']}", o["ts"], o["amount"]
        v = self._open("shop_sku", key)
        if v is not None and ts > v["attrs"]["last"] + self.idle:
            self._apply(
                "DELETE",
                "shop_sku",
                key,
                v["attrs"]["last"] + self.idle,
                [v["attrs"]["last_seq"]],
                reason=f"trajectory ended: no settle for {self.cfg['inactive_h']} h",
            )
            v = None
        if v is None:
            self._apply(
                "ADD",
                "shop_sku",
                key,
                ts,
                [o["seq"]],
                {
                    "since": ts,
                    "last": ts,
                    "last_seq": o["seq"],
                    "settles": 1,
                    "broken": 0,
                    "refunds": 0,
                    "last_price": amt,
                    "ref_price": amt,
                },
                "first settle of the shop for this SKU",
                a=o["shop"],
                b=o["sku"],
            )
            return
        at = v["attrs"]
        ref = at["ref_price"]
        moved = (
            amt is not None and ref and abs(amt - ref) > self.cfg["price_band"] * ref
        )
        new = dict(
            at,
            settles=at["settles"] + 1,
            last=max(ts, at["last"]),
            last_seq=o["seq"],
            last_price=amt if amt is not None else at["last_price"],
            ref_price=amt if moved or ref is None else ref,
        )
        if moved:
            self._apply(
                "UPDATE",
                "shop_sku",
                key,
                ts,
                [o["seq"]],
                new,
                f"price {amt} outside +-{self.cfg['price_band']:.0%} of {ref}",
            )
        else:
            v["attrs"] = new
            self._note(v, o["seq"])

    def sweep(self, now):
        """DELETE every open trajectory idle for more than inactive_h at `now` (op ts = last settle + inactive_h)."""
        for buyer, b in sorted(self.buyers.items()):
            if now > b["last"] + self.idle:
                self._end_buyer(buyer)
        for key, e in sorted(self.edges["shop_sku"].items()):
            v = e["v"][-1]
            if v["to"] is None and now > v["attrs"]["last"] + self.idle:
                self._apply(
                    "DELETE",
                    "shop_sku",
                    key,
                    v["attrs"]["last"] + self.idle,
                    [v["attrs"]["last_seq"]],
                    reason=f"trajectory ended: no settle for {self.cfg['inactive_h']} h",
                )

    # --- breakage, the reputation.py way (see Reputation.update)
    def _rep_settle(self, d):
        r, shop, led = self.rep, d["shop"], d.get("ledger")
        if led is not None and str(led) in r["seen"]:
            first = r["seen"][str(led)]
            if first != shop and shop not in r["alias"]:
                r["alias"][shop] = first
            return
        if led is not None:
            r["seen"][str(led)] = self._canon(shop)
            _bounded(r["seen"])

    def _ours(self, ev, d):
        if self.me_buyer is not None:
            return norm_name(d.get("buyer") or "") == self.me_buyer
        return ev.get("mine") is True

    def _broken(self, ev, d):
        r, market, led = self.rep, ev.get("src") == "market", d.get("ledger")
        if not (market or d.get("attributed")):
            return False
        key = (
            (f"market:{led}" if led is not None else f"market:{ev.get('key')}")
            if market
            else f"attributed:{ev.get('key')}"
        )
        if key in r["bseen"]:
            return False
        r["bseen"][key] = 1
        _bounded(r["bseen"])
        if (
            not market
            and d.get("under_warranty")
            and led is not None
            and str(led) in self._by_ledger
        ):
            self._invalidate(self._by_ledger[str(led)], ev, "breakage under warranty")
        sku = d.get("sku") or d.get("item")
        if broken_pair(
            r["bpair"],
            "m" if market else "a",
            sku,
            ev.get("ts"),
            (not market) or self._ours(ev, d),
        ):
            return True
        shop = d.get("shop")
        if not shop and not market and led is not None:
            shop = r["seen"].get(str(led))
        if not shop:
            return True
        c = self._canon(shop)
        r["broken"][c] = r["broken"].get(c, 0) + 1
        self.brk.append([ev.get("ts"), c, sku, ev["seq"]])
        v = self._open("shop_sku", f"{c}|{sku}")
        if v is not None:
            v["attrs"]["broken"] += 1
            self._note(v, ev["seq"])
        return True

    # --- refunds
    def _refund(self, ev, d):
        det = d.get("detail") if isinstance(d.get("detail"), dict) else {}
        o = self._refund_target(det, ev.get("ts"))
        if o is None:
            self._apply(
                "NOOP",
                "settle_obs",
                "?",
                ev.get("ts"),
                [ev["seq"]],
                reason="refund of no known settle",
            )
            return False
        self._invalidate(o, ev, "refund")
        return True

    def _refund_target(self, det, ts):
        """The settle a refund refers to: its market ledger # (settle / settle_ledger / ref), else its order, else
        the latest unrefunded settle of the same shop x SKU (x buyer when given) at or before the refund."""
        for k in ("settle", "settle_ledger", "settle_seq", "ref"):
            if det.get(k) is not None and str(det[k]) in self._by_ledger:
                return self._by_ledger[str(det[k])]
        if det.get("order") is not None and str(det["order"]) in self._by_order:
            return self._by_order[str(det["order"])]
        sku = det.get("sku") or det.get("item")
        if not det.get("shop") or not sku:
            return None
        cands = [
            o
            for o in self._by_ss.get(f"{self._canon(det['shop'])}|{sku}", ())
            if o["to"] is None
            and (not det.get("buyer") or o["buyer"] == det["buyer"])
            and (not _num(ts) or o["ts"] <= ts)
        ]
        return max(cands, key=lambda o: (o["ts"], o["seq"])) if cands else None

    def _invalidate(self, o, ev, why):
        if o["to"] is not None:
            return
        ts = ev.get("ts") if _num(ev.get("ts")) else self.t_hi
        o["to"] = ts
        self._apply(
            "DELETE", "settle_obs", str(o["seq"]), ts, [ev["seq"], o["seq"]], reason=why
        )
        v = self._open("shop_sku", f"{o['shop']}|{o['sku']}")
        if v is not None and o["ts"] >= v["attrs"]["since"]:
            v["attrs"]["refunds"] += 1
            self._note(v, ev["seq"])
        v = self._open("buyer_cat", f"{o['buyer']}|{o['cat']}")
        if v is not None and o["ts"] >= v["attrs"]["since"]:
            v["attrs"]["n"] -= 1
            self._note(v, ev["seq"])
            self._refav(o["buyer"], ts, ev["seq"])

    # --- proposals
    def propose(self, p, lookup=None, now=None):
        """p: {op, kind ("memo" when it has a subject), a + b or subject + predicate, value, evidence: [seq]}.
        lookup(seq) -> event or None, used to check that memo evidence exists and mentions the subject. Returns the
        op log row written (the rule's op)."""
        kind = p.get("kind") or ("memo" if "subject" in p else None)
        a, b = (
            (p.get("subject"), p.get("predicate"))
            if kind == "memo"
            else (p.get("a"), p.get("b"))
        )
        key, want = f"{a}|{b}", str(p.get("op") or "").upper() or None
        ts = now if now is not None else self.t_hi
        evidence = p.get("evidence")
        why = self._evidence_problem(kind, a, b, evidence, lookup)
        if why:
            op, reason = "NOOP", f"rejected: {why}"
        else:
            op, reason = self._rule(kind, key, a, p.get("value"), ts)
        over = want is not None and want != op
        reason = f"proposal: {reason}" + (f" (proposed {want})" if over else "")
        ev = evidence if isinstance(evidence, list) else []
        if op == "DELETE" and kind == "buyer_cat":
            self._end_buyer(a, reason, over, only=key)
            return self.ops[-1]
        if op == "DELETE" and kind == "shop_sku":
            at = self._open(kind, key)["attrs"]
            return self._apply(
                "DELETE",
                kind,
                key,
                at["last"] + self.idle,
                [at["last_seq"]],
                reason=reason,
                overridden=over,
            )
        attrs = {"value": p.get("value")} if op in ("ADD", "UPDATE") else None
        return self._apply(
            op,
            kind if kind in KINDS else str(kind),
            key,
            ts,
            ev,
            attrs,
            reason,
            over,
            a,
            b,
        )

    def _evidence_problem(self, kind, a, b, evidence, lookup):
        if kind not in KINDS:
            return f"unknown kind {kind!r}"
        if not isinstance(a, str) or not a or not isinstance(b, str) or not b:
            return "no key"
        if not isinstance(evidence, list) or not evidence:
            return "no evidence"
        if not all(_seq(s) for s in evidence):
            return "evidence is not ledger seqs"
        if kind == "memo" and a.partition(":")[0] in LESSON_KINDS:
            if lookup is None or not all(s < 0 for s in evidence):
                return "evidence is not lesson ids"
            evs = [lookup(s) for s in evidence]
            if not all(evs):
                return "evidence not in the lessons"
            return None if any(self._mentions(e, a) for e in evs) else "evidence not about the key"
        if self.seq_lo is None or not all(
            self.seq_lo <= s <= self.seq_hi for s in evidence
        ):
            return "evidence not in the ledger"
        if kind == "buyer_cat":
            ok = any(
                (o := self.obs.get(str(s))) and o["buyer"] == a and o["cat"] == b
                for s in evidence
            )
        elif kind == "shop_sku":
            ok = any(
                (o := self.obs.get(str(s)))
                and o["shop"] == self._canon(a)
                and o["sku"] == b
                for s in evidence
            ) or any(
                x[3] in evidence and x[1:3] == [self._canon(a), b] for x in self.brk
            )
        elif lookup is None:
            return None
        else:
            evs = [lookup(s) for s in evidence]
            if not all(evs):
                return "evidence not in the ledger"
            ok = any(self._mentions(e, a) for e in evs)
        return None if ok else "evidence not about the key"

    def _mentions(self, ev, subject):
        d = ev.get("data") or {}
        kind, _, name = subject.partition(":")
        fields = {
            "shop": ("shop",),
            "buyer": ("buyer",),
            "sku": ("sku", "item"),
            "category": ("category",),
        }
        if kind not in fields:
            return subject in json.dumps(d, ensure_ascii=False)
        if any(d.get(f) == name for f in fields[kind]):
            return True
        return (
            kind == "category"
            and (self.catalog.get(d.get("sku") or d.get("item")) or {}).get("category")
            == name
        )

    def _subject_open(self, subject):
        kind, _, name = subject.partition(":")
        if kind == "buyer":
            return any(self._open("buyer_cat", k) for k in self._bedges.get(name, ()))
        if kind == "shop":
            return any(
                e["a"] == name and e["v"][-1]["to"] is None
                for e in self.edges["shop_sku"].values()
            )
        return True

    def _rule(self, kind, key, a, value, ts):
        v = self._open(kind, key)
        if kind == "buyer_cat":
            if (
                v is not None
                and ts is not None
                and ts > self.buyers[a]["last"] + self.idle
            ):
                return "DELETE", "trajectory ended"
            return (
                "NOOP",
                "edge already open" if v else "edges are ADDed by ingest only",
            )
        if kind == "shop_sku":
            if v is not None and ts is not None and ts > v["attrs"]["last"] + self.idle:
                return "DELETE", "trajectory ended"
            return (
                "NOOP",
                "edge already open" if v else "edges are ADDed by ingest only",
            )
        if not self._subject_open(a):
            return (
                ("DELETE", "subject has no open edge")
                if v
                else ("NOOP", "subject has no open edge")
            )
        if v is None:
            return (
                ("ADD", "new memo") if value is not None else ("NOOP", "nothing to add")
            )
        if value is not None and value != v["attrs"]["value"]:
            return "UPDATE", "value changed"
        return "NOOP", "same value"

    # --- queries
    def _no_data(self, at):
        return self.start is None or (at is not None and at <= self.start)

    @staticmethod
    def _version_at(e, at):
        if at is None:
            return e["v"][-1] if e["v"][-1]["to"] is None else None
        return next(
            (
                v
                for v in reversed(e["v"])
                if v["from"] < at and (v["to"] is None or at <= v["to"])
            ),
            None,
        )

    @staticmethod
    def _valid(o, T):
        return o["ts"] < T and (o["to"] is None or o["to"] >= T)

    def _view(self, kind, key, at):
        e = self.edges[kind][key]
        v = self._version_at(e, at)
        if v is None:
            return None
        T = INF if at is None else at
        row = {
            "kind": kind,
            "key": key,
            "a": e["a"],
            "b": e["b"],
            "valid_from": v["from"],
            "valid_to": v["to"],
        }
        if kind == "memo":
            return dict(row, attrs=dict(v["attrs"]))
        a = v["attrs"]
        if kind == "buyer_cat":
            seen = [o for o in self._by_buyer.get(e["a"], ()) if o["ts"] < T]
            mine = [
                o
                for o in seen
                if o["cat"] == e["b"] and o["ts"] >= a["since"] and self._valid(o, T)
            ]
            attrs = {
                "since": a["since"],
                "count": len(mine),
                "fav": a["fav"],
                "first_ts": min((o["ts"] for o in mine), default=None),
                "last_ts": max((o["ts"] for o in mine), default=None),
            }
        else:
            seen = [o for o in self._by_ss.get(key, ()) if a["since"] <= o["ts"] < T]
            last = max(seen, key=lambda o: (o["ts"], o["seq"]), default=None)
            attrs = {
                "since": a["since"],
                "settles": len(seen),
                "refunds": sum(not self._valid(o, T) for o in seen),
                "broken": sum(
                    1
                    for x in self.brk
                    if x[1:3] == [e["a"], e["b"]]
                    and _num(x[0])
                    and a["since"] <= x[0] < T
                ),
                "last_price": last and last["amount"],
                "ref_price": a["ref_price"],
            }
        if at is not None and (not seen or at - max(o["ts"] for o in seen) > self.idle):
            return None  # trajectory ended before `at` (no sweep needed)
        return dict(row, attrs=attrs)

    def active_edges(self, kind, at=None):
        """Edges open at `at` (None: now), each {kind, key, a, b, valid_from, valid_to, attrs}; ABSTAIN before the
        record starts."""
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if self._no_data(at):
            return ABSTAIN
        return [r for k in sorted(self.edges[kind]) if (r := self._view(kind, k, at))]

    def buyer_taste(self, buyer, at=None):
        """{category: settles in the buyer's current trajectory, refunds excluded} at `at`; {} when the trajectory
        ended; ABSTAIN for a buyer with no settle before `at`."""
        if self._no_data(at) or not any(
            o["ts"] < (INF if at is None else at) for o in self._by_buyer.get(buyer, ())
        ):
            return ABSTAIN
        rows = (
            self._view("buyer_cat", k, at) for k in sorted(self._bedges.get(buyer, ()))
        )
        return {r["b"]: r["attrs"]["count"] for r in rows if r and r["attrs"]["count"]}

    def active_buyers(self, since_ts, at=None):
        """{buyer: settles with since_ts <= ts < at, refunded ones excluded}."""
        if self._no_data(at):
            return ABSTAIN
        T, out = INF if at is None else at, {}
        for buyer, os_ in self._by_buyer.items():
            n = sum(1 for o in os_ if o["ts"] >= since_ts and self._valid(o, T))
            if buyer and n:
                out[buyer] = n
        return out

    def buyer_trajectory(self, buyer, at=None):
        """[(ts, category, sku, seq)] of the buyer's market settles with ts < at, time order, refunded ones left out;
        the whole record, not only the current trajectory."""
        T = INF if at is None else at
        if self._no_data(at) or not any(
            o["ts"] < T for o in self._by_buyer.get(buyer, ())
        ):
            return ABSTAIN
        return sorted(
            (o["ts"], o["cat"], o["sku"], o["seq"])
            for o in self._by_buyer[buyer]
            if self._valid(o, T)
        )

    def shop_broken(self, at=None):
        """{shop: broken units} (reputation.py's count); with `at`, breakages with ts < at only."""
        if at is None:
            return dict(self.rep["broken"])
        out = {}
        for ts, shop, _, _ in self.brk:
            if _num(ts) and ts < at:
                out[shop] = out.get(shop, 0) + 1
        return out

    # --- persistence
    def flush_ops(self, path):
        """Append the pending op log rows to `path` (append only); returns how many."""
        n = len(self.ops)
        if n:
            with open(path, "a", encoding="utf-8") as f:
                f.write(
                    "".join(
                        json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n"
                        for r in self.ops
                    )
                )
        self.ops = []
        return n

    def to_json(self):
        return {
            "version": 1,
            "cfg": self.cfg,
            "edges": self.edges,
            "obs": list(self.obs.values()),
            "brk": self.brk,
            "rep": self.rep,
            "buyers": self.buyers,
            "seq_lo": self.seq_lo,
            "seq_hi": self.seq_hi,
            "t_hi": self.t_hi,
            "start": self.start,
        }

    @classmethod
    def from_json(cls, d, catalog=None, **kw):
        g = cls(catalog, **dict(d.get("cfg") or {}, **kw))
        g.edges = {k: dict((d.get("edges") or {}).get(k) or {}) for k in KINDS}
        g.obs = {str(o["seq"]): o for o in d.get("obs") or []}
        g.brk = d.get("brk") or []
        g.rep = dict(g.rep, **(d.get("rep") or {}))
        g.buyers = d.get("buyers") or {}
        g.seq_lo, g.seq_hi, g.t_hi, g.start = (
            d.get("seq_lo"),
            d.get("seq_hi") or 0,
            d.get("t_hi"),
            d.get("start"),
        )
        g._index()
        return g
