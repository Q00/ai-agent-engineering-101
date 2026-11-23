"""Wider buying with idle cash (person's decision 11/12 ~14:20, from a 24 h simulation of A-D; applied: the best two by
expected profit / 12 h+ unsold increase at cost, normal goods first when close -> C and B). Rules in wide_trial.json
(read on every call; off without it or after the guard trips):
  B  high-price buying margin filter 8% -> 6% (b_margin), only for SKUs my shop has sold before (any time)
  C  hot SKUs that turn in <= 1 h (depts/hot_fast.py), normal goods only (est. cost <= HIGH_MIN): my_share 0.8 -> 1.2
  guard  high-price units (cost > qc.HIGH_MIN) held unsold over STALE_H (12 h), counted from the live inventory: at
         or over base + max_add_n (10) units or base + max_add_cost (5,000,000) -> both off, one urgent Telegram
         (base = the value when applied: 63 units, 35,875,897). Checked by tools/wide_trial_watch.py every 5 min and
         at the start of every buy plan.
  report one Telegram check_h (3) hours after the start (report()).
11/13 ~00:00 (person): TWO - a high-price SKU (not deep_high's) that my shop sold at least once in the last 24 h may
be held up to 2 (depts/deep_high.limit); high_max 100 -> 200. Guard with "max_cost" in the rules: only the cost of the
12 h+ unsold high-price units over max_cost (60,000,000) trips it (the unit-count rule is gone) -> B, C and TWO off,
high_max back to 100 (policy.json + the CEO / QC ceiling in high_caps.json), one urgent Telegram.
Lines bought because of B / C (B: the expected margin 6-8%; C: the units over the 0.8 target) -> logs/wide_trial.jsonl.
"""
import json
import os
import sqlite3
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "wide_trial.json")
LINES = os.path.join(HERE, "logs", "wide_trial.jsonl")
STALE_H = 12
ME = "choo-chu"
_sold = {"ts": 0, "skus": set()}


def rules(path=None):
    try:
        with open(path or RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(r, path=None):
    path = path or RULES
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def on(key, r=None):
    r = rules() if r is None else r
    return bool(r.get("enabled")) and not r.get("off_ts") and key in (r.get("applied") or [])


def sold_skus(db_path=None, now=None, ttl=600):
    """SKUs my shop has sold (market.db settles, any time); cached for ttl seconds."""
    now = now or time.time()
    if db_path is None and now - _sold["ts"] < ttl:
        return _sold["skus"]
    d = sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=30)
    try:
        s = {k for (k,) in d.execute("SELECT DISTINCT sku FROM settles WHERE shop=?", (ME,))}
    finally:
        d.close()
    if db_path is None:
        _sold.update(ts=now, skus=s)
    return s


_sold24 = {}


def sold_24h(db_path=None, now=None, ttl=600, min_n=1):
    """SKUs my shop sold at least min_n times in the last 24 h (settles); cached for ttl seconds (per min_n)."""
    now = now or time.time()
    c = _sold24.get(min_n)
    if db_path is None and c and now - c[0] < ttl:
        return c[1]
    d = sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=30)
    try:
        s = {k for (k,) in d.execute("SELECT sku FROM settles WHERE shop=? AND ts>=? GROUP BY sku HAVING COUNT(*)>=?",
                                     (ME, now - 86400, min_n))}
    finally:
        d.close()
    if db_path is None:
        _sold24[min_n] = (now, s)
    return s


def two_limit(sku, r=None, sold=None):
    """2 for a SKU sold in the last 24 h while TWO is on, else 1 (deep_high's SKUs are decided before this).
    11/13 ~13:10 (person): sold at least two_min_sold (wide_trial.json, 2) times in the last 24 h."""
    r = rules() if r is None else r
    if not on("TWO", r):
        return 1
    try:
        s = sold_24h(min_n=int(r.get("two_min_sold", 1))) if sold is None else sold
    except sqlite3.Error:
        return 1
    return int(r.get("two_max", 2)) if sku in s else 1


def high_max_back(n=100, why=""):
    """policy.json purchasing.high_max and the high_caps.json ceiling back to n (the guard's trip)."""
    import policy
    from depts import high_caps
    sc, _ = high_caps.get()
    high_caps.set_caps(sc, n, why)
    policy.write({"purchasing": {"high_max": n}}, why, by="person")


def b_margin(sku, default, r=None, sold=None):
    """The high-price margin filter for `sku`: B's min (6%) for a SKU my shop has sold, else `default`."""
    r = rules() if r is None else r
    if not on("B", r):
        return default
    try:
        s = sold_skus() if sold is None else sold
    except sqlite3.Error:
        return default
    return min(default, float(r.get("B_min_margin", 0.06))) if sku in s else default


def c_shares(fast_share, is_high, r=None):
    """hot_fast's {sku: share} with C's share (1.2) for the normal-goods SKUs on it (high-price keep theirs)."""
    r = rules() if r is None else r
    if not on("C", r):
        return dict(fast_share)
    c = float(r.get("C_share", 1.2))
    return {k: (v if is_high(k) else max(v, c)) for k, v in fast_share.items()}


def note(lines, log=print, path=None):
    """[{opt, sku, qty}] planned because of B / C -> one row each."""
    if not lines:
        return
    try:
        with open(path or LINES, "a", encoding="utf-8") as f:
            for x in lines:
                f.write(json.dumps(dict(x, ts=round(time.time()))) + "\n")
        log("[wide trial] " + ", ".join(f"{x['opt']} {x['sku']} +{x['qty']}" for x in lines))
    except OSError:
        pass


def aged_high(units, now=None):
    """(units, cost) of held high-price units (cost > qc.HIGH_MIN) supplied over STALE_H ago."""
    from depts.qc import HIGH_MIN
    now = now or time.time()
    xs = [u for u in units if u.get("grade") != "expired" and (u.get("cost") or 0) > HIGH_MIN and u.get("supplied_at")
          and u["supplied_at"] <= now - STALE_H * 3600]
    return len(xs), int(sum(u["cost"] for u in xs))


def live_units(inv_path=None):
    with open(inv_path or os.path.join(HERE, ".market", "inventory.json"), encoding="utf-8") as f:
        return json.load(f).get("units") or []


def guard(units=None, now=None, send=None, path=None, log=print, high_max_back_fn=None):
    """Both off + one urgent Telegram when the 12 h+ unsold high-price stock grew by max_add_n units or max_add_cost
    over the base. Returns 'tripped', 'ok', or 'off' (not running)."""
    r = rules(path)
    if not r.get("enabled") or r.get("off_ts"):
        return "off"
    now = now or time.time()
    units = live_units() if units is None else units
    n, cost = aged_high(units, now)
    if r.get("max_cost"):                         # 11/13 ~00:00 (person): the cost only
        if cost <= r["max_cost"]:
            return "ok"
        why = f"12시간+ 미판매 고가 {n}개 원가 {cost:,} > 기준 {r['max_cost']:,}"
    else:
        b = r.get("base") or {}
        dn, dc = n - b.get("n", 0), cost - b.get("cost", 0)
        if dn < r.get("max_add_n", 10) and dc < r.get("max_add_cost", 5_000_000):
            return "ok"
        why = (f"12시간+ 미판매 고가 {n}개 {cost:,} (적용 때 {b.get('n')}개 {b.get('cost', 0):,}, {dn:+}개 {dc:+,}; 기준 "
               f"+{r.get('max_add_n', 10)}개 또는 +{r.get('max_add_cost', 5_000_000):,})")
    r.update(off_ts=round(now), off_why=why)
    save(r, path)
    log(f"[wide trial] guard tripped: {why} -> {'/'.join(r.get('applied') or [])} off")
    back = ""
    if r.get("max_cost") and r.get("high_max_back"):
        try:
            (high_max_back_fn or high_max_back)(int(r["high_max_back"]), f"wide trial guard: {why}")
            back = f", high_max {r['high_max_back']}"
        except Exception as e:
            log(f"[wide trial] high_max back failed: {type(e).__name__}: {e}")
            back = f", high_max 되돌리기 실패 ({type(e).__name__})"
    if send is None:
        import alerts
        send = alerts.send
    try:
        send("urgent", f"wide_trial_off:{round(now)}", f"후보 넓히기 {'+'.join(r.get('applied') or [])} 자동 끔: {why}. "
             f"고가 마진 8%, 일반 my_share 0.8"
             + (", 고가 SKU당 1개" if "TWO" in (r.get("applied") or []) else "") + f"로 돌아감{back}")
    except Exception as e:
        log(f"[wide trial] alert failed: {type(e).__name__}: {e}")
    return "tripped"


REON = os.path.join(HERE, "logs", "wide_trial_reon.json")
REON_GAP_S = 1800          # a gap in the checks longer than this restarts the under-the-line clock


def _reon_state(path=None):
    try:
        with open(path or REON, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def re_on(units=None, now=None, send=None, path=None, state_path=None, log=print, high_max_fn=None):
    """11/14 ~16:10 (person): after the guard turned the trial off, the 12 h+ unsold high-price cost under re_on_cost
    (45,000,000) for re_on_h (6) hours in a row -> on again (off_ts cleared, the off kept in "offs"), high_max back to
    re_on_high_max (120; policy.json + the high_caps.json ceiling), one Telegram. The clock (logs/wide_trial_reon.json)
    restarts when the cost is at or over the line or the checks stop for over REON_GAP_S. Checked at every buy plan.
    Returns 'n/a', 'above', 'below' or 'on'."""
    r = rules(path)
    if not r.get("enabled") or not r.get("off_ts") or not r.get("re_on_cost"):
        return "n/a"
    now = now or time.time()
    units = live_units() if units is None else units
    n, cost = aged_high(units, now)
    st = _reon_state(state_path)
    since = st.get("below_since") if st.get("off_ts") == r["off_ts"] and now - st.get("last", 0) <= REON_GAP_S else None
    if cost >= r["re_on_cost"]:
        since = None
    elif since is None:
        since = round(now)
    st = {"off_ts": r["off_ts"], "below_since": since, "last": round(now), "n": n, "cost": cost}
    tmp = (state_path or REON) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f)
    os.replace(tmp, state_path or REON)
    h = float(r.get("re_on_h", 6))
    if since is None:
        return "above"
    if now - since < h * 3600:
        return "below"
    off = {k: r.pop(k) for k in ("off_ts", "off_why") if k in r}
    r.setdefault("offs", []).append(dict(off, on_ts=round(now), on_why=f"12h+ unsold high cost < {r['re_on_cost']:,} "
                                                                         f"for {h:g} h"))
    save(r, path)
    why = (f"12시간+ 미판매 고가 원가가 {datetime.fromtimestamp(since):%m-%d %H:%M}부터 {h:g}시간 동안 "
           f"{r['re_on_cost']:,} 아래 (지금 {n}개 {cost:,})")
    log(f"[wide trial] on again: {why}")
    back = ""
    if r.get("re_on_high_max"):
        try:
            (high_max_fn or high_max_back)(int(r["re_on_high_max"]), f"wide trial on again: {why}")
            back = f", high_max {r['re_on_high_max']}"
        except Exception as e:
            log(f"[wide trial] high_max on again failed: {type(e).__name__}: {e}")
            back = f", high_max 되돌리기 실패 ({type(e).__name__})"
    if send is None:
        import alerts
        send = alerts.send
    try:
        send("urgent", f"wide_trial_on:{round(now)}", f"후보 넓히기 {'+'.join(r.get('applied') or [])} 자동으로 다시 켬: "
             f"{why}{back}. 안전장치(원가 {r.get('max_cost', 0):,} 초과 시 끔)는 그대로")
    except Exception as e:
        log(f"[wide trial] alert failed: {type(e).__name__}: {e}")
    return "on"


def _lines(t0, t1, path=None):
    out = []
    try:
        with open(path or LINES, encoding="utf-8") as f:
            for ln in f:
                try:
                    x = json.loads(ln)
                except ValueError:
                    continue
                if t0 <= x.get("ts", 0) < t1:
                    out.append(x)
    except OSError:
        pass
    return out


def window(skus, t0, t1, d):
    """My supplies and sales of `skus` in [t0, t1): (buy n, buy cost, sold n, revenue, profit)."""
    if not skus:
        return 0, 0, 0, 0, 0
    q = ",".join("?" * len(skus))
    b = d.execute(f"SELECT COUNT(*), COALESCE(SUM(cost), 0) FROM my_stock WHERE sku IN ({q}) AND supplied_at>=? AND "
                  f"supplied_at<?", (*skus, t0, t1)).fetchone()
    s = d.execute(f"SELECT COUNT(*), COALESCE(SUM(s.amount), 0), COALESCE(SUM(m.cost), 0) FROM settles s JOIN my_stock m "
                  f"ON m.unit=s.unit WHERE s.shop=? AND s.sku IN ({q}) AND s.ts>=? AND s.ts<?", (ME, *skus, t0, t1)).fetchone()
    return b[0], int(b[1]), s[0], int(s[1]), int(s[1] - s[2])


def report(now=None, path=None, lines_path=None, db_path=None, inv_path=None):
    """The check_h report text: cash, the B / C lines (planned extra units), my buys and sales of those SKUs after vs
    the same length before, the 12 h+ unsold high-price trend every hour."""
    r = rules(path)
    now = now or time.time()
    t0 = r["since"]
    h = max((now - t0) / 3600, 0.01)
    xs = _lines(t0, now, lines_path)
    L = [f"후보 넓히기 {'+'.join(r.get('applied') or [])} {h:.1f}시간 점검 ({datetime.fromtimestamp(t0):%m-%d %H:%M} 적용"
         + (f", {datetime.fromtimestamp(r['off_ts']):%H:%M} 자동 끔" if r.get("off_ts") else "") + ", 후 vs 직전 같은 길이)"]
    d = sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=60)
    try:
        c = lambda t: d.execute("SELECT balance, stock_cost FROM cash ORDER BY ABS(ts - ?) LIMIT 1", (t,)).fetchone()
        c0, c1 = c(t0), c(now)
        if c0 and c1:
            L.append(f"- 현금 {c0[0]:,} -> {c1[0]:,} ({c1[0] - c0[0]:+,}), 재고 원가 {c0[1]:,} -> {c1[1]:,} "
                     f"({c1[1] - c0[1]:+,})")
        for opt, name in (("B", "B 고가 마진 6%"), ("C", "C 일반 my_share 1.2")):
            ys = [x for x in xs if x["opt"] == opt]
            skus = sorted({x["sku"] for x in ys})
            a, p = window(skus, t0, now, d), window(skus, t0 - h * 3600, t0, d)
            L.append(f"- {name}: 계획 추가분 {sum(x['qty'] for x in ys)}개 ({len(skus)}개 SKU); 그 SKU들 매입 {a[0]}개 "
                     f"{a[1]:,} vs {p[0]}개 {p[1]:,}, 판매 {a[2]}개 매출 {a[3]:,} 이익 {a[4]:,} vs {p[2]}개 {p[3]:,} {p[4]:,}")
    finally:
        d.close()
    import sys
    sys.path.insert(0, os.path.join(HERE, "tools"))
    import morning_summary as M
    tr = M.aged_high_trend(now, inv_path=inv_path, db_path=db_path, step_h=1, span_h=max(1, round(h)))
    b = r.get("base") or {}
    L.append(f"- {tr.split(': ', 1)[-1]} | 기준: 적용 때 {b.get('n')}개 {b.get('cost', 0):,} +{r.get('max_add_n', 10)}개 "
             f"또는 +{r.get('max_add_cost', 5_000_000):,}")
    return "\n".join(L)


def report_due(now=None, send=None, path=None, log=print, **kw):
    """Send report() once, check_h hours after the start. True when sent."""
    r = rules(path)
    now = now or time.time()
    if not r.get("since") or r.get("reported") or now < r["since"] + r.get("check_h", 3) * 3600:
        return False
    if send is None:
        import alerts
        send = alerts.send
    send("daily", f"wide_trial_report:{r['since']}", report(now, path=path, **kw))
    r = rules(path)
    r["reported"] = round(now)
    save(r, path)
    return True


def high_window(t0, t1, d):
    """My high-price (cost > qc.HIGH_MIN) buys and sales in [t0, t1): (buy n, buy cost, sold n, revenue, profit)."""
    from depts.qc import HIGH_MIN
    b = d.execute("SELECT COUNT(*), COALESCE(SUM(cost), 0) FROM my_stock WHERE cost>? AND supplied_at>=? AND supplied_at<?",
                  (HIGH_MIN, t0, t1)).fetchone()
    s = d.execute("SELECT COUNT(*), COALESCE(SUM(s.amount), 0), COALESCE(SUM(s.amount - m.cost), 0) FROM settles s JOIN "
                  "my_stock m ON m.unit=s.unit WHERE s.shop=? AND m.cost>? AND s.ts>=? AND s.ts<?",
                  (ME, HIGH_MIN, t0, t1)).fetchone()
    return b[0], int(b[1]), s[0], int(s[1]), int(s[2])


def report_two(now=None, path=None, db_path=None, inv_path=None, book=None):
    """11/13 ~00:00 (person): the 3 h check of TWO (high 1 -> 2, high_max 200): high-price buys / sales / profit,
    cash, the 12 h+ unsold high-price trend, Book and revenue per hour - after vs the same length before.
    book(t0, t1) -> (book per h, revenue per h) or None (tools/board_read by default)."""
    r = rules(path)
    now = now or time.time()
    t0 = r["two_since"]
    h = max((now - t0) / 3600, 0.01)
    L = [f"고가 1→2개 + high_max 200 {h:.1f}시간 점검 ({datetime.fromtimestamp(t0):%m-%d %H:%M} 적용"
         + (f", {datetime.fromtimestamp(r['off_ts']):%H:%M} 자동 끔" if r.get("off_ts") else "") + ", 후 vs 직전 같은 길이)"]
    d = sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=60)
    try:
        a, p = high_window(t0, now, d), high_window(t0 - h * 3600, t0, d)
        L.append(f"- 고가 매입 {a[0]}개 {a[1]:,} vs {p[0]}개 {p[1]:,}")
        L.append(f"- 고가 판매 {a[2]}개 매출 {a[3]:,} 이익 {a[4]:+,} vs {p[2]}개 {p[3]:,} {p[4]:+,}")
        c = lambda t: d.execute("SELECT balance, stock_cost FROM cash ORDER BY ABS(ts - ?) LIMIT 1", (t,)).fetchone()
        c0, c1 = c(t0), c(now)
        if c0 and c1:
            L.append(f"- 현금 {c0[0]:,} -> {c1[0]:,} ({c1[0] - c0[0]:+,}), 재고 원가 {c0[1]:,} -> {c1[1]:,} "
                     f"({c1[1] - c0[1]:+,})")
    finally:
        d.close()
    import sys
    sys.path.insert(0, os.path.join(HERE, "tools"))
    import morning_summary as M
    tr = M.aged_high_trend(now, inv_path=inv_path, db_path=db_path, step_h=1, span_h=max(1, round(h)))
    L.append(f"- {tr.split(': ', 1)[-1]} | 끄는 기준: 원가 {r.get('max_cost', 0):,} 초과")
    if book is None:
        def book(x0, x1):
            import board_read as BR
            q0, q1 = BR.at(x0, db_path=db_path), (BR.now(db_path=db_path) if x1 >= time.time() - 300 else BR.at(x1, db_path=db_path))
            return BR.per_h(q0, q1, "s", "book"), BR.per_h(q0, q1, "s", "revenue")
    try:
        ba, bp = book(t0, now), book(t0 - h * 3600, t0)
        f = lambda v: "-" if v is None else f"{v:,.0f}"
        L.append(f"- 시간당 Book {f(ba[0])} vs {f(bp[0])}, 시간당 매출 {f(ba[1])} vs {f(bp[1])}")
    except Exception as e:
        L.append(f"- 시간당 Book·매출: 읽기 실패 ({type(e).__name__})")
    return "\n".join(L)


def report_two_due(now=None, send=None, path=None, log=print, **kw):
    """Send report_two() once, check_h hours after two_since. True when sent."""
    r = rules(path)
    now = now or time.time()
    if not r.get("two_since") or r.get("two_reported") or now < r["two_since"] + r.get("check_h", 3) * 3600:
        return False
    if send is None:
        import alerts
        send = alerts.send
    send("daily", f"high_two_report:{r['two_since']}", report_two(now, path=path, **kw))
    r = rules(path)
    r["two_reported"] = round(now)
    save(r, path)
    log("[wide trial] TWO report sent")
    return True
