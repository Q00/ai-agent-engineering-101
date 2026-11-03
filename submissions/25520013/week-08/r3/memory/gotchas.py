"""Layer 2': failure-reason index (report 4.3). Pure in-memory; the runner feeds it ledger events.

    G = Gotchas(catalog)                 # catalog: sku -> {category, name, list_price} (optional)
    G.ingest(ev) -> bool                 # True if ev added records; the same event (seq, key, market ledger #) once
    G.query(t0, t1, shop="s1")           # records with t0 <= ts < t1; a window is required
    G.memos(t0, t1)                      # per (reason, shop, stage): count, first_ts, last_ts, example_seq
    G.last_failure_before(t, **f)        # latest record with ts < t, else ABSTAIN
    G.first_of(["mismatch", "over_cap"], t0, t1)   # which value occurred first in the window, else ABSTAIN

Failure events indexed (ledger.py, hooks/market_buyer.py):
    fail/market   stage refund (detail: market entry) or result (detail: failed checks); fail/hook stage a2a
    reject/payments   the kit did not pay; reason = reason_code
    broken/market, broken/broken   goods broke after the sale
    decision/hook (offer_selection)   one record per tried shop that ended without a deal; candidates 0 -> one record
Record: {seq, ts, type, src, stage, reason, shop, sku, category, grade, hour, order, detail}. hour is the hour of
day in UTC+tz_hours (default 9, KST). Free-text reasons have digit runs replaced by N so they group.
"""

import bisect
import re

from memory import ABSTAIN

KEYS = ("type", "stage", "reason", "shop", "sku", "category", "grade", "hour")
FIELDS = KEYS + ("seq", "ts", "src", "order", "detail")


def _text(x):
    return re.sub(r"\d+", "N", str(x))[:80] if x not in (None, "") else None


def _window(t0, t1):
    if t0 is None or t1 is None or t0 > t1:
        raise ValueError(f"a time window [t0, t1) is required, got [{t0}, {t1})")


def identities(ev):
    """Ways the same event can come back: ledger seq, ledger key, and the market ledger # of a market event."""
    d = ev.get("data") or {}
    out = []
    if ev.get("seq") is not None:
        out.append(f"seq:{ev['seq']}")
    if ev.get("key"):
        out.append(f"key:{ev['key']}")
    if ev.get("src") == "market" and d.get("ledger") is not None:
        out.append(f"market:{ev.get('type')}:{d['ledger']}")
    return out


class Gotchas:
    def __init__(self, catalog=None, tz_hours=9):
        self.catalog = catalog or {}
        self.tz_hours = tz_hours
        self.records = []  # sorted by (ts, seq)
        self.seen = set()

    def _rows(self, ev):
        t, src, d = ev.get("type"), ev.get("src"), ev.get("data") or {}
        if t == "fail":
            det = d.get("detail")
            dd = det if isinstance(det, dict) else {}
            reason = (
                d.get("reason_code")
                or _text(d.get("reason"))
                or _text(dd.get("reason"))
            )
            if not reason and isinstance(det, list):
                reason = "+".join(sorted(map(str, det))) or None
            shop = d.get("shop") or dd.get("shop")
            if (
                not shop
                and d.get("stage") == "a2a"
                and str(ev.get("key", "")).count(":") >= 2
            ):
                shop = ev["key"].split(":", 2)[2]  # key a2a:<order>:<shop url>
            return [
                dict(
                    stage=d.get("stage"),
                    reason=reason or d.get("stage"),
                    shop=shop,
                    sku=d.get("sku")
                    or d.get("item")
                    or dd.get("sku")
                    or dd.get("item"),
                    grade=d.get("grade") or dd.get("grade"),
                    order=d.get("order") or dd.get("order"),
                    category=d.get("category") or dd.get("category"),
                    detail=_short(det),
                )
            ]
        if t == "reject":
            return [
                dict(
                    stage="payment",
                    reason=d.get("reason_code") or _text(d.get("reason")) or "other",
                    shop=d.get("shop"),
                    sku=d.get("item") or d.get("sku"),
                    order=d.get("order"),
                    detail=_short(d.get("reason")),
                )
            ]
        if t == "broken":
            return [
                dict(
                    stage="broken",
                    reason="broken",
                    shop=d.get("shop"),
                    sku=d.get("sku"),
                    order=d.get("order"),
                    detail=_short(d.get("name")),
                )
            ]
        if t == "decision" and d.get("what") == "offer_selection":
            rows = [
                dict(
                    stage=x.get("state") or "error",
                    reason=_text(x.get("why") or x.get("error") or x.get("state")),
                    shop=x.get("shop"),
                    sku=x.get("sku"),
                    detail=_short(x.get("why") or x.get("error")),
                )
                for x in d.get("tried") or []
                if x.get("state") != "TASK_STATE_COMPLETED"
            ]
            if not d.get("candidates"):
                rows.append(dict(stage="no_candidates", reason="no_candidates"))
            order = str(ev.get("key", "")).removeprefix("buy:") or None
            return [dict(r, order=order) for r in rows]
        return []

    def ingest(self, ev):
        ids = identities(ev)
        ts = ev.get("ts")
        if not isinstance(ts, (int, float)) or any(i in self.seen for i in ids):
            return False
        rows = self._rows(ev)
        if not rows:
            return False
        self.seen.update(ids)
        hour = int(((ts + self.tz_hours * 3600) % 86400) // 3600)
        for r in rows:
            rec = {k: None for k in FIELDS}
            rec.update(
                r,
                seq=ev.get("seq"),
                ts=ts,
                type=ev["type"],
                src=ev.get("src"),
                hour=hour,
            )
            if rec["category"] is None and rec["sku"] in self.catalog:
                rec["category"] = (self.catalog[rec["sku"]] or {}).get("category")
            bisect.insort(self.records, rec, key=lambda x: (x["ts"], x["seq"] or 0))
        return True

    def _match(self, rows, filters):
        bad = set(filters) - set(FIELDS)
        if bad:
            raise TypeError(f"unknown filter keys {sorted(bad)}; use {FIELDS}")
        return [r for r in rows if all(r[k] == v for k, v in filters.items())]

    def query(self, t0, t1, **filters):
        _window(t0, t1)
        lo = bisect.bisect_left(self.records, t0, key=lambda x: x["ts"])
        hi = bisect.bisect_left(self.records, t1, key=lambda x: x["ts"])
        return [dict(r) for r in self._match(self.records[lo:hi], filters)]

    def memos(self, t0, t1, **filters):
        out = {}
        for r in self.query(t0, t1, **filters):
            m = out.setdefault(
                (r["reason"], r["shop"], r["stage"]),
                {
                    "reason": r["reason"],
                    "shop": r["shop"],
                    "stage": r["stage"],
                    "count": 0,
                    "first_ts": r["ts"],
                    "last_ts": r["ts"],
                    "example_seq": r["seq"],
                },
            )
            m["count"] += 1
            m["last_ts"] = r["ts"]
        return sorted(out.values(), key=lambda m: (-m["count"], m["first_ts"]))

    def last_failure_before(self, t, **filters):
        hi = bisect.bisect_left(self.records, t, key=lambda x: x["ts"])
        rows = self._match(self.records[:hi], filters)
        return dict(rows[-1]) if rows else ABSTAIN

    def first_of(self, values, t0, t1, key="reason", **filters):
        """Which of `values` (matched against record field `key`) occurred first in [t0, t1), else ABSTAIN."""
        values = set(values)
        if key not in FIELDS:
            raise TypeError(f"unknown key {key!r}")
        return next(
            (r[key] for r in self.query(t0, t1, **filters) if r[key] in values), ABSTAIN
        )

    def to_json(self):
        return {
            "version": 1,
            "tz_hours": self.tz_hours,
            "records": [dict(r) for r in self.records],
            "seen": sorted(self.seen),
        }

    @classmethod
    def from_json(cls, d, catalog=None):
        g = cls(catalog, d.get("tz_hours", 9))
        g.records = sorted(
            (dict(r) for r in d.get("records") or []),
            key=lambda x: (x["ts"], x["seq"] or 0),
        )
        g.seen = set(d.get("seen") or [])
        return g


def _short(x):
    return None if x is None else (x if isinstance(x, str) else str(x))[:200]
