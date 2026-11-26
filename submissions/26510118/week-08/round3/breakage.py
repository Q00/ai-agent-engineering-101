"""One breakage table for the shop and the buyer (11/7 01:30, the user's call "적용").

Goods break within 6 hours of the sale with a chance the market does not publish, and the chance is set by the grade:
over 24 h on 11/7 the market's goods broke A 2.6%, B 11.1%, C 29.9%, the same within noise in every category (A
2.0-4.4%, B 9.4-11.9%, C 28-37%). Three parts used their own numbers: the shop's floors and the line's expected
margin pricing.BREAK (about right), and the buyer BUYER_BREAK, A 5.5%, B 8.5%, C 11%, from breakdowns it matched to its
purchases by product name, which missed most: by order on the public ledger its goods broke A 2.8%, B 13.6%, C 29.0%.
So it paid more for B and C than its own least share allows (a C ceiling of 0.63 of list where 0.54 keeps it), and the
shares kept that objective.py and the haggling experiment read left breaks out.

Every 20 minutes run() counts, from the collected ledger (settles and broken entries), the goods settled between
WINDOW and SETTLE_AGE ago (a unit sold since may still break) and which of them broke, by grade: the market's and our
buyer's own (settles whose buyer is ours), with our buyer's broken orders. analysis/breakage.json {"at", "market",
"market_n", "ours", "ours_n", "our_broken_orders"}. rates() gives a side's rates while the table is FRESH, our buyer's
own where it has OURS_MIN purchases of the grade, the market's where it has MARKET_MIN, else DEFAULT.
"""
import time

import common
import ledger

WINDOW = 24 * 3600
SETTLE_AGE = 6 * 3600        # a unit sold more recently may still break: left out of the rates
FRESH = 3 * 3600
OURS_MIN, MARKET_MIN = 30, 100
DEFAULT = {"A": 0.026, "B": 0.111, "C": 0.30}
GRADES = ("A", "B", "C")


def path():
    return common.MARKET_DIR / "analysis" / "breakage.json"


def compute(settles: list, broken: list, catalog: dict, now: float) -> dict:
    gone = {b.get("unit") for b in broken if b.get("unit")}
    out = {"at": int(now), "market": {}, "market_n": {}, "ours": {}, "ours_n": {}}
    count = {side: {g: [0, 0] for g in GRADES} for side in ("market", "ours")}
    for s in settles:
        ts = s.get("ts") or 0
        if not (now - WINDOW <= ts <= now - SETTLE_AGE) or s.get("grade") not in GRADES:
            continue
        if (catalog.get(s.get("item")) or {}).get("kind") != "goods":
            continue
        hit = s.get("unit") in gone
        for side in ("market",) + (("ours",) if common.is_ours(s.get("buyer") or "") else ()):
            count[side][s["grade"]][0] += 1
            count[side][s["grade"]][1] += hit
    for side in ("market", "ours"):
        for g in GRADES:
            n, b = count[side][g]
            out[side + "_n"][g] = n
            out[side][g] = round(b / n, 4) if n else None
    out["our_broken_orders"] = sorted({b.get("order") for b in broken if b.get("order") and common.is_ours(b.get("buyer") or "")})[-5000:]
    return out


def run(now: float | None = None) -> str:
    now = now or time.time()
    since = now - WINDOW - 3600
    table = compute(common.read_jsonl(ledger.folder() / "settles.jsonl", since=since),
                    common.read_jsonl(ledger.folder() / "broken.jsonl", since=since), common.catalog(), now)
    common.write_json(path(), table)
    fmt = lambda side: ", ".join(f"{g} {100 * (table[side][g] or 0):.1f}% of {table[side + '_n'][g]}" for g in GRADES)
    return f"goods breakage, market: {fmt('market')}; our buyer: {fmt('ours')}; {len(table['our_broken_orders'])} broken orders of ours"


def rates(table: dict | None, side: str = "market", now: float | None = None) -> dict:
    """{grade: chance a goods unit breaks} for "market" (the shop) or "ours" (the buyer)."""
    now = now or time.time()
    if not isinstance(table, dict) or now - (table.get("at") or 0) > FRESH:
        return dict(DEFAULT)
    out = {}
    for g in GRADES:
        mine = (table.get("ours") or {}).get(g) if (table.get("ours_n") or {}).get(g, 0) >= OURS_MIN else None
        market = (table.get("market") or {}).get(g) if (table.get("market_n") or {}).get(g, 0) >= MARKET_MIN else None
        v = mine if side == "ours" and mine is not None else market
        out[g] = v if isinstance(v, (int, float)) and 0 <= v < 0.9 else DEFAULT[g]
    return out


def our_broken(table: dict | None) -> set:
    return set((table or {}).get("our_broken_orders") or []) if isinstance(table, dict) else set()
