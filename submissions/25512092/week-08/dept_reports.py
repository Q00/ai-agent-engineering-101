"""Department reports: one JSON per department in reports/dept/, rewritten every 5 minutes by restock.py.

    {"dept", "ts", "policy_version", "window_h", "metrics": {...}, "issues": [{"level", "text", "refs"}],
     "requests": [{"key", "to", "reason"}]}

Read-only over market.db, inventory.json and logs/decisions.jsonl. Numbers and ledger/unit references only: no
forum or review text goes into a report (the CEO reads these, and forum text is written by other people).
"""
import json
import os
import time

import policy
from limits import CASH_WARN, STOCK_CAP
from market_db import ME, connect, meta

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "reports", "dept")
EVERY_S = 300
WINDOW_H = 6
HIGH_MIN = 100_000


def _decisions(since):
    out = []
    try:
        with open(os.path.join(HERE, "logs", "decisions.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("ts", 0) >= since:
                    out.append(d)
    except OSError:
        pass
    return out


def window(db, t0, t1, catalog=None, exclude_units=()):
    """Shop and buyer numbers for [t0, t1): the CEO's inputs and the step-7 comparison (ceo_guard).
    exclude_units: my units whose sales are left out of sales / revenue / profit (the guard drops a stopped sofa
    trial's units from both windows)."""
    ex = list(exclude_units)
    sales = db.execute("SELECT COUNT(*), COALESCE(SUM(s.amount), 0), COALESCE(SUM(s.amount - m.cost), 0) "
                       "FROM settles s JOIN my_stock m ON m.unit=s.unit WHERE s.shop=? AND s.ts>=? AND s.ts<?"
                       + (f" AND s.unit NOT IN ({','.join('?' * len(ex))})" if ex else ""),
                       (ME, t0, t1, *ex)).fetchone()
    stars = db.execute("SELECT COUNT(stars), AVG(stars), SUM(stars=1) FROM my_results WHERE ts>=? AND ts<?",
                       (t0, t1)).fetchone()
    acct = db.execute("SELECT COUNT(*), AVG(m) FROM (SELECT AVG(stars) AS m FROM my_results WHERE ts>=? AND ts<? "
                      "AND stars IS NOT NULL GROUP BY buyer)", (t0, t1)).fetchone()   # Patch 3.4: one mean per account
    deals = db.execute("SELECT COUNT(*), SUM(outcome='receipt'), SUM(outcome='cancelled') FROM shop_deals "
                       "WHERE ts>=? AND ts<?", (t0, t1)).fetchone()
    b = db.execute("SELECT COUNT(*), SUM(outcome='deal') FROM buyer_deals WHERE ts>=? AND ts<? AND outcome!='skip'",
                   (t0, t1)).fetchone()
    surplus, shares = 0, []                           # list price - paid; share of list price kept (Saved)
    for sku, amount in db.execute("SELECT sku, amount FROM settles WHERE buyer=? AND ts>=? AND ts<?", (ME, t0, t1)):
        lp = ((catalog or {}).get(sku) or {}).get("list_price")
        surplus += (lp - amount) if lp else 0
        if lp:
            shares.append((lp - amount) / lp)
    paid = db.execute("SELECT COUNT(*) FROM settles WHERE buyer=? AND ts>=? AND ts<?", (ME, t0, t1)).fetchone()[0]
    return {"hours": round((t1 - t0) / 3600, 2), "sales": sales[0], "revenue": sales[1], "profit": sales[2],
            "reviews": stars[0], "stars": round(acct[1], 2) if acct[1] is not None else None, "review_accounts": acct[0],
            "stars_per_review": round(stars[1], 2) if stars[1] is not None else None,
            "one_star": stars[2] or 0, "negotiations": deals[0], "receipts": deals[1] or 0,
            "cancelled": deals[2] or 0, "buyer_tries": b[0], "buyer_deals": b[1] or 0, "buyer_paid": paid,
            "buyer_surplus": surplus, "buyer_saved_sum": round(sum(shares), 3),
            "buyer_saved_avg": round(sum(shares) / len(shares), 4) if shares else None}


def build(inv, catalog, now=None):
    now = now or time.time()
    t0 = now - WINDOW_H * 3600
    db = connect()
    try:
        w = window(db, t0, now, catalog)
        units = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
        stock = sum(u.get("cost", 0) for u in units)
        cash = int(inv.get("balance", 0))
        storage = round(stock * float(inv.get("storage_per_day") or 0.01))
        dec = _decisions(t0)
        reports = {}

        # purchasing
        bought = db.execute("SELECT COUNT(*), COALESCE(SUM(cost), 0) FROM my_stock WHERE supplied_at>=?", (t0,)).fetchone()
        try:
            cap = db.execute("SELECT COUNT(*), COALESCE(SUM(budget_free - budget), 0) FROM cap_runs WHERE ts>=?",
                             (t0,)).fetchone()
        except Exception:
            cap = (0, 0)
        runs = sum(1 for d in dec if d["dept"] == "purchasing" and d["action"] in ("buy", "no_restock", "cap"))
        issues = []
        if cap[0]:
            issues.append({"level": "info", "text": f"STOCK_CAP cut or blocked {cap[0]} runs, "
                                                    f"{cap[1]:,} of budget held back", "refs": []})
        reports["purchasing"] = {"metrics": {"units_bought": bought[0], "cost_bought": bought[1], "cap_runs": cap[0],
                                             "cap_budget_held": cap[1], "plan_decisions": runs,
                                             "dedup": sum(1 for d in dec if d["action"] == "dedup")},
                                 "issues": issues}

        # sales
        conv = w["receipts"] / w["negotiations"] if w["negotiations"] else None
        asks = [d for d in dec if d["dept"] == "sales" and d["action"] == "first_ask"]
        reports["sales"] = {"metrics": {"sales": w["sales"], "revenue": w["revenue"], "profit": w["profit"],
                                        "negotiations": w["negotiations"], "receipts": w["receipts"],
                                        "conversion": round(conv, 3) if conv is not None else None,
                                        "first_asks": len(asks)},
                            "issues": [] if conv is None or conv >= 0.1 else
                            [{"level": "warn", "text": f"conversion {conv:.0%} under 10%", "refs": []}]}

        # logistics
        old_goods = [u for u in units if u.get("kind") != "food" and now - (u.get("supplied_at") or now) > 48 * 3600]
        stale_high = [u for u in units if u.get("cost", 0) > HIGH_MIN and now - (u.get("supplied_at") or now) > 24 * 3600]
        near = [u for u in units if u.get("expires_at") and u["expires_at"] - now < 12 * 3600]
        import sales_report
        per = sales_report.perishable_numbers(db, now - 86400, now, catalog, inv)
        issues = []
        if stock >= __import__('limits').stock_cap_now(inv.get('balance', 0), stock)[0] * 0.95:
            issues.append({"level": "info", "text": f"stock at cost {stock:,} within 5% of STOCK_CAP", "refs": []})
        if stale_high:
            issues.append({"level": "warn", "text": f"{len(stale_high)} high-price units unsold > 24 h",
                           "refs": [u["unit"] for u in stale_high[:5]]})
        if per["wasted"]:
            issues.append({"level": "warn", "text": f"{per['wasted']} perishable units wasted in 24 h "
                                                    f"({per['waste_cost']:,})", "refs": []})
        reports["logistics"] = {"metrics": {"cash": cash, "stock_cost": stock, "units": len(units),
                                            "storage_per_day": storage, "near_expiry_12h": len(near),
                                            "goods_over_48h": len(old_goods), "high_over_24h": len(stale_high),
                                            "perishable_24h": per},
                                "disposal_candidates": [{"unit": u["unit"], "sku": u["sku"], "cost": u["cost"],
                                                         "hours": round((now - u["supplied_at"]) / 3600)}
                                                        for u in sorted(old_goods + stale_high,
                                                                        key=lambda u: u.get("supplied_at") or now)[:10]],
                                "issues": issues}

        # cs
        posts = [d for d in dec if d["dept"] == "cs" and d["action"] == "post"]
        guards = [d for d in dec if d["dept"] == "cs" and d["action"] == "guard"]
        share1 = w["one_star"] / w["reviews"] if w["reviews"] else 0
        reports["cs"] = {"metrics": {"reviews": w["reviews"], "stars": w["stars"], "review_accounts": w["review_accounts"],
                                     "stars_per_review": w["stars_per_review"], "one_star": w["one_star"],
                                     "cancelled": w["cancelled"], "posts": len(posts), "guards": len(guards)},
                         "issues": [] if share1 < 0.3 else
                         [{"level": "urgent" if share1 >= 0.5 and w["reviews"] >= 20 else "warn",
                           "text": f"{w['one_star']} of {w['reviews']} reviews in {WINDOW_H} h are 1 star", "refs": []}]}

        # qc
        no_cost = db.execute("SELECT COUNT(*) FROM settles s WHERE s.shop=? AND s.ts>=? AND "
                             "(s.unit IS NULL OR s.unit NOT IN (SELECT unit FROM my_stock))", (ME, t0)).fetchone()[0]
        unpaid = db.execute("SELECT COUNT(*) FROM shop_deals d WHERE d.outcome='receipt' AND d.ts>=? AND d.ts<? AND NOT "
                            "EXISTS (SELECT 1 FROM settles s WHERE s.unit=d.unit AND s.ts>=d.ts-60)",
                            (t0, now - 900)).fetchone()[0]
        last_refresh = float(meta(db, "refreshed", 0))
        issues = []
        if no_cost:
            issues.append({"level": "warn", "text": f"{no_cost} of my sales have no recorded unit cost", "refs": []})
        if now - last_refresh > 1800:
            issues.append({"level": "urgent", "text": f"market.db not refreshed for {(now - last_refresh) / 60:.0f} min",
                           "refs": []})
        reports["qc"] = {"metrics": {"sales_without_cost": no_cost, "receipts_unpaid": unpaid,
                                     "decisions_logged": len(dec), "refresh_age_s": round(now - last_refresh)},
                         "issues": issues}

        # buyer (separate business; the CEO's buyer section)
        reports["buyer"] = {"metrics": {"tries": w["buyer_tries"], "deals": w["buyer_deals"], "paid": w["buyer_paid"],
                                        "surplus": w["buyer_surplus"], "saved_sum": w["buyer_saved_sum"],
                                        "saved_avg": w["buyer_saved_avg"]}, "issues": []}

        if cash < CASH_WARN:
            reports["logistics"]["issues"].append({"level": "urgent", "text": f"cash {cash:,} < {CASH_WARN:,}",
                                                   "refs": []})
    finally:
        db.close()
    for dept, r in reports.items():
        r.update(dept=dept, ts=round(now), policy_version=policy.version(), window_h=WINDOW_H)
        r.setdefault("requests", [])
    return reports


def tick(inv, catalog, log=print):
    db = connect()
    try:
        now = time.time()
        if now - float(meta(db, "dept_ts", 0)) < EVERY_S:
            return None
        db.execute("INSERT OR REPLACE INTO meta VALUES ('dept_ts', ?)", (str(now),))
        db.commit()
    finally:
        db.close()
    reports = build(inv, catalog, now)
    os.makedirs(OUT, exist_ok=True)
    for dept, r in reports.items():
        tmp = os.path.join(OUT, f"{dept}.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(OUT, f"{dept}.json"))
    n = sum(len(r["issues"]) for r in reports.values())
    log(f"[dept] reports written ({', '.join(reports)}), {n} issues")
    return reports


def load():
    out = {}
    for f in os.listdir(OUT) if os.path.isdir(OUT) else []:
        if f.endswith(".json"):
            try:
                out[f[:-5]] = json.load(open(os.path.join(OUT, f), encoding="utf-8"))
            except (OSError, ValueError):
                pass
    return out
