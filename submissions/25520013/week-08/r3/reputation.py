"""Shop reputation for the buyer, updated incrementally from the event ledger (own byte cursor, O(new events)).

Uses claim_honest from settle lines (the truth of a grade claim is revealed only after the sale) and broken
events (market lines, or broken.jsonl lines that ledger.ingest_broken attributed to our purchase).
- Each market ledger settle (#N) counts once, whichever source saw it first (market GET, deal.json).
- The same shop can appear as a name (market ledger) and as a URL (deal.json / payments): when two sources report
  the same ledger #N with different shop keys, the later key becomes an alias of the first, and table() returns
  the stats under both keys.
- Our own shops (policy owner.own_shop_names, same GitHub owner) are not rated: they cannot settle with our buyer.
This is a small lead-owned table for the buyer hook; the general aggregates are aggregates.py (ML Engineer).

v1.13 (CR, pre-deploy condition): breakages are counted once per MARKET breakage. The kit's broken.jsonl line is a
copy of a market ledger `broken` row for our buyer (kit broken_news: name + broke_at = int(row ts), nothing else), so:
- a market row is identified by its market ledger seq (the event id, `ledger`), counted once;
- an attributed broken.jsonl row is the same breakage as one not-yet-paired market row of OUR buyer for the same SKU
  whose time is within BROKEN_PAIR_S seconds (one-to-one, nearest first, either arrival order); it counts only when
  no such market row exists (e.g. the market row was never ingested).
v1.12 deduped by purchase instead and counted one breakage twice when the same shop/SKU was bought more than once
(market row -> oldest purchase, broken.jsonl row -> the real order; mock run_212: market 1 -> 2, run_211R 2 -> 4).
reputation.json written by v1.12 (no "v": 2) is rebuilt from the start of the ledger once.
"""
import json
import math
import os

from ledger import Ledger

KEEP = 50_000
STATE_V = 2
BROKEN_PAIR_S = 2.0          # market row ts (float) vs broken.jsonl broke_at (int seconds of the same row)
PAIR_KEEP = 500


def _num(ts):
    return isinstance(ts, (int, float)) and not isinstance(ts, bool) and math.isfinite(ts)


def broken_pair(bp, side, sku, ts, ours=True):
    """One breakage row: side "m" (market ledger row) or "a" (attributed broken.jsonl row, always our buyer).
    Returns True when it is the copy of an unpaired row of the other side (same SKU, |dt| <= BROKEN_PAIR_S, our
    buyer): that pair is used up and the row must not be counted. Else the row is kept as unpaired (when it can
    pair at all) and False is returned. bp = {"m": [...], "a": [...]} (JSON state).
    v1.14 (QA review_r3_01ac_v113 minor 2): a row whose ts is not a finite number (None, bool, NaN) has no time to
    compare, so it pairs with the OLDEST unpaired same-SKU row of the other side whatever its time, and is kept
    (ts None) as a wildcard that a later same-SKU row pairs with when nothing within the window does. reputation.py
    and memory/graph.py (ledger mode) share this function, so both count such a breakage once."""
    bp.setdefault("m", [])
    bp.setdefault("a", [])
    if not ours or not sku:
        return False
    other = bp["a" if side == "m" else "m"]
    t = ts if _num(ts) else None
    best = None
    for i, o in enumerate(other):
        if o["sku"] != sku:
            continue
        if t is None or o["ts"] is None:
            if best is None:                            # oldest wildcard match; a timed match within the window wins
                best = (i, float("inf"))
            continue
        dt = abs(o["ts"] - t)
        if dt <= BROKEN_PAIR_S and (best is None or dt < best[1]):
            best = (i, dt)
    if best is not None:
        other.pop(best[0])
        return True
    lst = bp[side]
    lst.append({"sku": sku, "ts": t})
    del lst[:-PAIR_KEEP]
    return False


def _blank():
    return {"settled": 0, "honest": 0, "claims": 0, "broken": 0}


class Reputation:
    def __init__(self, ledger=None, own_names=()):
        self.L = ledger or Ledger()
        self.file = self.L.root / "reputation.json"
        from owner import norm_name
        self._norm = norm_name
        self.own = {norm_name(n) for n in own_names} - {""}
        try:
            self.s = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.s = {}
        if self.s.get("v") != STATE_V:
            self.s = {"v": STATE_V}                     # v1.13: v1.12 counts are wrong, rebuild from offset 0
        for k, v in (("off", 0), ("shops", {}), ("seen", {}), ("alias", {}), ("bseen", {}), ("bpair", {})):
            self.s.setdefault(k, v)
        self.me_buyer = norm_name(self.L.me_buyer) if getattr(self.L, "me_buyer", None) else None

    def _canon(self, shop):
        return self.s["alias"].get(shop, shop)

    def _bounded(self, d):
        while len(d) > KEEP:
            d.pop(next(iter(d)))

    def update(self):
        n = 0
        for off, e in self.L.iter_from(self.s["off"]):
            self.s["off"] = off
            d = e.get("data") or {}
            shop = d.get("shop")
            if shop and self._norm(shop) in self.own:
                continue
            if e["type"] == "settle" and e["src"] in ("market", "deal") and shop:
                led = d.get("ledger")
                if led is not None and str(led) in self.s["seen"]:
                    first = self.s["seen"][str(led)]
                    if first != shop and shop not in self.s["alias"]:
                        self.s["alias"][shop] = first
                    continue                            # deal and market copies of one settle
                c = self._canon(shop)
                r = self.s["shops"].setdefault(c, _blank())
                r["settled"] += 1
                if d.get("claim") is not None and d.get("claim_honest") is not None:
                    r["claims"] += 1
                    r["honest"] += d.get("claim_honest") is True
                if led is not None:
                    self.s["seen"][str(led)] = c
                    self._bounded(self.s["seen"])
                n += 1
            elif e["type"] == "broken" and (e["src"] == "market" or d.get("attributed")):
                # ML Engineer finding (a): the market `broken` line carries the ledger seq of the BREAK (its event
                # id), the attributed broken.jsonl line the seq of the SETTLE. v1.13: the market row counts once per
                # id; the attributed row is its copy when broken_pair finds the market row (see module doc).
                led = d.get("ledger")
                market = e["src"] == "market"
                key = (f"market:{led}" if led is not None else f"market:{e.get('key')}") if market \
                    else f"attributed:{e.get('key')}"
                if key in self.s["bseen"]:
                    continue                            # the same row seen twice
                self.s["bseen"][key] = 1
                self._bounded(self.s["bseen"])
                ours = (not market) or self._ours(e, d)
                if broken_pair(self.s["bpair"], "m" if market else "a", d.get("sku") or d.get("item"),
                               e.get("ts"), ours):
                    continue                            # the same breakage, already counted from the other source
                if market:
                    led = None                          # a market broken row's ledger is the break's own seq
                if not shop and led is not None:
                    shop = self.s["seen"].get(str(led))
                if shop:
                    self.s["shops"].setdefault(self._canon(shop), _blank())["broken"] += 1
                    n += 1
        tmp = self.file.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.s, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.file)
        return n

    def _ours(self, e, d):
        """a market broken row of OUR buyer (only those have a broken.jsonl copy)"""
        if self.me_buyer is not None:
            return self._norm(d.get("buyer") or "") == self.me_buyer
        return e.get("mine") is True

    def table(self, prior=2):
        """shop -> settled, claim_honest_rate, broken_rate (Laplace prior: new shops start trusted). Aliases
        (URL vs name of the same shop) map to the same row."""
        out = {}
        for k, r in self.s["shops"].items():
            out[k] = {"settled": r["settled"],
                      "claim_honest_rate": (r["honest"] + prior) / (r["claims"] + prior),
                      "broken_rate": r["broken"] / (r["settled"] + prior)}
        for a, c in self.s["alias"].items():
            if c in out:
                out[a] = out[c]
        return out
