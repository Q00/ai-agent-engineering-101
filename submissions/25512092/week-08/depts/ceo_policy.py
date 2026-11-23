"""CEO: reads the department reports and sets the policy (policy.json). Rule-based; no LLM.

Runs from restock.py every RUN_EVERY_S. Mode (ceo_state.json):
  shadow  proposals are logged (decisions.jsonl, action shadow_policy) and nothing is applied
  live    proposals are written to policy.json (a new version with the reason)

Keys the CEO may set (step 7 opens only these):
  purchasing.high_share / high_max   once a day, the buying-split rule that sales_report.daily used:
        high-price units unsold > 24 h or a high-price refund -> share -10 %p (min 20%)
        else high-price profit/cost >= low-price profit/cost -> share +10 %p (max 60%), units held +1 (max 30;
        10 -> 20 on 11/07 ~14:45 with units held 8 -> 18; 11/07 ~16:05: max 60% / 30 with share 0.6 / units 28;
        the unsold rule counts only when >= qc.STALE_SHARE of the high-price units held are unsold > 24 h)
  purchasing.spend_share             stock within 10% of STOCK_CAP -> no change (the cap binds anyway);
        cash < 2.5M -> 0.3; 6 h profit > 0, stock < STOCK_CAP x 0.9 (half the cap until 11/09), cash > 3M -> 0.5;
        else 0.4
  purchasing.perishable_trial_qty    perishable units wasted in 24 h -> 1, else 2
A key changes at most once every KEY_HOLD_S (the daily rule once a day). Hard limits are not policy keys.

Guard (live only, hourly after GUARD_MIN_H): the live window against the same hours a day earlier. Shop profit
or sales down 30% or more, or stars (one mean per reviewing account, Patch 3.4) down 0.4 or more -> the 4 CEO keys
(LIVE_KEYS) go back to their values when step 7 started (pre_live in ceo_state.json), mode shadow, urgent alert.
Person's decision 11/07 ~14:30 (option B):
  - the revert touches the 4 CEO keys only: limits (CASH_RESERVE ...), analyst approvals and the person's keys stay
    (no logs/ceo_revert.json any more, so the analyst does not undo its approvals for a CEO revert)
  - buyer Saved is not a guard condition (the CEO keys are purchasing keys; buyer experiments run); it stays in
    the comparison as information
  - while the base hours start before MIXED_UNTIL, every comparison says the 11/07 morning changes are mixed in
    and it is for reference
"""
import json
import os
import time
from datetime import datetime

import alerts
import decisions
import dept_reports
import policy
from limits import STOCK_CAP
from market_db import ME, connect

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(HERE, "ceo_state.json")
RUN_EVERY_S = 1800
KEY_HOLD_S = 3 * 3600
GUARD_EVERY_S = 3600
GUARD_MIN_H = 3
HIGH_MIN = 100_000
LIVE_KEYS = ("purchasing.high_share", "purchasing.high_max", "purchasing.spend_share",
             "purchasing.perishable_trial_qty")
# the values a revert falls back to when pre_live is missing (None: key removed, the code's own rule applies)
DEFAULTS_CEO = {"purchasing.high_share": None, "purchasing.high_max": None, "purchasing.spend_share": 0.4,
                "purchasing.perishable_trial_qty": 2}
# the 11/07 morning changes (01:47 volume pricing / buyer offers, 02:09 buyer tactics, 10:00 breadth buying +
# CASH_RESERVE 1.0M + surge last, 11:00 code haggling + substitute / receipt rules) are in the live window and not
# in base hours that start before this time
MIXED_UNTIL = datetime(2026, 11, 7, 11, 0).timestamp()
MIXED_NOTE = "오늘(11/07) 오전 변경들이 섞여 있어 참고용"


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"mode": "shadow", "shadow_since": time.time(), "live_since": None, "auto_live_at": None,
                "runs": 0, "errors": 0, "last_run": 0, "last_guard": 0, "key_ts": {}, "split_day": None}


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def current_split(before=None):
    """The buying split in force: the CEO's policy keys when set, else the policy table (sales_report.daily's
    rule) as it stood before `before` (the daily rule adjusts the table at midnight; the CEO's daily rule starts
    from the value before that adjustment, so the two never apply the same step twice)."""
    import sales_report
    pol = sales_report.policy()
    if before is not None:
        db = connect()
        try:
            row = db.execute("SELECT high_share, high_max FROM policy WHERE ts<? ORDER BY ts DESC LIMIT 1",
                             (before,)).fetchone()
        finally:
            db.close()
        if row:
            pol = {"high_share": row[0], "high_max": row[1]}
    for k in ("high_share", "high_max"):
        if (policy.load().get("purchasing") or {}).get(k) is not None:
            pol[k] = policy.get("purchasing", k)
    return pol


def split_rule(inv, catalog, now):
    """The daily buying-split rule over yesterday (same as sales_report.daily)."""
    import sales_report
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    t1 = today.timestamp()
    t0 = t1 - 86400
    db = connect()
    try:
        s = sales_report.summarize(sales_report.my_sales(db, t0, t1), catalog)
        refunds = db.execute("""SELECT COUNT(*) FROM broken b LEFT JOIN settles s ON s.sku=b.sku AND s.buyer=b.buyer
                                AND s.shop=? LEFT JOIN my_stock m ON m.unit=s.unit
                                WHERE b.shop=? AND b.ts>=? AND b.ts<? AND b.refunded=1 AND m.cost>?""",
                             (ME, ME, t0, t1, HIGH_MIN)).fetchone()[0]
    finally:
        db.close()
    from depts.qc import STALE_SHARE
    held = [u for u in inv.get("units", []) if u.get("grade") != "expired" and u.get("cost", 0) > HIGH_MIN]
    stale = [u for u in held if now - (u.get("supplied_at") or now) > 24 * 3600]
    stale = stale if held and len(stale) / len(held) >= STALE_SHARE else []
    lr = sales_report.rate(s["low_profit"], s["low_cost"])
    hr = sales_report.rate(s["high_profit"], s["high_cost"])
    cur = current_split(before=t1)
    share, cap = cur["high_share"], cur["high_max"]
    if stale or refunds:
        return {"high_share": max(0.2, round(share - 0.1, 2)), "high_max": cap}, \
            f"{today - __import__('datetime').timedelta(days=1):%m-%d}: {len(stale)} high-price units unsold > 24 h, {refunds} high-price refunds"
    if hr is not None and (lr is None or hr >= lr):
        from depts import high_caps               # 11/09 ~14:35: ceilings from high_caps.json (0.6/40 or 0.7/60)
        sc, mc = high_caps.get()
        return {"high_share": min(sc, round(share + 0.1, 2)), "high_max": min(mc, cap + 1)}, \
            f"high profit/cost {sales_report.pct(hr)} >= low {sales_report.pct(lr)}"
    return None, f"no change (high {sales_report.pct(hr)}, low {sales_report.pct(lr)})"


def trial_rule(inv, catalog, now):
    """perishable_trial_qty (person's decision 11/07 ~16:40): only '부패주의' units wasted in the last 24 h that came
    from a trial buy count (1 if any, else 2); normal-buy waste stays in the logistics report and its alert."""
    from depts import ceo_detail
    db = connect()
    try:
        units = ceo_detail.wasted_units(db, now - 86400, now, catalog, inv)
    finally:
        db.close()
    t = [u for u in units if u["trial"]]
    return (1 if t else 2), (f"trial-buy perishable units wasted in 24 h: {len(t)} (normal-buy waste {len(units) - len(t)} "
                             f"not counted)")


def proposals(inv, catalog, reports, st, now):
    """[(section, key, value, reason)] that differ from the policy in force."""
    out = []
    lg = (reports.get("logistics") or {}).get("metrics") or {}
    sa = (reports.get("sales") or {}).get("metrics") or {}
    stock, cash = lg.get("stock_cost", 0), lg.get("cash", 0)

    day = datetime.now().strftime("%Y%m%d")
    if st.get("split_day") != day:
        st["split_day"] = day
        new, why = split_rule(inv, catalog, now)
        cur = current_split()
        from depts.qc import split_floor
        fl = split_floor(now)                        # 11/07 ~16:10: 24 h after buying expansion 2, no lowering
        if new:
            for k, v in new.items():
                if fl and v < fl[k]:
                    decisions.record("ceo", "held", f"purchasing.{k}", v, f"daily buying split ({why}) would lower "
                                     f"{k} below {fl[k]}: held for 24 h after buying expansion 2")
                    continue
                if v != cur[k]:
                    out.append(("purchasing", k, v, f"daily buying split: {why}"))
        decisions.record("ceo", "rule", "buying_split", new, why)

    import limits
    spend, why = spend_rule(stock, cash, sa.get("profit", 0), limits.stock_cap_now(cash, stock)[0])
    if spend is not None and spend != policy.get("purchasing", "spend_share"):
        out.append(("purchasing", "spend_share", spend, why))

    trial, why = trial_rule(inv, catalog, now)
    if trial != policy.get("purchasing", "perishable_trial_qty"):
        out.append(("purchasing", "perishable_trial_qty", trial, why))

    held = []
    for sec, k, v, why in out:
        full = f"{sec}.{k}"
        if full not in LIVE_KEYS:
            continue
        if k not in ("high_share", "high_max") and now - st["key_ts"].get(full, 0) < KEY_HOLD_S:
            continue
        held.append((sec, k, v, why))
    return held


def anchor_start(L, now):
    """Where the compared window starts (anchor() without the GUARD_MIN_H check)."""
    from limits import STOCK_CAP_CHANGES
    mine = analyst_change_times()
    a = L
    for t, _ in STOCK_CAP_CHANGES:
        if any(abs(t - m) < 900 for m in mine):
            continue
        if L - 86400 < t < now:
            a = max(a, t + 86400)
    return a


def anchor(L, now):
    """Start of the live window the guard compares (option B, 11/06). Normally live_since. When a STOCK_CAP change
    falls within a day before it (or after it), the window starts 24 h after the latest such change, so the base
    hours (the same hours a day earlier) are all after the change: (start, None), or (None, reason) while the
    window is still shorter than GUARD_MIN_H."""
    from limits import STOCK_CAP_CHANGES
    mine = analyst_change_times()
    a = L
    for t, _ in STOCK_CAP_CHANGES:
        if any(abs(t - m) < 900 for m in mine):
            continue                               # an analyst approval: checked 3 h before vs after instead
        if L - 86400 < t < now:
            a = max(a, t + 86400)
    if now - a < GUARD_MIN_H * 3600:
        return None, (f"comparison starts {datetime.fromtimestamp(a):%m-%d %H:%M} (24 h after the STOCK_CAP change)"
                      f" and needs {GUARD_MIN_H} h" if a > L else "live window still short")
    return a, None


def trial_units_if_stopped(db, now):
    """Units bought in the sofa trial, when the trial has ended (stopped: end passed, not extended); () while it
    runs (kept or expanded: the trial's sales are part of both windows and stay in)."""
    t0, t1 = policy.get("purchasing", "sofa_trial_start"), policy.get("purchasing", "sofa_trial_end")
    if not t0 or now < t1:
        return ()
    cat = _catalog()
    sofas = [k for k, c in cat.items() if (c.get("category") or "").split(">")[-1] == "소파/의자/스툴"]
    if not sofas:
        return ()
    rows = db.execute(f"SELECT unit FROM my_stock WHERE supplied_at>=? AND supplied_at<? AND sku IN "
                      f"({','.join('?' * len(sofas))})", (t0, t1, *sofas)).fetchall()
    return tuple(r[0] for r in rows)


MARKET_ADJ_MIN = -0.30       # analyst approval check (11/09 person): my revenue change - market revenue change


def market_revenue(db, t0, t1):
    """Revenue of every shop on the market (my shop included) in [t0, t1)."""
    return db.execute("SELECT COALESCE(SUM(amount), 0) FROM settles WHERE ts>=? AND ts<?", (t0, t1)).fetchone()[0]


def market_adjusted(cur, base, m_cur, m_base):
    """(my revenue change, market revenue change, my change - market change); None parts when a base is 0."""
    mine = cur["revenue"] / base["revenue"] - 1 if base["revenue"] else None
    mkt = m_cur / m_base - 1 if m_base else None
    return mine, mkt, (mine - mkt if mine is not None and mkt is not None else None)


def spend_rule(stock, cash, profit, cap):
    """purchasing.spend_share: (value or None, why). 11/09 ~12:15 (person, option A of 11/08): the 0.5 condition is
    stock < STOCK_CAP x 0.9 (was half the cap); profit > 0, cash > 3M and the cash < 2.5M -> 0.3 rule unchanged."""
    if stock >= cap * 0.9:
        return None, f"stock {stock:,} within 10% of STOCK_CAP: spend share does not bind"
    if cash < 2_500_000:
        return 0.3, f"cash {cash:,} < 2,500,000"
    if profit > 0 and stock < cap * 0.9 and cash > 3_000_000:
        return 0.5, f"6 h profit {profit:,} > 0, stock {stock:,} < STOCK_CAP x 0.9, cash {cash:,} > 3M"
    return 0.4, f"default (stock {stock:,}, cash {cash:,}, 6 h profit {profit:,})"


def worse(cur, base, min_sales=20, min_accounts=8, min_paid=10, buyer=True, market=None):
    """Measures clearly worse in cur than in base (dept_reports.window dicts); [] if none. buyer=False leaves the
    buyer Saved measures out (the CEO guard, 11/07; the analyst's approval checks keep them).
    market=(market revenue in cur, in base) (the analyst's approval check, person's decision 11/09): sales and profit
    are not compared; instead my revenue change minus the market's revenue change over the same windows under
    MARKET_ADJ_MIN (-30%p) is worse."""
    bad = []
    if market is not None:
        mine, mkt, adj = market_adjusted(cur, base, *market)
        if base["sales"] >= min_sales and adj is not None and adj < MARKET_ADJ_MIN:
            bad.append(f"매출 변화 {mine:+.1%} - 시장 전체 {mkt:+.1%} = {adj:+.1%}p < {MARKET_ADJ_MIN:+.0%}p "
                       f"(매출 {cur['revenue']:,} vs {base['revenue']:,})")
    elif base["sales"] >= min_sales and cur["sales"] < 0.7 * base["sales"]:
        bad.append(f"sales {cur['sales']} vs {base['sales']}")
    if market is None and base["sales"] >= min_sales and base["profit"] > 0 and cur["profit"] < 0.7 * base["profit"]:
        bad.append(f"profit {cur['profit']:,} vs {base['profit']:,}")
    if cur["review_accounts"] >= min_accounts and base["review_accounts"] >= min_accounts and cur["stars"] is not None \
            and base["stars"] is not None and cur["stars"] < base["stars"] - 0.4:
        bad.append(f"stars (one mean per account) {cur['stars']} vs {base['stars']}")
    if not buyer:
        return bad
    if base["buyer_paid"] >= min_paid and base["buyer_saved_sum"] > 0 and cur["buyer_saved_sum"] < 0.7 * base["buyer_saved_sum"]:
        bad.append(f"buyer saved (sum of shares kept) {cur['buyer_saved_sum']} vs {base['buyer_saved_sum']}, "
                   f"paid {cur['buyer_paid']} vs {base['buyer_paid']}")
    elif base["buyer_paid"] >= min_paid and cur["buyer_paid"] >= min_paid and cur["buyer_saved_avg"] is not None \
            and cur["buyer_saved_avg"] < base["buyer_saved_avg"] - 0.05:
        bad.append(f"buyer saved per deal {cur['buyer_saved_avg']} vs {base['buyer_saved_avg']}")
    return bad


def analyst_change_times():
    """Times of limit changes made by approved analyst proposals: the analyst checks those itself (3 h before
    vs after), so the live guard does not pause 24 h for them (person's decision 11/06)."""
    try:
        db = connect()
        try:
            return [r[0] for r in db.execute("SELECT decided_ts FROM proposals WHERE kind='limit' AND decided_ts IS NOT "
                                             "NULL AND status IN ('approved','reverted')")]
        finally:
            db.close()
    except Exception:
        return []


def guard(st, now, log):
    """Live window vs the same hours a day before; revert to shadow when one measure is clearly worse."""
    L = st.get("live_since")
    if st.get("mode") != "live" or not L or now - L < GUARD_MIN_H * 3600 or now - st.get("last_guard", 0) < GUARD_EVERY_S:
        return None
    A, why = anchor(L, now)
    # while waiting, the next check is at the first possible comparison (19:46 on 11/07), not an hour later
    st["last_guard"] = now if A is not None else min(now, anchor_start(L, now) + GUARD_MIN_H * 3600 - GUARD_EVERY_S)
    if A is None:                                  # within 24 h of a STOCK_CAP change: no comparison yet
        decisions.record("ceo", "guard_check", "live vs a day before", None, why)
        log(f"[ceo] guard waits: {why}")
        return None
    db = connect()
    try:
        ex = trial_units_if_stopped(db, now)
        cur = dept_reports.window(db, A, now, _catalog(), ex)
        base = dept_reports.window(db, A - 86400, now - 86400, _catalog(), ex)
    finally:
        db.close()
    if ex:
        log(f"[ceo] guard: sofa trial stopped, its {len(ex)} units' sales left out of both windows")
    bad = worse(cur, base, buyer=False)
    summary = {k: [cur[k], base[k]] for k in ("sales", "profit", "stars", "review_accounts", "buyer_paid",
                                               "buyer_saved_sum", "buyer_saved_avg")}
    summary["buyer_saved"] = "information only, not a guard condition"
    note = f" [{MIXED_NOTE}]" if A - 86400 < MIXED_UNTIL else ""
    if note:
        summary["note"] = MIXED_NOTE
    decisions.record("ceo", "guard_check", "live vs a day before", summary,
                     ("; ".join(bad) or "no measure clearly worse") + note)
    log(f"[ceo] guard: live {cur['hours']} h vs same hours a day before: {summary}"
        + (f" -> WORSE: {bad}" if bad else "") + note)
    if bad:
        from depts import policy_notify
        pre = st.get("pre_live") or {}
        back = {k.split(".", 1)[1]: pre.get(k, DEFAULTS_CEO[k]) for k in LIVE_KEYS
                if not policy_notify.pinned(k, now)}            # a pinned key stays (person's 24 h pin)
        raw = policy.load().get("purchasing") or {}
        v0 = policy.version()
        new_v = policy.revert_keys("purchasing", back, f"ceo guard: {'; '.join(bad)} (CEO keys only)")["version"]
        policy_notify.notify("guard", [{"key": f"purchasing.{k}", "old": raw.get(k), "new": v,
                                        "where": f"policy.json > purchasing.{k}"} for k, v in back.items()],
                             "CEO 가드: 라이브 구간이 하루 전 같은 시간보다 나빠짐 -> CEO 키를 7단계 시작 값으로, CEO는 shadow",
                             evidence="; ".join(bad) + note + f" (live {cur['hours']} h: " +
                             ", ".join(f"{k} {summary[k][0]} vs {summary[k][1]}" for k in ("sales", "profit", "stars")) + ")"
                             + f"\n비교 기간: 라이브 {datetime.fromtimestamp(A):%m-%d %H:%M}~{datetime.fromtimestamp(now):%m-%d %H:%M}"
                               f" vs 하루 전 같은 시간 {datetime.fromtimestamp(A - 86400):%m-%d %H:%M}~"
                               f"{datetime.fromtimestamp(now - 86400):%m-%d %H:%M}"
                             + "\n예상 효과: CEO 키 4개가 7단계 시작 값으로 돌아가고, 다시 live로 바꿀 때까지 CEO는 제안만 기록",
                             version=(f"v{v0}", f"v{new_v}"), next_at="없음 (CEO shadow, 다시 live는 사람 결정)", log=log)
        st.update(mode="shadow", live_since=None, shadow_since=now, auto_live_at=None,
                  reverts=st.get("reverts", []) + [{"ts": now, "why": bad, "keys": back}])
        decisions.record("ceo", "revert", "mode", {"mode": "shadow", "keys": back}, "; ".join(bad) + note)
        alerts.send("urgent", "ceo_revert", f"CEO policy reverted to step 6 (shadow): {'; '.join(bad)}. "
                                            f"Live {cur['hours']} h vs the same hours a day before. CEO keys back to "
                                            f"{back}; limits and analyst approvals unchanged.{note}", log)
    return bad


_cat = {}


def _catalog():
    if not _cat:
        try:
            _cat.update(json.load(open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return _cat


def tick(inv, catalog, log=print):
    st = load_state()
    now = time.time()
    try:
        guard(st, now, log)
        if st.get("mode") == "shadow" and st.get("auto_live_at") and now >= st["auto_live_at"]:
            if st.get("runs", 0) >= 12 and not st.get("errors"):
                st.update(mode="live", live_since=now,     # pre_live: the values a guard revert goes back to
                          pre_live={k: policy.get(*k.split(".", 1)) for k in LIVE_KEYS})
                decisions.record("ceo", "mode", "mode", "live", f"auto after {st['runs']} shadow runs, no errors")
                alerts.send("info", "ceo_live", f"CEO policy is live (step 7) after {st['runs']} shadow runs.", log)
            else:
                st["auto_live_at"] = None
                alerts.send("info", "ceo_live_held", f"CEO stays in shadow: {st.get('runs', 0)} runs, "
                                                     f"{st.get('errors', 0)} errors", log)
        if now - st.get("last_run", 0) < RUN_EVERY_S:
            return
        st["last_run"] = now
        props = proposals(inv, catalog, dept_reports.load(), st, now)
        st["runs"] = st.get("runs", 0) + 1
        if not props:
            log(f"[ceo] {st['mode']}: no policy change")
            return
        reason = "; ".join(f"{s}.{k} -> {v} ({why})" for s, k, v, why in props)
        if st["mode"] == "live":
            from depts import policy_notify, night_freeze
            if night_freeze.frozen(now):               # 11/09 ~23:55 (person): no new policy change overnight
                for s, k, v, why in props:
                    decisions.record("ceo", "frozen", f"{s}.{k}", v, f"overnight freeze until "
                                     f"{night_freeze.until_text()}: not changed ({why})")
                log(f"[ceo] live: frozen until {night_freeze.until_text()}, not applied: {reason}")
                return
            for s, k, v, why in [x for x in props if policy_notify.pinned(f"{x[0]}.{x[1]}", now)]:
                decisions.record("ceo", "pinned", f"{s}.{k}", v, f"pinned by the person for 24 h: not changed ({why})")
            props = [x for x in props if not policy_notify.pinned(f"{x[0]}.{x[1]}", now)]
            if not props:
                return
            split = current_split()
            old = {f"{s}.{k}": (split.get(k) if k in ("high_share", "high_max") else policy.get(s, k))
                   for s, k, v, why in props}
            v0 = policy.version()
            secs = {}
            for s, k, v, why in props:
                secs.setdefault(s, {})[k] = v
                st["key_ts"][f"{s}.{k}"] = now
            reason = "; ".join(f"{s}.{k} -> {v} ({why})" for s, k, v, why in props)
            new = policy.write(secs, reason)
            for s, k, v, why in props:
                decisions.record("ceo", "policy", f"{s}.{k}", v, why)
            log(f"[ceo] live: policy v{new['version']}: {reason}")
            hold = min((now + (86400 if k in ("high_share", "high_max") else KEY_HOLD_S)) for s, k, v, why in props)
            ev = []                                      # 11/07 ~16:20: every number, the period, the effect
            try:
                from depts import ceo_detail
                rep = dept_reports.load()
                for s, k, v, why in props:
                    if old[f"{s}.{k}"] == v:             # unchanged keys are not in the notice
                        continue
                    dt = ceo_detail.detail(s, k, old[f"{s}.{k}"], v, inv, catalog, rep, now)
                    ev.append(f"[{s}.{k}]\n" + "\n".join(f"  {x}" for x in dt["numbers"])
                              + f"\n  비교 기간: {dt['period']}\n  예상 효과: {dt['effect']}")
            except Exception as e:
                ev.append(f"(근거 상세 실패: {type(e).__name__}: {e})")
            policy_notify.notify("ceo", [{"key": f"{s}.{k}", "old": old[f"{s}.{k}"], "new": v,
                                          "where": f"policy.json > {s}.{k}"} for s, k, v, why in props],
                                 "; ".join(why for s, k, v, why in props), evidence="\n" + "\n".join(ev),
                                 version=(f"v{v0}", f"v{new['version']}"),
                                 next_at=f"{datetime.fromtimestamp(now + RUN_EVERY_S):%m-%d %H:%M} (30분마다), 이 키 "
                                         f"다음 변경 가능 {datetime.fromtimestamp(hold):%m-%d %H:%M}", log=log)
        else:
            for s, k, v, why in props:
                st["key_ts"][f"{s}.{k}"] = now
                decisions.record("ceo", "shadow_policy", f"{s}.{k}", v, why)
            log(f"[ceo] shadow (not applied): {reason}")
    except Exception as e:
        st["errors"] = st.get("errors", 0) + 1
        log(f"[ceo] failed: {type(e).__name__}: {e}")
    finally:
        save_state(st)
