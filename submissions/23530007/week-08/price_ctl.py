"""Price controller for the shop's goods (yms979 11-11, user: 'balance it dynamically and get both';
'some items need not be cheaper at all, they can cost a little more').

seller_r3 prices a goods unit against the shops we race (rival_price: their median settle for the SKU and grade):
    floor = ref - under + adj(sku)        ask = floor + over            (shares of list; the cost floor still holds)
This script, run every 15 min by watch_round.sh, moves `under` and `adj` from what was measured and writes
.market/price_ctl.json, which the seller re-reads every few minutes (no restart). No model call.

GLOBAL level `under` (how far under the rival's price), one step of STEP at most every GLOBAL_EVERY, judged on the
book slope fitted through the guard's 6.5 h of readings (trendfit.py; not the two readings since the last step, which
carried to the end are +-170 M of noise) and the revenue of the last 3 h:
  book first   the lead over the runner-up, carried to the round's end at the fitted slope, under BOOK_THIN -> price up
  then revenue the book is rich even one standard error lower (>= BOOK_RICH) and we do not outsell the shop ahead of
               us in revenue by what we need to pass it before the end -> price down
  else hold.   Bounds UNDER_MIN (above the rival) .. UNDER_MAX.

PER SKU `adj` (+ = dearer), one step at most every SKU_EVERY per SKU, on the quotes since its last step:
  strong demand  paid closes / quotes >= HIGH with >= MIN_Q quotes -> +STEP
  nearly out     <= THIN_STOCK grade A/B units left and >= 4 quotes -> +STEP (no reason to sell the last ones cheap)
  no takers      paid closes / quotes <= LOW with >= MIN_Q_LOW quotes -> -STEP
Bounds ADJ_MIN .. ADJ_MAX. Every change is logged to logs/price_ctl.log with the numbers behind it.
"""
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
MD = HERE / ".market"
CTL = MD / "price_ctl.json"
LOG = HERE / "logs" / "price_ctl.log"
END = 1794927540                      # 2026-11-17 23:59 KST
ME = "yms979"

STEP = 0.01
UNDER_MIN, UNDER_MAX = -0.02, 0.01    # -0.02 = two points ABOVE the rival's price. 11-12 (user: 'the safest 1st,
                                      # leave the shop as it is'): MAX was .04; revenue 1st is worth +.007 and needs
                                      # +28.5 M/h over inextro (we do +20), so prices are no longer cut to chase it
ADJ_MIN, ADJ_MAX = -0.03, 0.06
GLOBAL_EVERY = 2 * 3600
SKU_EVERY = 2 * 3600
BOOK_THIN, BOOK_RICH = 150e6, 220e6   # projected book lead at the round's end
HIGH, LOW, MIN_Q, MIN_Q_LOW, THIN_STOCK = 0.40, 0.10, 6, 8, 2
DRY = "--dry" in sys.argv


def read(p, default):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC {msg}"
    print(line)
    if not DRY:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def tail_events(since, max_bytes=12_000_000):
    """Seller log records newer than `since` (reads the end of the file only)."""
    p = HERE / "seller_r3_log.jsonl"
    out = []
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            f.seek(max(0, size - max_bytes))
            if size > max_bytes:
                f.readline()
            for raw in f:
                try:
                    r = json.loads(raw)
                except ValueError:
                    continue
                if r.get("t", 0) >= since:
                    out.append(r)
    except OSError:
        pass
    return out


def global_step(ctl, now, base_under):
    """Returns (new under, reason) or (None, why not)."""
    t0 = ctl.get("t_global") or now
    if now - t0 < GLOBAL_EVERY:
        return None, f"next global check in {(GLOBAL_EVERY - (now - t0)) / 60:.0f} min"
    g = read(MD / "guard_state.json", {})
    # 11-12: the book slope is fitted through ALL the guard's readings (6.5 h, every 15 min), not taken from the two
    # readings since the last step: the lead moves +-3 M between readings, which over 2 h is +-1.2 M/h, +-170 M at
    # the round's end, against bars of 150 and 220 M (trendfit.py). sd = one standard error of the lead at the end.
    sys.path.insert(0, str(HERE))
    import trendfit
    bh = g.get("book_hist") or []
    bh = [h for h in bh if h.get("rival") == bh[-1].get("rival")] if bh else []
    if len(bh) < 8 or bh[-1]["t"] - bh[0]["t"] < 4 * 3600:
        return None, "not enough book history (needs 4 h against the same runner-up)"
    dt = (bh[-1]["t"] - bh[0]["t"]) / 3600
    fl = trendfit.linfit([(h["t"], h["us"] - h["rb"]) for h in bh])
    ours = trendfit.linfit([(h["t"], h["us"]) for h in bh])["slope"]
    theirs = trendfit.linfit([(h["t"], h["rb"]) for h in bh])["slope"]
    lead = bh[-1]["us"] - bh[-1]["rb"]
    left = max(0.0, (END - now) / 3600)
    proj = lead + fl["slope"] * left
    sd = (fl["se"] or 0.0) * left
    sv = read(MD / "shop_value.json", {})
    D = sv.get("deals") or []
    t1 = max((d["ts"] for d in D), default=now)
    a = max(t0 + 600, t1 - 3 * 3600)
    h = max(0.25, (t1 - a) / 3600)
    rate = lambda shop: sum(d.get("amt") or 0 for d in D if d.get("shop") == shop and d["ts"] >= a) / h
    ahead = next((s for s, why in (read(MD / "price_rivals.json", {}).get("shops") or {}).items()
                  if any("revenue ahead" in w for w in why)), None)
    r_us, r_ah = rate(ME), (rate(ahead) if ahead else 0.0)
    need = None
    try:
        sys.path.insert(0, str(HERE))
        import board_api
        b = board_api.fetch(timeout=15, tries=1)
        rev = {s["name"]: s.get("revenue") or 0 for s in b["shops"]}
        if ahead and left > 0:
            need = (rev[ahead] - rev[ME]) / left
    except Exception:  # noqa: BLE001
        pass
    under = ctl.get("under", base_under)
    facts = (f"book {ours / 1e6:+.2f} vs {bh[-1]['rival']} {theirs / 1e6:+.2f} M/h over {dt:.1f} h, lead {lead / 1e6:.0f} M -> "
             f"{proj / 1e6:.0f} +-{sd / 1e6:.0f} M at the end; revenue {r_us / 1e6:.1f} M/h vs {ahead} {r_ah / 1e6:.1f}"
             + (f" (need +{need / 1e6:.1f})" if need is not None else ""))
    # the book comes first (user: the shop must stay 1st): up on the best estimate; down only when the book is rich
    # even one standard error lower
    if proj < BOOK_THIN and under > UNDER_MIN:
        return round(under - STEP, 3), f"price UP (book thin): {facts}"
    if proj - sd >= BOOK_RICH and need is not None and (r_us - r_ah) < need and under < UNDER_MAX:
        return round(under + STEP, 3), f"price DOWN (book rich, revenue behind): {facts}"
    return under, f"hold: {facts}"


def sku_steps(ctl, now):
    cat = read(MD / "catalog.json", {})
    inv = read(MD / "inventory.json", {}).get("units") or []
    ab = Counter(u["sku"] for u in inv if u.get("kind") == "goods" and u.get("grade") in ("A", "B"))
    paid = {d.get("unit") for d in (read(MD / "shop_value.json", {}).get("deals") or []) if d.get("shop") == ME}
    start = ctl.get("t_start") or now
    ev = tail_events(max(start, now - 6 * 3600))
    changes = []
    by = {}
    for r in ev:
        sku = r.get("sku")
        if r.get("event") in ("deal", "canceled", "lapsed", "lost") and sku and (cat.get(sku) or {}).get("kind") == "goods":
            by.setdefault(sku, []).append(r)
    for sku, L in by.items():
        last = max(ctl["sku_t"].get(sku, 0), start)
        if now - last < SKU_EVERY:
            continue
        L = [r for r in L if r.get("t", 0) >= last + 300]
        q = len(L)
        if q < 4:
            continue
        p = sum(1 for r in L if r.get("event") == "deal" and r.get("unit") in paid) / q
        adj = ctl["sku"].get(sku, 0.0)
        new, why = adj, None
        if q >= MIN_Q and p >= HIGH and adj < ADJ_MAX:
            new, why = adj + STEP, f"strong demand: {p:.0%} of {q} quotes paid"
        elif ab[sku] <= THIN_STOCK and q >= 4 and adj < ADJ_MAX:
            new, why = adj + STEP, f"nearly out: {ab[sku]} A/B units left, {q} quotes"
        elif q >= MIN_Q_LOW and p <= LOW and adj > ADJ_MIN:
            new, why = adj - STEP, f"no takers: {p:.0%} of {q} quotes paid"
        if why:
            ctl["sku"][sku] = round(new, 3)
            ctl["sku_t"][sku] = now
            changes.append(f"{sku} {(cat.get(sku) or {}).get('name', '')[:22]} {adj:+.2f} -> {new:+.2f} ({why})")
    return changes


def main():
    now = time.time()
    base_under = ((read(HERE / "params.json", {}).get("shop") or {}).get("base") or {}).get("rival_under", 0.01)
    ctl = read(CTL, None) or {"under": base_under, "sku": {}, "sku_t": {}, "t_start": now, "t_global": now, "log": []}
    new, why = global_step(ctl, now, base_under)
    if new is not None:
        if new != ctl.get("under"):
            log(f"GLOBAL under {ctl.get('under'):+.2f} -> {new:+.2f} | {why}")
            ctl["log"] = (ctl.get("log") or [])[-30:] + [{"t": now, "under": new, "why": why}]
            ctl["under"] = new
        else:
            log(f"GLOBAL {why}")
        ctl["t_global"] = now
    ch = sku_steps(ctl, now)
    for c in ch:
        log("SKU " + c)
    ctl["t"] = now
    ctl["status"] = why
    if not DRY:
        tmp = CTL.with_suffix(".tmp")
        tmp.write_text(json.dumps(ctl, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, CTL)
    if DRY:
        up = sum(1 for v in ctl["sku"].values() if v > 0)
        print(f"dry: under {ctl['under']:+.2f}; {why}; SKU steps this run {len(ch)}; SKUs dearer {up}, cheaper {len(ctl['sku']) - up}")


if __name__ == "__main__":
    main()
