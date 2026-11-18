"""My shop's sales reports and the low/high-price buying split. restock.py calls tick() every minute.

  hourly  after each full hour: sales, revenue, profit (price - unit cost from my_stock), profit / cost for
          low-price (unit cost <= HIGH_MIN) and high-price units, sales per category, stock at cost, cash
          -> market.db table hourly + one log line
  daily   after midnight: the day's totals, the 3 SKUs that made the most, the 3 slowest stock SKUs, broken /
          refunds -> reports/daily_YYYYMMDD.md, then adjust the split (policy table):
            high-price stock unsold > 24 h, or a refund on a high-price unit  -> high share -10 %p (min 20%)
            else high profit/cost >= low profit/cost (and some high sales)   -> high share +10 %p (max 50%),
                                                                                 high-price units held +1 (max 5)
"""
import json
import os
import time
from datetime import datetime, timedelta

from market_db import ME, connect, load_stats, meta
from depts.logistics import perishable, perishable_line, perishable_numbers  # noqa: F401

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # week-08/ (this file is in depts/)
REPORTS = os.path.join(HERE, "reports")
HIGH_MIN = 100_000              # unit cost above this is "high price" ...
HIGH_MAX = 1_500_000            # ... up to this (11/14 ~02:15 person: 2.0M -> 1.5M; 11/13 ~15:00 person: 3.0M -> 2.0M; 11/11 ~20:10 person: 1.5M -> 3.0M, pinned to 11/13 12:00; 11/06: was 500,000)
START_SHARE, START_MAX = 0.30, 2
SHARE_STEP, SHARE_MIN, SHARE_CAP, MAX_CAP = 0.10, 0.20, 0.60, 40   # MAX_CAP 5 -> 10 on 11/06 (units held 8);
#                                                     11/07 ~16:05: SHARE_CAP 0.5 -> 0.6, MAX_CAP 20 -> 30 (share 0.6, units 28)
#                                                     11/08 ~13:30 (person): MAX_CAP 30 -> 60 (high_max 60); ~17:00 back to 30
#                                                     11/09 ~11:00 (person): MAX_CAP 30 -> 40 (high_max 40)
HOLD_S = 24 * 3600   # 11/07 ~16:10 (person's decision): for 24 h after buying expansion 2 is applied, no rule lowers
#                      the high-price share (0.6) or units held (28); raising stays allowed. buy_expand.json since2.


def split_floor(now=None):
    """{"high_share": 0.6, "high_max": 28} while the hold after buying expansion 2 runs, else None."""
    import json
    import os
    try:
        j = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                        "buy_expand.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return None
    t = j.get("since2")
    now = now or time.time()
    if j.get("hold_paused"):                       # 0.4 / 18 for now (no buy guard by 22:00; tools/guard_watch.py)
        return None
    if not t or not (t <= now < t + HOLD_S):
        return None
    return {"high_share": (j.get("high_share2") or [0, 0.6])[1], "high_max": (j.get("high_max2") or [0, 28])[1]}


STALE_SHARE = 0.20   # 11/07 ~16:05 (person's decision): the share is cut only when >= 20% of the high-price units held
#                      are unsold > 24 h (was: any one of them)
#                                                     10 -> 20 on 11/07 ~14:45 (units held 8 -> 18, person's decision)


def policy(db=None):
    own = db is None
    db = db or connect()
    try:
        row = db.execute("SELECT high_share, high_max FROM policy ORDER BY ts DESC LIMIT 1").fetchone()
        if row is None:
            db.execute("INSERT INTO policy VALUES (?,?,?,?)", (time.time(), START_SHARE, START_MAX, "start"))
            db.commit()
            row = (START_SHARE, START_MAX)
        return {"high_share": row[0], "high_max": row[1]}
    finally:
        if own:
            db.close()


def my_sales(db, t0, t1):
    """(seq, ts, sku, name, amount, cost) of my shop's settles in [t0, t1)."""
    return db.execute("""SELECT s.seq, s.ts, s.sku, s.name, s.amount, m.cost FROM settles s JOIN my_stock m ON m.unit=s.unit
                         WHERE s.ts>=? AND s.ts<? ORDER BY s.seq""", (t0, t1)).fetchall()


def summarize(rows, catalog):
    out = {"sales": len(rows), "revenue": 0, "profit": 0, "low_cost": 0, "low_profit": 0, "high_cost": 0,
           "high_profit": 0, "cats": {}}
    for _, _, sku, _, amount, cost in rows:
        out["revenue"] += amount
        out["profit"] += amount - cost
        tier = "high" if cost > HIGH_MIN else "low"
        out[f"{tier}_cost"] += cost
        out[f"{tier}_profit"] += amount - cost
        leaf = ((catalog.get(sku) or {}).get("category") or "?").split(">")[-1]
        out["cats"][leaf] = out["cats"].get(leaf, 0) + 1
    return out


def rate(profit, cost):
    return profit / cost if cost else None


def pct(x):
    return "-" if x is None else f"{100 * x:.0f}%"


def extras(db, t0, t1, catalog):
    """Stage 1/2 numbers for a report period."""
    out = {}
    rows = db.execute("SELECT ts, shop_case FROM cases WHERE ts < ? ORDER BY ts", (t1,)).fetchall()
    dur = {}
    for (ts, c), nxt in zip(rows, rows[1:] + [(t1, None)]):
        a, b = max(ts, t0), min(nxt[0], t1)
        if b > a:
            dur[c] = dur.get(c, 0) + round((b - a) / 60)
    out["case_minutes"] = dur
    out["case_switches"] = sum(1 for (_, a), (ts, b) in zip(rows, rows[1:]) if a != b and t0 <= ts < t1)
    import strategy
    out["variants"] = {}
    for key in strategy.VARIANTS:
        w = strategy.weights(db, key)
        perf = strategy.performance(db, key)
        out["variants"][key] = {v: {"weight": w[v], "n": perf[v][0], "metric": perf[v][1]} for v in w}
    surplus = 0
    for sku, amount in db.execute("SELECT sku, amount FROM settles WHERE buyer=? AND ts>=? AND ts<?", (ME, t0, t1)):
        lp = (catalog.get(sku) or {}).get("list_price")
        surplus += (lp - amount) if lp else 0
    refund = db.execute("SELECT COALESCE(SUM(paid), 0) FROM my_broken WHERE refunded=1 AND broke_at>=? AND broke_at<?",
                        (t0, t1)).fetchone()[0]
    out["buyer_surplus"] = surplus + refund
    out["buyer_refunds"] = refund
    wr = db.execute("""SELECT COUNT(*), COALESCE(SUM((SELECT s.amount FROM settles s WHERE s.shop=? AND s.sku=b.sku
                       AND s.buyer=b.buyer AND s.ts<=b.ts ORDER BY s.ts DESC LIMIT 1)), 0)
                       FROM broken b WHERE b.shop=? AND b.refunded=1 AND b.ts>=? AND b.ts<?""", (ME, ME, t0, t1)).fetchone()
    out["warranty_refunds"] = {"n": wr[0], "amount": wr[1]}
    out["warranty_sales"] = db.execute("SELECT COUNT(*) FROM settles WHERE shop=? AND warranty_hours>0 AND ts>=? AND ts<?",
                                       (ME, t0, t1)).fetchone()[0]
    out["want_sales"] = db.execute("""SELECT COUNT(DISTINCT s.seq) FROM settles s JOIN want_replies w ON w.sku=s.sku
                                      WHERE s.shop=? AND s.ts>=w.ts AND s.ts<=w.ts+7200 AND s.ts>=? AND s.ts<?""",
                                   (ME, t0, t1)).fetchone()[0]
    scores = load_stats().get("shop_score", {})
    out["top_shops"] = [(k, v["score"]) for k, v in sorted(scores.items(), key=lambda kv: -kv[1]["score"])[:3]]
    return out


def day_money(db, t0, t1, s):
    """Cash, stock at cost and storage from the 5-min cash table; sales profit; book change; STOCK_CAP runs."""
    rows = db.execute("SELECT ts, balance, stock_cost, storage_per_day FROM cash WHERE ts>=? AND ts<? ORDER BY ts",
                      (t0, t1)).fetchall()
    out = {"profit": s["profit"], "revenue": s["revenue"], "rows": len(rows)}
    if rows:
        out.update(cash0=rows[0][1], cash1=rows[-1][1], stock0=rows[0][2], stock1=rows[-1][2],
                   storage=round(sum(r[3] or 0 for r in rows) / len(rows) * (rows[-1][0] - rows[0][0]) / 86400),
                   book_change=(rows[-1][1] + rows[-1][2]) - (rows[0][1] + rows[0][2]))
    try:
        cap = db.execute("SELECT shop_case, budget_free - budget, plan_free, plan FROM cap_runs WHERE ts>=? AND ts<?",
                         (t0, t1)).fetchall()
    except Exception:
        cap = []
    out["cap_runs"] = len(cap)
    out["cap_blocked"] = sum(1 for c in cap if c[0] == "overstock")
    out["cap_budget_cut"] = sum(c[1] for c in cap)
    extra = {}
    for _, _, pf, p in cap:
        mine, free = {}, {}
        for x in json.loads(p):
            mine[x["sku"]] = mine.get(x["sku"], 0) + x["qty"]
        for x in json.loads(pf):
            free[x["sku"]] = free.get(x["sku"], 0) + x["qty"]
        for k, q in free.items():
            extra[k] = extra.get(k, 0) + max(0, q - mine.get(k, 0))
    out["cap_extra"] = sorted(((k, v) for k, v in extra.items() if v), key=lambda kv: -kv[1])
    return out


def money_line(m):
    if not m["rows"]:
        return f"sales profit {m['profit']:,}; no cash snapshots"
    return (f"cash {m['cash0']:,} -> {m['cash1']:,}, stock at cost {m['stock0']:,} -> {m['stock1']:,}, "
            f"storage ~{m['storage']:,}, sales profit {m['profit']:,}, book change {m['book_change']:+,}; "
            f"STOCK_CAP runs {m['cap_runs']} (blocked {m['cap_blocked']}, budget cut {m['cap_budget_cut']:,})")


def money_md(m, catalog):
    if not m["rows"]:
        return [f"- sales profit {m['profit']:,}; no cash snapshots"]
    return [f"- cash {m['cash0']:,} -> {m['cash1']:,}",
            f"- stock at cost {m['stock0']:,} -> {m['stock1']:,}",
            f"- storage ~{m['storage']:,} (5-min snapshots of stock x storage_per_day)",
            f"- sales profit {m['profit']:,} on revenue {m['revenue']:,}; book (cash + stock at cost) change "
            f"{m['book_change']:+,}",
            f"- runs where STOCK_CAP ({__import__("strategy").STOCK_CAP:,}) blocked or cut buying: {m['cap_runs']} (blocked "
            f"{m['cap_blocked']}), budget kept back {m['cap_budget_cut']:,} in total",
            "- without the cap these runs would also have bought: "
            + (", ".join(f"{((catalog.get(k) or {}).get('name') or k)[:24]} x{q}" for k, q in m["cap_extra"][:10])
               or "nothing")]


def payment_counts():
    """payments.jsonl so far: (lines, unpaid, unpaid for shelf life). The file has no time stamps, so hourly
    reports store these counts and report the growth since the last report."""
    lines = unpaid = shelf = 0
    try:
        with open(os.path.join(HERE, ".market", "payments.jsonl"), encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                lines += 1
                p = json.loads(line)
                if not p.get("paid"):
                    unpaid += 1
                    shelf += "shelf life" in (p.get("reason") or "")
    except (OSError, ValueError):
        pass
    return {"lines": lines, "unpaid": unpaid, "shelf": shelf}


def payment_delta(db):
    now = payment_counts()
    prev = json.loads(meta(db, "pay_seen") or "null") or now
    db.execute("INSERT OR REPLACE INTO meta VALUES ('pay_seen', ?)", (json.dumps(now),))
    return {k: now[k] - prev.get(k, 0) for k in now}


def hourly(db, inv, catalog, log):
    now = time.time()
    hour = now // 3600 * 3600 - 3600                  # the last full hour (epoch hours line up with KST hours)
    done = meta(db, "hourly_done")                    # start of the next hour still to report
    if done is not None and hour < float(done):
        return
    s = summarize(my_sales(db, hour, hour + 3600), catalog)
    stock = sum(u.get("cost", 0) for u in inv.get("units", []) if u.get("grade") != "expired")
    cash = int(inv.get("balance", 0))
    x = extras(db, hour, hour + 3600, catalog)
    x["payments"] = payment_delta(db)
    x["perishable"] = perishable_numbers(db, hour, hour + 3600, catalog, inv)
    db.execute("INSERT OR REPLACE INTO hourly VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (hour, s["sales"], s["revenue"], s["profit"], s["low_cost"], s["low_profit"], s["high_cost"],
                s["high_profit"], json.dumps(s["cats"], ensure_ascii=False), stock, cash,
                json.dumps(x, ensure_ascii=False)))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('hourly_done', ?)", (str(hour + 3600),))
    db.commit()
    cats = ", ".join(f"{k} {v}" for k, v in sorted(s["cats"].items(), key=lambda kv: -kv[1])[:4]) or "-"
    log(f"[report] {datetime.fromtimestamp(hour):%m-%d %H}:00 sold {s['sales']}, revenue {s['revenue']:,}, "
        f"profit {s['profit']:,} (low {pct(rate(s['low_profit'], s['low_cost']))}, "
        f"high {pct(rate(s['high_profit'], s['high_cost']))}), by category: {cats}; stock {stock:,}, cash {cash:,}")
    log(f"[report] {datetime.fromtimestamp(hour):%H}:00 cases {x['case_minutes']} min, "
        f"{x['case_switches']} switches, buyer surplus "
        f"{x['buyer_surplus']:,} (refunds {x['buyer_refunds']:,}), warranty sales {x['warranty_sales']}, "
        f"warranty refunds {x['warranty_refunds']['n']} ({x['warranty_refunds']['amount']:,}), wanted-reply sales "
        f"{x['want_sales']}, top shops {x['top_shops']}")
    pay = x["payments"]
    log(f"[report] {datetime.fromtimestamp(hour):%H}:00 buyer payments: {pay['lines']} checkouts, {pay['unpaid']} unpaid, "
        f"{pay['shelf']} of them for shelf life")
    log(f"[report] {datetime.fromtimestamp(hour):%H}:00 {perishable_line(x['perishable'])}")


def extras_md(db, t0, t1, catalog):
    x = extras(db, t0, t1, catalog)
    lines = ["## Cases and variants", "",
             "- time per shop case (min): " + (", ".join(f"{k} {v}" for k, v in x["case_minutes"].items()) or "-")
             + f"; {x['case_switches']} switches"]
    for key, vs in x["variants"].items():
        lines.append(f"- {key}: " + ", ".join(f"{v} weight {d['weight']:.2f} (n {d['n']}, per negotiation {d['metric']})"
                                               for v, d in vs.items()))
    changes = db.execute("SELECT datetime(ts,'unixepoch','localtime'), key, reason FROM variant_weights "
                         "WHERE ts>=? AND ts<? AND reason!='start' GROUP BY ts, key", (t0, t1)).fetchall()
    lines += [f"- weight change {ts} {key}: {reason}" for ts, key, reason in changes] or ["- no weight change"]
    pays = [json.loads(e).get("payments") for (e,) in db.execute(
        "SELECT extra FROM hourly WHERE hour>=? AND hour<? AND extra IS NOT NULL", (t0, t1))]
    pays = [p for p in pays if p]
    if pays:
        lines.append(f"- buyer checkouts {sum(p['lines'] for p in pays)}, unpaid {sum(p['unpaid'] for p in pays)}, "
                     f"for shelf life {sum(p['shelf'] for p in pays)} (hourly growth of payments.jsonl)")
    lines += ["", "## Buyer, warranty, forum", "",
              f"- buyer surplus (list - paid + refunds) {x['buyer_surplus']:,}, refunds {x['buyer_refunds']:,}",
              f"- my warranty sales {x['warranty_sales']}, warranty refunds {x['warranty_refunds']['n']} "
              f"({x['warranty_refunds']['amount']:,})",
              f"- sales within 2 h of a '구합니다' reply: {x['want_sales']}",
              "- top shop scores: " + ", ".join(f"{k} {v:.2f}" for k, v in x["top_shops"]), ""]
    return lines


def daily(db, inv, catalog, log):
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    day = today - timedelta(days=1)
    done = meta(db, "daily_done")
    if done is None:                                  # first run: start with the day in progress
        db.execute("INSERT OR REPLACE INTO meta VALUES ('daily_done', ?)", (day.strftime("%Y%m%d"),))
        db.commit()
        return
    if done >= day.strftime("%Y%m%d"):
        return
    t0, t1 = day.timestamp(), today.timestamp()
    rows = my_sales(db, t0, t1)
    s = summarize(rows, catalog)
    by_sku = {}
    for _, _, sku, name, amount, cost in rows:
        b = by_sku.setdefault(sku, {"name": name, "n": 0, "profit": 0})
        b["n"] += 1
        b["profit"] += amount - cost
    top = sorted(by_sku.items(), key=lambda kv: -kv[1]["profit"])[:3]
    now = time.time()
    held = {}
    for u in inv.get("units", []):
        if u.get("grade") != "expired":
            h = held.setdefault(u["sku"], {"name": u["name"], "n": 0, "cost": 0, "oldest": now})
            h["n"] += 1
            h["cost"] += u.get("cost", 0)
            h["oldest"] = min(h["oldest"], u.get("supplied_at") or now)
    slow = sorted(held.items(), key=lambda kv: kv[1]["oldest"])[:3]
    broken = db.execute("""SELECT b.seq, b.sku, b.hours_after_sale, b.refunded, m.cost FROM broken b
                           LEFT JOIN settles s ON s.sku=b.sku AND s.buyer=b.buyer AND s.shop=? LEFT JOIN my_stock m ON m.unit=s.unit
                           WHERE b.shop=? AND b.ts>=? AND b.ts<?""", (ME, ME, t0, t1)).fetchall()
    stock = sum(h["cost"] for h in held.values())
    lr, hr = rate(s["low_profit"], s["low_cost"]), rate(s["high_profit"], s["high_cost"])

    pol = policy(db)
    share, cap = pol["high_share"], pol["high_max"]
    held_high = [u for u in inv.get("units", []) if u.get("grade") != "expired" and u.get("cost", 0) > HIGH_MIN]
    stale_high = [u for u in held_high if now - (u.get("supplied_at") or now) > 24 * 3600]
    high_refund = [b for b in broken if b[3] and (b[4] or 0) > HIGH_MIN]
    if (held_high and len(stale_high) / len(held_high) >= STALE_SHARE) or high_refund:
        share = max(SHARE_MIN, round(share - SHARE_STEP, 2))
        reason = (f"{len(stale_high)} of {len(held_high)} high-price units unsold > 24 h, "
                  f"{len(high_refund)} high-price refunds")
    elif hr is not None and (lr is None or hr >= lr):
        from depts import high_caps                # 11/09 ~14:35: the ceilings live in high_caps.json (0.6/40 or 0.7/60)
        sc, mc = high_caps.get()
        share, cap = min(sc, round(share + SHARE_STEP, 2)), min(mc, cap + 1)
        reason = f"high profit/cost {pct(hr)} >= low {pct(lr)}"
    else:
        reason = f"no change (high {pct(hr)}, low {pct(lr)})"
    fl = split_floor(now)
    if fl and (share < fl["high_share"] or cap < fl["high_max"]):     # 24 h hold after buying expansion 2
        reason += (f"; held: share {share:.0%} -> {fl['high_share']:.0%}, units {cap} -> {fl['high_max']} "
                   f"(no lowering for 24 h after expansion 2)")
        share, cap = max(share, fl["high_share"]), max(cap, fl["high_max"])
    from depts import policy_notify, night_freeze
    if night_freeze.frozen(now) and (share, cap) != (pol["high_share"], pol["high_max"]):   # 11/09 ~23:55 (person)
        reason += (f"; overnight freeze until {night_freeze.until_text()}: stays {pol['high_share']} / {pol['high_max']} "
                   f"(rule wanted {share} / {cap})")
        share, cap = pol["high_share"], pol["high_max"]
    if policy_notify.pinned("purchasing.high_share", now) and share != pol["high_share"]:
        reason += f"; high_share pinned by the person: stays {pol['high_share']}"
        share = pol["high_share"]
    if policy_notify.pinned("purchasing.high_max", now) and cap != pol["high_max"]:
        reason += f"; high_max pinned by the person: stays {pol['high_max']}"
        cap = pol["high_max"]
    db.execute("INSERT INTO policy VALUES (?,?,?,?)", (now, share, cap, f"{day:%Y-%m-%d}: {reason}"))
    try:
        policy_notify.notify("qc", [{"key": "purchasing.high_share", "old": pol["high_share"], "new": share,
                                     "where": "QC 정책 테이블 (market.db policy) > high_share"},
                                    {"key": "purchasing.high_max", "old": pol["high_max"], "new": cap,
                                     "where": "QC 정책 테이블 (market.db policy) > high_max"}],
                             f"{day:%Y-%m-%d} 매입 분할 규칙: {reason}",
                             evidence=f"고가 이익/원가 {pct(hr)}, 저가 {pct(lr)}, 고가 보유 {len(held_high)}개 중 24시간 "
                                      f"미판매 {len(stale_high)}개, 고가 환불 {len(high_refund)}건",
                             version=("QC 테이블 이전 행", "새 행"), next_at="내일 00:00", log=log)
    except Exception as e:
        log(f"[policy_notify] qc: {type(e).__name__}: {e}")
    __import__("decisions").record("ceo", "buying_split", "high_share/high_max", {"high_share": share, "high_max": cap},
                                   f"{day:%Y-%m-%d}: {reason}")

    money = day_money(db, t0, t1, s)
    hc = high_cancel_reasons(db, t0, t1, catalog)
    sofa = sofa_trial_text(sofa_trial_numbers(db, catalog, t1), now=t1).replace(chr(10), "; ")
    import alerts as _al
    swaps, off_min = _al.tunnel_swaps(t0, t1)
    tunnel = f"터널 교체 {swaps}회, 오프라인 합계 {off_min}분 (kit re-registered within 2 min: no alert)"
    ones, tops_md = None, []
    try:                                              # bundle 2 D (11/07): 1-star causes, top 10 shops
        from depts import insights
        ones = insights.one_star(db, t0, t1)
        tops_md = insights.top_shops_md(insights.top_shops(db, t0, t1, catalog, day))
    except Exception as e:
        log(f"[report] insights failed: {type(e).__name__}: {e}")
    lines = [f"# Choo Shop daily report {day:%Y-%m-%d}", "",
             f"- sales {s['sales']}, revenue {s['revenue']:,}, profit {s['profit']:,}",
             f"- profit / cost: low-price {pct(lr)} ({s['low_profit']:,} on {s['low_cost']:,}), "
             f"high-price {pct(hr)} ({s['high_profit']:,} on {s['high_cost']:,})",
             f"- stock at cost {stock:,}, cash {int(inv.get('balance', 0)):,}",
             f"- {perishable_line(perishable_numbers(db, t0, t1, catalog, inv))}", "",
             "## Cash, stock, storage, profit", ""] + money_md(money, catalog) + ["", f"- {high_cancel_line(hc)}", f"- {sofa}", f"- {tunnel}", ""] +             cap_compare_md(cap_compare(db, t1)) + ["",
             "## Sales by category", ""] + \
            [f"- {k}: {v}" for k, v in sorted(s["cats"].items(), key=lambda kv: -kv[1])] + \
            ["", "## Top 3 SKUs by profit", ""] + \
            [f"- {v['name']} ({k}): {v['n']} sold, profit {v['profit']:,}" for k, v in top] + \
            ["", "## Slowest stock (oldest on the shelf)", ""] + \
            [f"- {v['name']} ({k}): {v['n']} units, {v['cost']:,} at cost, "
             f"{(now - v['oldest']) / 3600:.0f} h on the shelf" for k, v in slow] + \
            ["", "## Broken / refunds", ""] + \
            ([f"- ledger #{b[0]} {b[1]}: broke {b[2]:.1f} h after sale, refunded {bool(b[3])}" for b in broken] or ["- none"]) + \
            ["", "## Buying split", "", f"- high-price share {pol['high_share']:.0%} -> {share:.0%}, "
             f"high-price units held at most {pol['high_max']} -> {cap} ({reason})", ""] + extras_md(db, t0, t1, catalog) +             (["", "## 1-star results by cause", "", f"- {insights.one_star_line(ones)}"]
             + [f"  - {k}: {ex}" for k, v in ones["examples"].items() for ex in v] if ones else []) + tops_md
    os.makedirs(REPORTS, exist_ok=True)
    path = os.path.join(REPORTS, f"daily_{day:%Y%m%d}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('daily_done', ?)", (day.strftime("%Y%m%d"),))
    db.commit()
    log(f"[report] daily {day:%Y-%m-%d}: {money_line(money)}")
    try:                                              # the once-a-day message for the person
        import alerts
        import policy as _pol
        ceo = json.load(open(os.path.join(HERE, "ceo_state.json"), encoding="utf-8")).get("mode", "?")
        n_alerts = 0
        try:
            n_alerts = sum(1 for ln in open(alerts.PATH, encoding="utf-8")
                           if '"level": "urgent"' in ln and t0 <= json.loads(ln)["ts"] < t1)
        except OSError:
            pass
        alerts.send("daily", f"daily:{day:%Y%m%d}", chr(10).join([
            f"{day:%m-%d} sold {s['sales']}, revenue {s['revenue']:,}, sales profit {s['profit']:,}",
            f"cash {money.get('cash1', 0):,}, stock at cost {money.get('stock1', 0):,}, book change "
            f"{money.get('book_change', 0):+,}, storage ~{money.get('storage', 0):,}",
            f"STOCK_CAP runs {money['cap_runs']}, budget held back {money['cap_budget_cut']:,}",
            cap_compare_line(cap_compare(db, t1)) or "no STOCK_CAP change",
            high_cancel_line(hc),
            sofa,
            tunnel,
            insights.one_star_line(ones) if ones else "1-star summary failed",
            f"CEO {ceo}, policy v{_pol.version()}; buying split {share:.0%} / {cap} ({reason})",
            f"urgent alerts that day: {n_alerts}; report: reports/{os.path.basename(path)}"]), log)
    except Exception as e:
        log(f"[report] daily message: {type(e).__name__}")
    auto_commit(day, log)                             # logs and reports of the day -> origin week-08
    log(f"[report] daily {day:%Y-%m-%d}: sold {s['sales']}, profit {s['profit']:,} -> {os.path.basename(path)}; "
        f"high share {share:.0%}, max {cap} ({reason})")


def tick(inv, catalog, log=print):
    db = connect()
    try:
        policy(db)
        hourly(db, inv, catalog, log)
        daily(db, inv, catalog, log)
    finally:
        db.close()


def cap_change():
    """(epoch, new cap) of the latest STOCK_CAP change, or None."""
    from limits import STOCK_CAP_CHANGES
    return max(STOCK_CAP_CHANGES) if STOCK_CAP_CHANGES else None


def money_window(db, t0, t1):
    """Buying, sales, storage and Book over [t0, t1) from my_stock, settles and the 5-min cash table."""
    bought = db.execute("SELECT COUNT(*), COALESCE(SUM(cost), 0) FROM my_stock WHERE supplied_at>=? AND supplied_at<?",
                        (t0, t1)).fetchone()
    sold = db.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM settles WHERE shop=? AND ts>=? AND ts<?",
                      (ME, t0, t1)).fetchone()
    rows = db.execute("SELECT ts, balance, stock_cost, storage_per_day FROM cash WHERE ts>=? AND ts<? ORDER BY ts",
                      (t0, t1)).fetchall()
    out = {"hours": round((t1 - t0) / 3600, 1), "bought_units": bought[0], "bought_cost": bought[1],
           "sales": sold[0], "revenue": sold[1], "storage": None, "book_change": None, "stock_start": None,
           "stock_end": None}
    if len(rows) >= 2:
        out["storage"] = round(sum(r[3] or 0 for r in rows) / len(rows) * (rows[-1][0] - rows[0][0]) / 86400)
        out["book_change"] = (rows[-1][1] + rows[-1][2]) - (rows[0][1] + rows[0][2])
        out["stock_start"], out["stock_end"] = rows[0][2], rows[-1][2]
    return out


def cap_compare(db, now, max_h=72):
    """The latest STOCK_CAP change: the same number of hours before and after it (up to max_h, and no more than
    have passed since the change)."""
    c = cap_change()
    if not c or now <= c[0]:
        return None
    d = min(now - c[0], max_h * 3600)
    return {"at": c[0], "cap": c[1], "before": money_window(db, c[0] - d, c[0]),
            "after": money_window(db, c[0], c[0] + d)}


def cap_compare_md(cc):
    if not cc:
        return []
    fmt = lambda v: "-" if v is None else f"{v:,}" if isinstance(v, int) else str(v)
    keys = [("bought_cost", "매입액 (원가)"), ("bought_units", "매입 수량"), ("sales", "판매 수"), ("revenue", "revenue"),
            ("storage", "보관비"), ("book_change", "Book 변화")]
    lines = [f"## STOCK_CAP -> {cc['cap']:,} ({datetime.fromtimestamp(cc['at']):%m-%d %H:%M}): "
             f"{cc['after']['hours']} h before vs after", "", "| | before | after |", "|---|---|---|"]
    lines += [f"| {label} | {fmt(cc['before'][k])} | {fmt(cc['after'][k])} |" for k, label in keys]
    return lines + [""]


def cap_compare_line(cc):
    if not cc:
        return ""
    b, a = cc["before"], cc["after"]
    f = lambda v: "-" if v is None else f"{v:,}"
    return (f"cap {cc['cap'] // 10**6}M, {a['hours']} h before/after: bought {f(b['bought_cost'])}/{f(a['bought_cost'])}, "
            f"sold {b['sales']}/{a['sales']}, revenue {f(b['revenue'])}/{f(a['revenue'])}, storage "
            f"{f(b['storage'])}/{f(a['storage'])}, book {f(b['book_change'])}/{f(a['book_change'])}")


# --- after the daily summary: commit and push the day's logs and reports (decision 11/06)
AUTO_PATHS = ["logs", "reports", "memory.jsonl", "ceo_state.json", "policy.json"]   # relative to week-08/
SENSITIVE = (".pem", ".key", ".env", "market.db", "market_stats.json", "/cache/")


def auto_commit(day, log=print, dry=False):
    """git add only AUTO_PATHS, refuse if anything staged looks sensitive, commit, push origin week-08.
    Never raises; a failure is logged (and alerts.send urgent via restock's log)."""
    import subprocess
    run = lambda *a: subprocess.run(["git", *a], cwd=HERE, capture_output=True, text=True, timeout=120)
    try:
        if run("branch", "--show-current").stdout.strip() != "week-08":
            log("[report] auto commit skipped: not on branch week-08")
            return "skipped"
        run("add", "--", *[p for p in AUTO_PATHS if os.path.exists(os.path.join(HERE, p))])
        staged = run("diff", "--cached", "--name-only").stdout.split()
        bad = [f for f in staged if any(s in f.replace("\\", "/") for s in SENSITIVE) or "/week-08/" not in f]
        if bad or dry:
            run("reset", "-q", "--", *[":/" + f for f in staged]) if staged else None
            if bad:
                log(f"[report] auto commit failed: sensitive or outside paths staged {bad[:5]}")
                return "refused"
            return staged
        if not staged:
            return "nothing"
        msg = (f"week-08: logs and reports through {day:%m-%d} (automatic, after the daily summary)\n\n"
               "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
        c = run("commit", "-q", "-m", msg)
        if c.returncode:
            log(f"[report] auto commit failed: {c.stderr.strip()[:200]}")
            return "commit failed"
        p = run("push", "-q", "origin", "week-08")
        if p.returncode:
            log(f"[report] auto push failed: {p.stderr.strip()[:200]}")
            return "push failed"
        head = run("log", "--oneline", "-1").stdout.strip()
        log(f"[report] auto commit + push: {head[:60]} ({len(staged)} files)")
        return head
    except Exception as e:
        log(f"[report] auto commit failed: {type(e).__name__}")
        return "error"


REASONS = [("price", r"price|budget|expensive|too high|come down|agree|value|walked away|asks \[|my offers"),
           ("stock", r"no stock|out of stock|not in stock|none of that|sold out|unavailable"),
           ("delay", r"time ?out|timed out|slow|no answer|did not respond|unresponsive|no reply|took too long")]


def high_cancel_reasons(db, t0, t1, catalog):
    """High-price negotiations (unit cost > HIGH_MIN) that did not end in a receipt, and the buyers' reasons from
    their review text (results naming my shop without a receipt whose text names a high-price SKU)."""
    import re
    nego = db.execute("SELECT COUNT(*), SUM(outcome='cancelled'), SUM(outcome='receipt') FROM shop_deals "
                      "WHERE cost>? AND ts>=? AND ts<?", (HIGH_MIN, t0, t1)).fetchone()
    high = {k for k, c in catalog.items() if (c.get("list_price") or 0) * 0.6 > HIGH_MIN}
    out = {"negotiations": nego[0], "cancelled": nego[1] or 0, "receipts": nego[2] or 0,
           "reviews": 0, "price": 0, "stock": 0, "delay": 0, "other": 0}
    for (text,) in db.execute("SELECT review FROM my_results WHERE has_receipt=0 AND ts>=? AND ts<?", (t0, t1)):
        skus = set(re.findall(r"G[0-9a-f]{8}", text or ""))
        if not skus & high:
            continue
        out["reviews"] += 1
        kind = next((k for k, p in REASONS if re.search(p, text, re.I)), "other")
        out[kind] += 1
    return out


def high_cancel_line(h):
    n = h["reviews"]
    share = lambda k: f"{h[k]} ({h[k] / n:.0%})" if n else "0"
    return (f"high-price negotiations {h['negotiations']}, cancelled {h['cancelled']}, receipts {h['receipts']}; "
            f"reasons from the {n} reviews that name a high-price SKU (most reviews name none): price {share('price')}, stock {share('stock')}, "
            f"delay {share('delay')}, other {share('other')}")


SOFA_CAT = "소파/의자/스툴"


def sofa_trial_numbers(db, catalog, now=None):
    """The sofa trial so far (policy purchasing.sofa_trial_start/end): units bought, sold, hours held, profit,
    storage (1% of cost a day while held), my sofa negotiations and the buyers' reasons where a review names one."""
    import re
    import policy
    now = now or time.time()
    t0, t1 = policy.get("purchasing", "sofa_trial_start"), policy.get("purchasing", "sofa_trial_end")
    if not t0:
        return None
    sofas = {k for k, c in catalog.items() if (c.get("category") or "").split(">")[-1] == SOFA_CAT}
    q = ",".join("?" * len(sofas)) or "''"
    units = db.execute(f"SELECT unit, sku, cost, supplied_at FROM my_stock WHERE supplied_at>=? AND sku IN ({q})",
                       (t0, *sofas)).fetchall()
    out = {"start": t0, "end": t1, "bought": len(units), "cost": sum(u[2] or 0 for u in units), "sold": 0,
           "profit": 0, "hours": [], "storage": 0, "negotiations": 0, "cancelled": 0, "price": 0, "stock": 0,
           "delay": 0, "other": 0}
    for unit, sku, cost, sup in units:
        s = db.execute("SELECT ts, amount FROM settles WHERE unit=?", (unit,)).fetchone()
        end = s[0] if s else now
        out["hours"].append((end - sup) / 3600)
        out["storage"] += round((cost or 0) * 0.01 * (end - sup) / 86400)
        if s:
            out["sold"] += 1
            out["profit"] += s[1] - (cost or 0)
    n = db.execute(f"SELECT COUNT(*), SUM(outcome='cancelled') FROM shop_deals WHERE ts>=? AND sku IN ({q})",
                   (t0, *sofas)).fetchone()
    out["negotiations"], out["cancelled"] = n[0], n[1] or 0
    for (text,) in db.execute("SELECT review FROM my_results WHERE has_receipt=0 AND ts>=?", (t0,)):
        if set(re.findall(r"G[0-9a-f]{8}", text or "")) & sofas:
            out[next((k for k, p in REASONS if re.search(p, text, re.I)), "other")] += 1
    return out


def sofa_trial_text(o, final=False, now=None):
    if not o:
        return "sofa trial: not started"
    now = now or time.time()
    h = o["hours"]
    state = "ended" if now >= o["end"] else f"{(now - o['start']) / 3600:.0f} h of 24"
    lines = [f"sofa trial {datetime.fromtimestamp(o['start']):%m-%d %H:%M} - {datetime.fromtimestamp(o['end']):%m-%d %H:%M} ({state})",
             f"bought {o['bought']} (cost {o['cost']:,}), sold {o['sold']}, avg hours held {sum(h) / len(h):.1f}" if h else
             f"bought 0, sold 0",
             f"profit on sold {o['profit']:,}, storage ~{o['storage']:,}",
             f"sofa negotiations {o['negotiations']}, cancelled {o['cancelled']}; reasons in reviews naming a sofa: "
             f"price {o['price']}, stock {o['stock']}, delay {o['delay']}, other {o['other']}"]
    if final:
        lines.append("확대 / 유지 / 중단 중 결정 필요 (no decision: sofa buying stops; sofas held keep selling)")
    return "\n".join(lines)
