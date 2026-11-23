"""STOCK_CAP brake (person's decision 11/07 ~20:40). While STOCK_CAP is above the brake's low value (15M), every
CHECK_S in the analyst process: if any of the three holds, STOCK_CAP goes back to 15M (limits.py, STOCK_EXIT 88%,
commit + push, no b0 check: a lower cap may change the shop case on purpose) and one urgent Telegram goes out.
  1. median supply -> sale time of my units sold in the last WINDOW_H is over MEDIAN_H (needs MIN_SALES sales)
  2. unsold units held longer than OLD_H: cost over OLD_COST (scope "all": every held unit; "since": only units
     supplied after the cap was raised)
     From 11/08 ~11:00 (person), with "old_share" in cap_brake.json instead: of the units supplied after the raise
     that are at least OLD_H old, the share of their cost still unsold is over OLD_SHARE (needs MIN_OLD_N of them;
     the 11/08 07:29 trip was 19.6% on this measure)
     From 11/09 ~19:00 (person): the share AND their unsold cost over OLD_UNSOLD_MIN (3M), judged only once their
     cost is at least MIN_OLD_COST (5M; was 20 units)
  3. cash (inventory balance, as the shop case reads it) under CASH_MIN
From 11/09 ~23:10 (person), while the automatic cap is on (limits.stock_cap_now, stock_cap_auto.json): trip 1 or 2
turns the automatic cap off (the fixed STOCK_CAP, 20M, stays: no limits.py edit needed while it is already there);
trip 3 (cash) only pauses buying ("paused" in stock_cap_auto.json, restock.py buys nothing) until cash is over
resume_cash (3M), the cap stays automatic. From limits.CAP_END_AT the scheduled end cap keeps priority.
Rules in cap_brake.json; the latest numbers in logs/cap_brake_state.json (read by the 3 h report). The brake fires
once; raising the cap again is the person's decision.
From 11/10 ~12:10 (person, "shop brakes minimised"): with "warn_only" in cap_brake.json the stock trips (1, 2) only
send a Telegram warning (decision cap_brake_warn), at most once per WARN_EVERY_S while they last: no 20M, no auto cap
off. The cash trip (buying paused until cash > 3M) still works.
"""
import json
import os
import statistics
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "cap_brake.json")
STATE = os.path.join(HERE, "logs", "cap_brake_state.json")
CHECK_S = 300
WARN_EVERY_S = 3600
ME = "choo-chu"
_last = 0.0


def rules():
    try:
        with open(RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"enabled": False}


def measure(r, now=None):
    """The three numbers the brake looks at, with the reason for each trip."""
    import sqlite3
    now = now or time.time()
    with open(os.path.join(HERE, ".market", "inventory.json"), encoding="utf-8") as f:
        inv = json.load(f)
    held = [u for u in inv.get("units", []) if u.get("grade") != "expired"]
    import dbutil  # 11/12 ~15:15: busy 10 s + retries while db_safe.json is on
    db = dbutil.connect(os.path.join(HERE, "market.db"))
    try:
        ages = [x[0] for x in db.execute(
            "SELECT s.ts - m.supplied_at FROM settles s JOIN my_stock m ON m.unit=s.unit WHERE s.shop=? AND s.ts>=? "
            "AND m.supplied_at IS NOT NULL", (ME, now - r["window_h"] * 3600))]
        el = db.execute("SELECT m.cost, EXISTS(SELECT 1 FROM settles s WHERE s.unit=m.unit AND s.shop=? AND s.ts<=?) "
                        "FROM my_stock m WHERE m.supplied_at>=? AND m.supplied_at<=?",
                        (ME, now, r.get("since") or 0, now - r["old_h"] * 3600)).fetchall()
    finally:
        db.close()
    since = r.get("since") or 0
    old = [u for u in held if now - (u.get("supplied_at") or now) > r["old_h"] * 3600
           and (r.get("old_scope", "all") == "all" or (u.get("supplied_at") or 0) >= since)]
    el_cost = sum(c or 0 for c, _ in el)
    un_cost = sum(c or 0 for c, sold in el if not sold)
    m = {"ts": round(now), "sales": len(ages),
         "old_share": round(un_cost / el_cost, 4) if el_cost else None, "old_eligible_n": len(el),
         "old_eligible_cost": el_cost, "old_unsold_cost": un_cost,
         "median_h": round(statistics.median(ages) / 3600, 2) if ages else None,
         "old_units": len(old), "old_cost": sum(u.get("cost", 0) for u in old),
         "cash": inv.get("balance", 0), "stock": sum(u.get("cost", 0) for u in held)}
    trips = []
    if m["sales"] >= r.get("min_sales", 10) and m["median_h"] > r["median_h"]:
        trips.append(f"입고→판매 중앙값 {m['median_h']:.1f}시간 > {r['median_h']}시간 (최근 {r['window_h']}시간 판매 "
                     f"{m['sales']}개)")
    if r.get("old_share") is not None:              # 11/08 ~11:00 (person): a share instead of a cost
        # 11/09 ~19:00 (person): the share AND the unsold cost over old_unsold_min (3M); judged only once the eligible
        # cost is at least min_old_cost (5M) instead of min_old_n (20) units
        enough = (m["old_eligible_cost"] >= r["min_old_cost"] if r.get("min_old_cost") is not None
                  else m["old_eligible_n"] >= r.get("min_old_n", 20))
        if enough and m["old_share"] > r["old_share"] and m["old_unsold_cost"] > r.get("old_unsold_min", 0):
            trips.append(f"상향 후 입고분 중 {r['old_h']}시간+ 미판매 비율 {m['old_share']:.1%} > {r['old_share']:.0%} "
                         f"이고 미판매 원가 {m['old_unsold_cost']:,} > {r.get('old_unsold_min', 0):,} "
                         f"(원가 {m['old_unsold_cost']:,} / {m['old_eligible_cost']:,}, {m['old_eligible_n']}개 중)")
    elif m["old_cost"] > r["old_cost"]:
        trips.append(f"{r['old_h']}시간 넘게 안 팔린 재고 원가 {m['old_cost']:,} > {r['old_cost']:,} ({m['old_units']}개"
                     + (", 상향 후 입고분만" if r.get("old_scope") == "since" else "") + ")")
    if m["cash"] < r["cash_min"]:
        trips.append(f"현금 {m['cash']:,} < {r['cash_min']:,}")
    return m, trips


def fire(r, m, trips, log=print):
    """STOCK_CAP -> r['low'] (limits.py, commit + push), the raising proposal marked, one urgent Telegram."""
    import alerts
    import decisions
    from depts import analyst, analyst_apply
    now = time.time()
    old = analyst_apply.current_limit("limits.STOCK_CAP")
    paths = analyst_apply.edit_limit("limits.STOCK_CAP", r["low"], "brake", now)
    head = analyst_apply.git_commit(list(paths), f"week-08 STOCK_CAP brake: {old:,} -> {r['low']:,} ({'; '.join(trips)})")
    if r.get("pid"):
        d = analyst.db()
        try:
            d.execute("UPDATE proposals SET status='auto_reverted', result=? WHERE id=? AND status='approved'",
                      (f"brake: {'; '.join(trips)}"[:500], r["pid"]))
            d.commit()
        finally:
            d.close()
    r.update(enabled=False, fired=round(now), fired_why=trips)
    with open(RULES, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    decisions.record("purchasing", "cap_brake", "limits.STOCK_CAP", [old, r["low"]], "; ".join(trips) + f" | {m}")
    alerts.send("urgent", f"cap_brake:{int(now)}",
                f"STOCK_CAP 자동 브레이크: {old:,} -> {r['low']:,} (커밋 {head}).\n걸린 조건: " + "; ".join(trips)
                + f"\n현재: 재고 원가 {m['stock']:,}, 현금 {m['cash']:,}, 최근 {r['window_h']}시간 판매 {m['sales']}개 "
                f"(중앙값 {m['median_h']}시간), {r['old_h']}시간+ 재고 {m['old_units']}개 {m['old_cost']:,}, "
                f"상향 후 입고분 {r['old_h']}시간+ 미판매 비율 {m['old_share']}.\n"
                "다시 올리는 건 사람 결정.", log=log)
    log(f"cap brake fired: {old:,} -> {r['low']:,}: {trips}")


def _save_auto(a):
    import limits
    tmp = limits.AUTO_CAP_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False, indent=1)
    os.replace(tmp, limits.AUTO_CAP_PATH)


def _streak(r, n):
    if (r.get("warn_streak") or 0) != n:
        r["warn_streak"] = n
        with open(RULES, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)


def confirmed(r, trips, m, log=print):
    """11/09 ~23:55 (person, overnight): a stock trip fires only on r['consecutive'] checks in a row (default 1); before
    that one Telegram warning a check (decision cap_brake_warn), nothing changes."""
    import alerts
    import decisions
    n = (r.get("warn_streak") or 0) + 1
    need = int(r.get("consecutive") or 1)
    _streak(r, n)
    if n >= need:
        return True
    decisions.record("purchasing", "cap_brake_warn", "limits.STOCK_CAP", m.get("cap"), f"check {n}/{need}: "
                     + "; ".join(trips) + " - warning only")
    alerts.send("info", f"cap_brake_warn:{int(time.time())}", f"STOCK_CAP 브레이크 경고 {time.strftime('%H:%M')} ({n}/{need}회, "
                f"아직 발동 안 함): " + "; ".join(trips) + f"\n다음 점검({CHECK_S // 60}분 뒤)에도 걸리면 발동", log=log)
    log(f"cap brake warning {n}/{need}: {trips}")
    return False


def warn_only(r, trips, m, log=print, now=None):
    """11/10 ~12:10 (person): a stock trip is a Telegram warning only; once when it starts, then hourly while it lasts."""
    import alerts
    import decisions
    now = now or time.time()
    first = not r.get("warn_streak")
    if first or now - (r.get("warn_last") or 0) >= WARN_EVERY_S:
        r.update(warn_streak=(r.get("warn_streak") or 0) + 1, warn_last=round(now))
        with open(RULES, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
        decisions.record("purchasing", "cap_brake_warn", "limits.STOCK_CAP", m.get("cap"),
                         "warning only (person 11/10): " + "; ".join(trips))
        alerts.send("info", f"cap_brake_warn:{int(now)}", f"STOCK_CAP 브레이크 경고 {time.strftime('%H:%M')} (경고만, 자동 조치 "
                    f"없음): " + "; ".join(trips) + f"\n재고 원가 {m['stock']:,}, 현금 {m['cash']:,}, 상한 {m.get('cap', 0):,}"
                    + ("" if first else " (계속 걸려 있음, 1시간마다 알림)"), log=log)
        log(f"cap brake warning only: {trips}")


def fire_auto(r, m, trips, log=print):
    """Automatic cap on, a stock trip (1 or 2): the automatic cap off, back to the fixed STOCK_CAP (r['low'])."""
    import alerts
    import decisions
    import limits
    from depts import analyst_apply
    now = time.time()
    a = limits.auto_cap_rules()
    a.update(enabled=False, paused=False, fired=round(now), fired_why=trips)
    _save_auto(a)
    fixed = analyst_apply.current_limit("limits.STOCK_CAP")
    head = None
    if fixed != r["low"]:
        paths = analyst_apply.edit_limit("limits.STOCK_CAP", r["low"], "brake", now)
        head = analyst_apply.git_commit(list(paths), f"week-08 STOCK_CAP brake (auto cap off): {fixed:,} -> {r['low']:,} "
                                                     f"({'; '.join(trips)})")
    r.update(enabled=False, fired=round(now), fired_why=trips)
    with open(RULES, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    decisions.record("purchasing", "cap_brake", "limits.STOCK_CAP", [m.get("cap"), r["low"]],
                     "auto cap off: " + "; ".join(trips) + f" | {m}")
    alerts.send("urgent", f"cap_brake:{int(now)}",
                f"STOCK_CAP 자동 브레이크: 자동 계산(현금+재고-300만, 지금 {m.get('cap', 0):,}) 끄고 {r['low']:,} 복귀"
                + (f" (커밋 {head})" if head else "") + ".\n걸린 조건: " + "; ".join(trips)
                + f"\n현재: 재고 원가 {m['stock']:,}, 현금 {m['cash']:,}, 최근 {r['window_h']}시간 판매 {m['sales']}개 "
                f"(중앙값 {m['median_h']}시간), 상향 후 입고분 {r['old_h']}시간+ 미판매 비율 {m['old_share']}.\n"
                "다시 켜는 건 사람 결정.", log=log)
    log(f"cap brake fired (auto off): {m.get('cap')} -> {r['low']:,}: {trips}")


def cash_pause(r, m, log=print):
    """Automatic cap on: cash under r['cash_min'] -> buying paused; cash over resume_cash -> resumed. One alert each."""
    import alerts
    import decisions
    import limits
    a = limits.auto_cap_rules()
    resume = a.get("resume_cash", 3_000_000)
    if not a.get("paused") and m["cash"] < r["cash_min"]:
        a.update(paused=True, paused_at=round(time.time()))
        _save_auto(a)
        decisions.record("purchasing", "cash_pause", "limits.STOCK_CAP", m["cash"], f"cash {m['cash']:,} < "
                         f"{r['cash_min']:,}: buying paused until cash > {resume:,}")
        alerts.send("urgent", f"cash_pause:{int(time.time())}", f"현금 브레이크: 현금 {m['cash']:,} < {r['cash_min']:,}, "
                    f"현금 {resume:,} 넘을 때까지 매입만 중지 (STOCK_CAP 자동 계산은 유지, 재고 원가 {m['stock']:,})", log=log)
        log(f"cash pause: cash {m['cash']:,}")
    elif a.get("paused") and m["cash"] > resume:
        a.update(paused=False, resumed_at=round(time.time()))
        _save_auto(a)
        decisions.record("purchasing", "cash_resume", "limits.STOCK_CAP", m["cash"], f"cash {m['cash']:,} > {resume:,}")
        alerts.send("info", f"cash_resume:{int(time.time())}", f"현금 브레이크 해제: 현금 {m['cash']:,} > {resume:,}, 매입 다시 시작",
                    log=log)
        log(f"cash resume: cash {m['cash']:,}")


def end_cap(log=print, now=None):
    """Scheduled (person's decision 11/07 ~22:00): from limits.CAP_END_AT, STOCK_CAP above limits.CAP_END goes down to
    it once (limits.py, commit + push, Telegram); the brake is switched off then (the cap is at its low already)."""
    import limits
    now = now or time.time()
    if now < limits.CAP_END_AT:
        return
    import alerts
    import decisions
    from depts import analyst_apply
    a = limits.auto_cap_rules()
    if a.get("enabled"):                                         # 11/09 ~23:10: the end cap has priority over auto
        a.update(enabled=False, paused=False, ended=round(now))
        _save_auto(a)
    old = analyst_apply.current_limit("limits.STOCK_CAP")       # reloads limits.py
    if old <= limits.CAP_END:
        return
    paths = analyst_apply.edit_limit("limits.STOCK_CAP", limits.CAP_END, "end", now)
    head = analyst_apply.git_commit(list(paths), f"week-08 STOCK_CAP end of round (scheduled 11/15 23:59): {old:,} -> "
                                                 f"{limits.CAP_END:,}")
    r = rules()
    if r.get("enabled"):
        r.update(enabled=False, ended=round(now))
        with open(RULES, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
    decisions.record("purchasing", "cap_end", "limits.STOCK_CAP", [old, limits.CAP_END], "scheduled 11/15 23:59 "
                     "(person's decision 11/07 ~22:00); food bought only if it lasts past the round end from now")
    alerts.send("urgent", "cap_end", f"막판 예약 적용: STOCK_CAP {old:,} -> {limits.CAP_END:,} (커밋 {head}). 지금부터 식품은 "
                "소비기한이 11/17 23:59 이후까지 남는 것만 매입, 공산품은 11/16 11:59 매입 중단까지 그대로", log=log)
    log(f"cap end: {old:,} -> {limits.CAP_END:,}")


def tick(log=print, now=None):
    global _last
    now = now or time.time()
    if now - _last < CHECK_S:
        return
    _last = now
    end_cap(log=log, now=now)
    r = rules()
    if not r.get("enabled"):
        return
    import limits
    from depts import analyst_apply
    cap = analyst_apply.current_limit("limits.STOCK_CAP")       # reloads limits.py
    m, trips = measure(r, now)
    auto = limits.auto_cap_on(now=now)
    if auto:
        cap = limits.stock_cap_now(m["cash"], m["stock"], now)[0]
    m.update(cap=cap, trips=trips, auto=auto, paused=limits.buy_paused(now=now))
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False)
    stock_trips = [t for t in trips if not t.startswith("현금 ")]
    if r.get("warn_only"):                             # 11/10 ~12:10 (person): stock trips warn only
        if stock_trips:
            warn_only(r, stock_trips, m, log, now)
        else:
            _streak(r, 0)
        if auto:
            cash_pause(r, m, log=log)
        return
    if auto:
        if stock_trips and confirmed(r, stock_trips, m, log):
            fire_auto(r, m, stock_trips, log=log)
        elif not stock_trips:
            _streak(r, 0)
            cash_pause(r, m, log=log)
        return
    if cap > r["low"] and trips:
        if confirmed(r, trips, m, log):
            fire(r, m, trips, log=log)
    else:
        _streak(r, 0)
