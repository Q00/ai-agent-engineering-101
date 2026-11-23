"""The full evidence of a CEO policy change, for its Telegram notice (person's decision 11/07 ~16:20): every number
the rule used, the period they come from, and one line on the expected effect. depts/ceo_policy.tick calls
detail() for each key it changed in live mode; policy_notify puts the lines in the message.

  spend_share           cash, stock at cost, STOCK_CAP (and its 90% / half marks), 6 h profit, sales and revenue
                        (dept_reports window), the run budget before and after
  perishable_trial_qty  each '부패주의' unit wasted in the last 24 h: name, SKU, cost, bought, shelf life, expired at,
                        whether it was a trial buy (purchasing 'perishable rule trial' decision for that item before
                        it arrived); bought / sold / wasted totals
  high_share/high_max   yesterday's low / high price sales (count, cost, profit, profit/cost), the high-price units held
                        and those unsold > 24 h (name, cost, hours), high-price refunds, the run budget split
"""
import json
import os
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _t(ts):
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def _trial_buys(t0):
    """{item name: [decision ts]} of purchasing 'buy' decisions under the perishable trial rule since t0."""
    out = {}
    try:
        with open(os.path.join(HERE, "logs", "decisions.jsonl"), encoding="utf-8") as f:
            for ln in f:
                if "perishable rule 'trial'" not in ln:
                    continue
                d = json.loads(ln)
                if d.get("ts", 0) >= t0 and d.get("action") == "buy":
                    name = (d.get("target") or "").split("] ", 1)[-1]
                    out.setdefault(name, []).append(d["ts"])
    except (OSError, ValueError):
        pass
    return out


def wasted_units(db, t0, t1, catalog, inv):
    """The '부패주의' units counted as wasted by logistics.perishable_numbers, one dict each."""
    from depts.logistics import perishable
    from market_db import ME  # noqa: F401
    skus = {k for k, c in catalog.items() if perishable(c)}
    held = {u["unit"] for u in (inv or {}).get("units", [])}
    trials = _trial_buys(t0 - 7 * 86400)
    now = time.time()
    out = []
    for unit, sku, name, cost, supplied in db.execute(
            "SELECT m.unit, m.sku, m.name, m.cost, m.supplied_at FROM my_stock m WHERE NOT EXISTS "
            "(SELECT 1 FROM settles s WHERE s.unit=m.unit)"):
        if sku not in skus or unit in held or not supplied:
            continue
        shelf = catalog[sku].get("shelf_hours") or 0
        exp = supplied + shelf * 3600
        if t0 <= exp < t1 and exp <= now:
            # decision targets keep only the first 60 characters of the item: match the name by prefix
            trial = any(supplied - 1800 <= ts <= supplied + 60 for tn, tss in trials.items()
                        if tn and (name or "").startswith(tn) for ts in tss)
            out.append({"unit": unit, "sku": sku, "name": (name or sku)[:30], "cost": cost or 0, "bought": supplied,
                        "shelf_h": shelf, "expired": exp, "trial": trial})
    return out


def detail(sec, k, old, new, inv, catalog, reports, now=None):
    """{"numbers": [...lines], "period": str, "effect": str} for one changed CEO key."""
    from limits import CASH_RESERVE, STOCK_CAP
    from market_db import connect
    now = now or time.time()
    lg = (reports.get("logistics") or {}).get("metrics") or {}
    sa = (reports.get("sales") or {}).get("metrics") or {}
    win = (reports.get("sales") or {}).get("window_h") or 6
    if k == "spend_share":
        cash, stock = lg.get("cash", 0), lg.get("stock_cost", 0)
        room = STOCK_CAP - stock
        b0 = max(0, min((cash - CASH_RESERVE) * (old or 0), room))
        b1 = max(0, min((cash - CASH_RESERVE) * new, room))
        return {"numbers": [f"현금 {cash:,}, 예비금 {CASH_RESERVE:,}, 예비금 위 현금 {cash - CASH_RESERVE:,}",
                            f"재고 원가 {stock:,} / STOCK_CAP {STOCK_CAP:,} (90% {int(STOCK_CAP * 0.9):,}, "
                            f"절반 {STOCK_CAP // 2:,}, 남은 여유 {room:,})",
                            f"최근 {win}시간 판매 {sa.get('sales', 0)}건, 매출 {sa.get('revenue', 0):,}, 이익 {sa.get('profit', 0):,}",
                            "규칙: 재고가 상한 90% 이상이면 그대로, 현금 < 2.5M이면 0.3, 이익 > 0이고 재고 < 상한 절반이고 "
                            "현금 > 3M이면 0.5, 그 외 0.4"],
                "period": f"이익·판매: 최근 {win}시간 ({_t(now - win * 3600)}~{_t(now)}), 현금·재고: 지금",
                "effect": f"run당 매입 예산 {int(b0):,} -> {int(b1):,} ({int(b1 - b0):+,}; STOCK_CAP 여유 {room:,}가 더 작으면 그만큼)"}
    if k == "perishable_trial_qty":
        db = connect()
        try:
            units = wasted_units(db, now - 86400, now, catalog, inv)
        finally:
            db.close()
        per = lg.get("perishable_24h") or {}
        lines = [f"부패주의 식품 최근 24시간: 입고 {per.get('bought', 0)}, 판매 {per.get('sold', 0)}, "
                 f"폐기 {per.get('wasted', 0)} (원가 {per.get('waste_cost', 0):,})"]
        for u in units[:10]:
            lines.append(f"폐기 {u['name']} ({u['sku']}): 원가 {u['cost']:,}, 입고 {_t(u['bought'])}, 유통 {u['shelf_h']}시간, "
                         f"만료 {_t(u['expired'])}, {'시험 매입분' if u['trial'] else '일반 매입분'}")
        if len(units) > 10:
            lines.append(f"외 {len(units) - 10}개")
        trial_cost = sum(u["cost"] for u in units if u["trial"])
        lines.append(f"그중 시험 매입분 {sum(u['trial'] for u in units)}개 (원가 {trial_cost:,})")
        lines.append("규칙 (11/07 ~16:40부터): 24시간 안에 시험 매입분 폐기가 1개 이상이면 시험 매입 1개, 없으면 2개. "
                     "일반 매입분 폐기는 이 키 판단에 쓰지 않음 (물류 보고·알림에만)")
        return {"numbers": lines, "period": f"최근 24시간 ({_t(now - 86400)}~{_t(now)}) 동안 유통기한이 끝난 유닛",
                "effect": (f"내 판매 기록이 없는 부패주의 품목의 시험 매입 {old} -> {new}개: "
                           + ("폐기 위험을 절반으로" if new < (old or 0) else "판매 기회를 늘림, 폐기 위험도 같이 늘어남"))}
    if k in ("high_share", "high_max"):
        import sales_report
        from depts.qc import STALE_SHARE
        from sales_report import HIGH_MIN
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        t1 = today.timestamp()
        t0 = t1 - 86400
        db = connect()
        try:
            s = sales_report.summarize(sales_report.my_sales(db, t0, t1), catalog)
        finally:
            db.close()
        held = [u for u in inv.get("units", []) if u.get("grade") != "expired" and u.get("cost", 0) > HIGH_MIN]
        stale = [u for u in held if now - (u.get("supplied_at") or now) > 24 * 3600]
        lr, hr = sales_report.rate(s["low_profit"], s["low_cost"]), sales_report.rate(s["high_profit"], s["high_cost"])
        lines = [f"어제 저가 판매: 원가 {s['low_cost']:,}, 이익 {s['low_profit']:,}, 이익/원가 {sales_report.pct(lr)}",
                 f"어제 고가 판매: 원가 {s['high_cost']:,}, 이익 {s['high_profit']:,}, 이익/원가 {sales_report.pct(hr)}",
                 f"고가 보유 {len(held)}개, 그중 24시간 넘게 미판매 {len(stale)}개 "
                 f"({(len(stale) / len(held)) if held else 0:.0%}; 비중 축소 기준 {STALE_SHARE:.0%})"]
        for u in sorted(stale, key=lambda u: u.get("supplied_at") or 0)[:8]:
            lines.append(f"미판매 {(u.get('name') or u['sku'])[:30]}: 원가 {u.get('cost', 0):,}, "
                         f"{(now - u['supplied_at']) / 3600:.0f}시간")
        lines.append("규칙: 미판매 비율 기준 이상이거나 고가 환불이 있으면 비중 -10%p, 고가 이익/원가 >= 저가면 비중 +10%p·보유 +1")
        cash, stock = lg.get("cash", 0), lg.get("stock_cost", 0)
        from depts.purchasing import spend_share
        b = max(0, min((cash - CASH_RESERVE) * spend_share(), STOCK_CAP - stock))
        eff = (f"run당 고가 몫 {int(b * (old or 0)):,} -> {int(b * new):,}" if k == "high_share"
               else f"고가 보유 한도 {old} -> {new}개 (지금 {len(held)}개 보유)")
        return {"numbers": lines, "period": f"이익/원가: 어제 ({(today - timedelta(days=1)):%m-%d} 00:00~24:00), 보유: 지금",
                "effect": eff}
    return {"numbers": [], "period": "", "effect": ""}
