"""Week 08 share watch: tell the owner within minutes when the shop falls behind the market (owner, 11-05 01 h).

On 11-04 our share of the market's revenue fell from 33% (14 h) to 6% (00 h) while the market held, and it took
hours of hand checks to see. The 0 h check compared us with the first-placed shop and called it a market slump.
Every 10 minutes this reads the ledger (market.db, read-only), our talks (data/negotiations.jsonl) and our stock
(.market/inventory.json) and appends what it finds to data/alerts.md (the same file rule_watch writes, newest last,
KST), with the numbers in data/share_watch.json. Signals, each against the 3 hours before the last hour:
  1. share: our share of the market's revenue (all, and goods listed at 1M+) under 0.75 of it while the market held;
  2. talks: buyers opening talks with us under 0.75 of it while the market's deals held;
  3. price: on a SKU where other shops sold grade A 5+ times in the hour, our median A floor is over their
     median paid share of list + 0.02 (on 11-04 the only signal that came before the revenue fell: 다우닝 and
     지누스 at 17 h, 자코모 at 22 h; session 56's audit);
  4. top SKUs: of the market's top 10 SKUs by revenue in the hour, those where our share is under 5%, with our stock,
     talks and deals on each (out of stock, or held and losing), added when 1 or 2 rings;
  5. warranty: buyers starting to want one on goods B/C (patch 3.4), see warranty_watch;
  6. new SKUs: first supplied in the last 6 h and 3%+ of the market's revenue in the last hour, see new_skus.
(Our opening ask sits over the market's paid price by design, so the price signal uses the floor: with the ask it
rang on 5 SKUs in every half hour of 11-04.)
It changes nothing the shop reads; the owner decides. A signal repeats at most once an hour.
"""
import argparse
import collections
import json
import sqlite3
import statistics as st
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MD, DATA = HERE / ".market", HERE / "data"
DB, NEG = DATA / "market.db", DATA / "negotiations.jsonl"
STATE, ALERTS = DATA / "share_watch.json", DATA / "alerts.md"
OURS = "inextro"
EVERY_S, COOLDOWN_S = 600, 3600
HIGH = 1_000_000            # list price of the high-end band
DROP, HELD = 0.75, 0.8      # ours under DROP x baseline while the market stays at HELD x its baseline or more
# (replay of 11-04: 0.5 first rang at 20:49 for 1M+ and 23:19 for all; 0.75 rings at 18:19, when 30% became 22%)
MIN_SHARE = 0.05            # baseline share under this: nothing to lose
PRICE_GAP, PRICE_MIN = 0.02, 5
TOP_N, TOP_SHARE = 10, 0.05


def kst(t):
    return time.strftime("%m-%d %H:%M", time.gmtime(t + 9 * 3600))


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def settles(db, t0, t1):
    names = dict(db.execute("select url, name from shops"))
    lp = dict(db.execute("select sku, list_price from catalog"))
    pn = dict(db.execute("select sku, name from catalog"))
    out = []
    for ts, d in db.execute("select ts, data from ledger where kind = 'settle' and ts >= ? and ts < ?", (t0, t1)):
        d = json.loads(d)
        sku = d.get("sku") or d.get("item")
        out.append({"ts": ts, "shop": names.get(d.get("shop"), "?"), "sku": sku, "grade": d.get("grade"),
                    "amount": d.get("amount") or 0, "list": lp.get(sku) or 0})
    return out, pn


def opens(t0, t1):
    out = []
    try:
        with NEG.open(encoding="utf-8") as f:
            for line in f:
                if '"open"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("outcome") == "open" and t0 <= d.get("ts", 0) < t1:
                    out.append(d)
    except OSError:
        pass
    return out


def held_units():
    inv = load(MD / "inventory.json", {})
    c = collections.Counter()
    for u in inv.get("units") or []:
        c[u.get("sku")] += 1
    return c


def measure(now, db, live=True):
    s, names = settles(db, now - 4 * 3600, now)
    last = [x for x in s if x["ts"] >= now - 3600]
    base = [x for x in s if x["ts"] < now - 3600]

    def rev(rows, band=False):
        rows = [x for x in rows if not band or x["list"] >= HIGH]
        m = sum(x["amount"] for x in rows)
        o = sum(x["amount"] for x in rows if x["shop"] == OURS)
        return m, o

    m = {}
    for key, band in (("all", False), ("high", True)):
        m1, o1 = rev(last, band)
        mb, ob = rev(base, band)
        m[key] = {"market_1h": m1, "ours_1h": o1, "share_1h": o1 / m1 if m1 else 0.0,
                  "market_base_h": mb / 3, "share_base": ob / mb if mb else 0.0}
    t_last = opens(now - 3600, now)
    t_base = opens(now - 4 * 3600, now - 3600)
    m["talks"] = {"ours_1h": len(t_last), "ours_base_h": len(t_base) / 3,
                  "market_deals_1h": len(last), "market_deals_base_h": len(base) / 3}

    # 3. price: our A asks / floors against the other shops' A paid share of list
    mk = collections.defaultdict(list)
    for x in last:
        if x["shop"] != OURS and x["grade"] == "A" and x["list"]:
            mk[x["sku"]].append(x["amount"] / x["list"])
    ours = collections.defaultdict(lambda: ([], []))
    for d in t_last:
        if d.get("grade") == "A" and d.get("list_price"):
            ours[d["sku"]][0].append(d["ask"] / d["list_price"])
            ours[d["sku"]][1].append(d["floor"] / d["list_price"])
    price = []
    for sku, (asks, floors) in ours.items():
        if len(mk.get(sku, [])) < PRICE_MIN:
            continue
        mmed, a, fl = st.median(mk[sku]), st.median(asks), st.median(floors)
        if fl > mmed + PRICE_GAP:
            price.append({"sku": sku, "name": (names.get(sku) or "")[:24], "market_a": round(mmed, 3),
                          "our_ask": round(a, 3), "our_floor": round(fl, 3), "market_n": len(mk[sku])})
    m["price"] = sorted(price, key=lambda p: p["market_a"] - p["our_floor"])

    # 4. the market's top SKUs where we are under TOP_SHARE
    by = collections.defaultdict(lambda: [0, 0, 0])
    for x in last:
        r = by[x["sku"]]
        r[0] += x["amount"]
        if x["shop"] == OURS:
            r[1] += x["amount"]
            r[2] += 1
    talks = collections.Counter(d.get("sku") for d in t_last)
    stock = held_units() if live else {}
    top = []
    for sku, (mr, orv, od) in sorted(by.items(), key=lambda kv: -kv[1][0])[:TOP_N]:
        if mr and orv / mr < TOP_SHARE:
            top.append({"sku": sku, "name": (names.get(sku) or "")[:24], "market_1h": mr, "share": round(orv / mr, 3),
                        "stock": stock.get(sku) if live else None, "talks": talks.get(sku, 0), "deals": od})
    m["top"] = top
    return m


def signals(m):
    out = []
    for key, label in (("all", "revenue"), ("high", "1M+ revenue")):
        x = m[key]
        if (x["share_base"] >= MIN_SHARE and x["share_1h"] < DROP * x["share_base"]
                and x["market_1h"] >= HELD * x["market_base_h"]):
            out.append((f"share_{key}", f"our share of the market's {label} {x['share_1h']:.1%} in the last hour against "
                        f"{x['share_base']:.1%} the 3 h before; the market {x['market_1h'] / 1e6:.0f}M against "
                        f"{x['market_base_h'] / 1e6:.0f}M/h"))
    t = m["talks"]
    if (t["ours_base_h"] >= 20 and t["ours_1h"] < DROP * t["ours_base_h"]
            and t["market_deals_1h"] >= HELD * t["market_deals_base_h"]):
        out.append(("talks", f"buyers opened {t['ours_1h']} talks with us in the last hour against {t['ours_base_h']:.0f}/h "
                    f"the 3 h before; market deals {t['market_deals_1h']} against {t['market_deals_base_h']:.0f}/h"))
    if m["price"]:      # one line an hour for all of them (11-05 01-06 h: one per SKU, 26 of 31 alerts)
        out.append(("price", f"price: {len(m['price'])} SKUs where our A floor is over what other shops sold A at; "
                    + "; ".join(f"{p['sku']} {p['name']} market A {p['market_a']} ({p['market_n']} sales), our ask "
                                f"{p['our_ask']}, floor {p['our_floor']}" for p in m["price"][:3])))
    if m["top"] and out and not all(k.startswith("price") for k, _ in out):
        show = "; ".join(f"{x['sku']} {x['name']} {x['market_1h'] / 1e6:.0f}M ours {x['share']:.0%} "
                         f"stock {x['stock']} talks {x['talks']} deals {x['deals']}" for x in m["top"][:5])
        out.append(("top", f"top SKUs under {TOP_SHARE:.0%} ours: {show}"))
    return out


# 5. warranty (owner, 11-06 11 h): patch 3.4 (06:00) scores a buyer's unrefunded break -1, so buyers may stop taking
# goods B and C without a warranty, which we give only to a buyer who asks (seller.WARRANTY_ON_ASK). Through 11-06
# 11 h they had not: the market's share of B / C goods settled with a warranty held at 48-50% / 33-34% (18 h before
# and after 06:00), no result carried an `accept` with warranty_hours, and our B/C talks whose buyer did not ask
# closed 85-100% per 3 h. Rings when one of these moves; then B by default is the first step (a B's refund, 12% x
# 538k = 65k, fits its 126k margin; a C's, 39% x 503k = 196k, takes its 207k).
# 11-06 15:50: B gets one by default since 13:22 (seller.WARRANTY_ON_ASK = ("C",), owner kept it), so only C is watched
ON_ASK = ("C",)
W_NOASK_MIN, W_NOASK_RATE = 15, 0.70
W_MARKET = {"B": 0.60, "C": 0.45}


def warranty_watch(now, db):
    cat = load(MD / "catalog.json", {})
    goods = lambda sku: (cat.get(sku) or {}).get("kind") == "goods"
    t0, t1 = now - 3 * 3600 - 600, now - 600            # talks that had 10 minutes to close
    opened, closed = {}, {}
    try:
        with NEG.open(encoding="utf-8") as f:
            for line in f:
                if '"open"' not in line and '"deal"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("outcome") == "open" and t0 <= d.get("ts", 0) < t1 and "wants_warranty" in d:
                    opened[d["task"]] = d
                elif d.get("outcome") == "deal" and t0 <= d.get("ts", 0) < now:
                    closed[d.get("task")] = True
    except OSError:
        pass
    noask = {g: [t for t, d in opened.items() if d.get("kind") == "goods" and d.get("grade") == g
                 and not d.get("wants_warranty")] for g in ("B", "C")}
    talks = {g: {"n": len(v), "deal_rate": round(sum(1 for t in v if t in closed) / len(v), 2) if v else None}
             for g, v in noask.items()}
    mk = {g: [0, 0] for g in ("B", "C")}
    for (d,) in db.execute("select data from ledger where kind = 'settle' and ts >= ? and ts < ?", (now - 3 * 3600, now)):
        d = json.loads(d)
        if d.get("grade") in mk and goods(d.get("sku") or d.get("item")):
            mk[d["grade"]][0] += 1
            mk[d["grade"]][1] += bool(d.get("warranty_hours"))
    market = {g: round(w / n, 2) if n >= 50 else None for g, (n, w) in mk.items()}
    acc = 0
    for (d,) in db.execute("select data from ledger where kind = 'result' and ts >= ? and ts < ?", (now - 3600, now)):
        a = json.loads(d).get("accept")
        acc += isinstance(a, dict) and bool(a.get("warranty_hours"))
    return {"noask_talks": talks, "market_share_warranty": market, "accept_warranty_1h": acc}


def warranty_signal(w):
    why = []
    for g, x in w["noask_talks"].items():
        if g in ON_ASK and x["n"] >= W_NOASK_MIN and x["deal_rate"] is not None and x["deal_rate"] < W_NOASK_RATE:
            why.append(f"our goods {g} talks whose buyer asked no warranty closed {x['deal_rate']:.0%} of {x['n']} in 3 h "
                       f"(85-100% through 11-06 11 h)")
    for g, s in w["market_share_warranty"].items():
        if g in ON_ASK and s is not None and s > W_MARKET[g]:
            why.append(f"the market's goods {g} settled with a warranty {s:.0%} in 3 h (11-06: B 48-50%, C 33-34%)")
    if w["accept_warranty_1h"]:
        why.append(f"{w['accept_warranty_1h']} buyer results in the last hour carried an accept with warranty_hours")
    if not why:
        return None
    return ("warranty", "warranty: buyers may now want one on C (patch 3.4, unrefunded break -1): " + "; ".join(why)
            + ". B gets one by default since 11-06 13:22; C only on ask (seller.WARRANTY_ON_ASK), since a C's refund "
              "(39% x 503k = 196k) takes about all its 207k margin: report it, the owner decides")


# 6. new SKUs (owner, 11-06 12 h): 다우닝 플라나 4인 G24f5f979 and 자코모 와드 4인 G47788edd first came to the market
# at 07:22; by 10 h new SKUs made 21-28% of the market's revenue an hour (플라나 589M by 12 h, ours 21%), and nothing
# said they were new. A SKU whose first market supply is within NEW_H hours (none in the NEW_LOOKBACK_D days before)
# rings once its last hour reaches NEW_SHARE of the market's revenue, with our share, stock and talks on it.
NEW_H, NEW_LOOKBACK_D, NEW_SHARE = 6, 3, 0.03


def new_skus(now, db):
    first = {}
    for ts, d in db.execute("select ts, data from ledger where kind = 'supply' and ts >= ? and ts < ? order by ts",
                            (now - NEW_LOOKBACK_D * 86400, now)):
        sku = json.loads(d).get("sku")
        if sku and sku not in first:
            first[sku] = ts
    new = {s: t for s, t in first.items() if t >= now - NEW_H * 3600}
    if not new:
        return []
    s, names = settles(db, now - 3600, now)
    total = sum(x["amount"] for x in s) or 1
    held = held_units()
    talks = collections.Counter(d.get("sku") for d in opens(now - 3600, now))
    out = []
    for sku, t in new.items():
        rows = [x for x in s if x["sku"] == sku]
        rev = sum(x["amount"] for x in rows)
        ours = [x for x in rows if x["shop"] == OURS]
        out.append({"sku": sku, "name": (names.get(sku) or "?")[:24], "first": kst(t), "market_1h": rev,
                    "units_1h": len(rows), "of_market": round(rev / total, 3), "ours_units": len(ours),
                    "share": round(sum(x["amount"] for x in ours) / rev, 3) if rev else 0.0,
                    "stock": held.get(sku, 0), "talks": talks.get(sku, 0)})
    return sorted(out, key=lambda x: -x["market_1h"])


def new_signals(rows):
    return [(f"new_{x['sku']}", f"new SKU: {x['sku']} {x['name']} first supplied {x['first']}, {x['units_1h']} sold in "
             f"the last hour, {x['market_1h'] / 1e6:.0f}M = {x['of_market']:.0%} of the market's revenue; ours "
             f"{x['ours_units']} ({x['share']:.0%}), stock {x['stock']}, talks {x['talks']}. restock buys it on its "
             f"own; report it to the owner and check that it does (stock, deals, our floor against the market's)")
            for x in rows if x["of_market"] >= NEW_SHARE]


def shortage_note(now):
    """model.shortage() as restock sees it (session 56, a9c5f03), recorded only; None when unavailable."""
    try:
        import model
        r = model.Estimates().shortage()
        return r if isinstance(r, (dict, list)) else str(r)
    except Exception as e:
        return f"unavailable ({type(e).__name__})"


def run(now=None, write=True, live=True):
    now = now or time.time()
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        m = measure(now, db, live)
        m["warranty"] = warranty_watch(now, db)
        m["new"] = new_skus(now, db)
    finally:
        db.close()
    sig = signals(m)
    ws = warranty_signal(m["warranty"])
    if ws:
        sig.append(ws)
    sig += new_signals(m["new"])
    if not write:
        return m, sig
    state = load(STATE, {})
    sent = state.get("sent", {})
    fresh = [(k, text) for k, text in sig if now - sent.get(k, 0) >= COOLDOWN_S]
    for k, _ in fresh:
        sent[k] = now
    short = shortage_note(now)
    STATE.write_text(json.dumps({"ts": now, "kst": kst(now), "metrics": m, "signals": [k for k, _ in sig],
                                 "shortage": short, "sent": sent}, ensure_ascii=False, default=str),
                     encoding="utf-8")
    if fresh:
        with ALERTS.open("a", encoding="utf-8") as f:
            for _, text in fresh:
                f.write(f"- {kst(now)} share: {text}\n")
    return m, sig


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true", help=f"run every {EVERY_S} s")
    ap.add_argument("--replay", type=float, default=0, help="hours back: print the signals every 30 min, write nothing")
    a = ap.parse_args()
    if a.replay:
        end = time.time()
        t = end - a.replay * 3600
        while t <= end:
            m, sig = run(t, write=False, live=False)
            x = m["all"]
            print(f"{kst(t)} share {x['share_1h']:.1%} (base {x['share_base']:.1%}) high {m['high']['share_1h']:.1%} "
                  f"talks {m['talks']['ours_1h']} | {', '.join(k for k, _ in sig) or '-'}", flush=True)
            t += 1800
    elif a.loop:
        while True:
            try:
                run()
            except Exception as e:
                print(f"{kst(time.time())} share_watch failed: {type(e).__name__}: {str(e)[:160]}", flush=True)
            time.sleep(EVERY_S)
    else:
        m, sig = run()
        print(json.dumps({"signals": sig, "all": m["all"], "high": m["high"], "talks": m["talks"]}, ensure_ascii=False,
                         indent=1))
