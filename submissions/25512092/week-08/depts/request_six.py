"""Request-based lines 4 -> 6 (person 11/15 ~14:20: 5 regular + 1 new-SKU trial; the 1 h turnover target and the
shelf-life rules unchanged). request_lines.json holds the counts (depts/request_buy.lines_cfg) and this trial's keys:
  six_since      when 6 lines went on
  max_aged       12 h+ unsold normal stock (live inventory, cost <= qc.HIGH_MIN, not expired) over this cost
                 (10,000,000) -> back to base_lines (4 = 3 + 1) by itself, off_ts / off_why, one urgent Telegram
  report_h       one Telegram report_h (6) hours after six_since: normal sales per hour, profit and the 12 h+ unsold
                 normal stock, after vs the same length before (base_aged = the stock when it went on)
check() runs both, at every buy plan (depts/purchasing.make_plan, after the request-based lines).
"""
import json
import os
import sqlite3
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "request_lines.json")
ME = "choo-chu"
STALE_H = 12


def rules(path=None):
    try:
        with open(path or RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(r, path=None):
    tmp = (path or RULES) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path or RULES)


def aged_normal(units, now=None):
    """(units, cost) of held normal units (0 < cost <= qc.HIGH_MIN, not expired) supplied over STALE_H ago."""
    from depts.qc import HIGH_MIN
    now = now or time.time()
    xs = [u for u in units if u.get("grade") != "expired" and 0 < (u.get("cost") or 0) <= HIGH_MIN
          and u.get("supplied_at") and u["supplied_at"] <= now - STALE_H * 3600]
    return len(xs), int(sum(u["cost"] for u in xs))


def guard(units, now=None, send=None, path=None, log=print):
    """Back to base_lines when the 12 h+ unsold normal stock is over max_aged. 'tripped', 'ok' or 'off'."""
    r = rules(path)
    if not r.get("six_since") or r.get("off_ts") or int(r.get("lines", 0)) <= int(r.get("base_lines", 4)):
        return "off"
    now = now or time.time()
    n, cost = aged_normal(units, now)
    if cost <= int(r.get("max_aged", 10_000_000)):
        return "ok"
    why = f"12시간+ 미판매 일반 재고 {n}개 원가 {cost:,} > 기준 {int(r.get('max_aged', 10_000_000)):,}"
    six = {k: r.get(k) for k in ("lines", "regular", "trial")}
    r.update(lines=int(r.get("base_lines", 4)), regular=int(r.get("base_lines", 4)) - int(r.get("trial", 1)),
             off_ts=round(now), off_why=why, six=six)
    save(r, path)
    log(f"[request six] guard tripped: {why} -> request lines {r['lines']}")
    if send is None:
        import alerts
        send = alerts.send
    try:
        send("urgent", f"request_six_off:{round(now)}", f"요청 기반 매입 6줄 자동 원복: {why}. 4줄(정식 3 + 시험 1)로 "
             f"돌아감")
    except Exception as e:
        log(f"[request six] alert failed: {type(e).__name__}: {e}")
    return "tripped"


def window(t0, t1, d):
    """My normal (cost <= qc.HIGH_MIN) sales in [t0, t1): (n, revenue, profit)."""
    from depts.qc import HIGH_MIN
    r = d.execute("SELECT COUNT(*), COALESCE(SUM(s.amount), 0), COALESCE(SUM(s.amount - m.cost), 0) FROM settles s "
                  "JOIN my_stock m ON m.unit=s.unit WHERE s.shop=? AND m.cost>0 AND m.cost<=? AND s.ts>=? AND s.ts<?",
                  (ME, HIGH_MIN, t0, t1)).fetchone()
    return r[0], int(r[1]), int(r[2])


def report(now, units, path=None, db_path=None):
    r = rules(path)
    t0 = r["six_since"]
    t1 = min(now, r["off_ts"]) if r.get("off_ts") else now
    L = max(t1 - t0, 60)
    h = L / 3600
    d = sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=60)
    try:
        a, b = window(t0, t1, d), window(t0 - L, t0, d)
    finally:
        d.close()
    n, cost = aged_normal(units, now)
    ba = r.get("base_aged") or [None, None]
    head = (f"요청 기반 매입 6줄 {h:.1f}시간 점검 ({datetime.fromtimestamp(t0):%m-%d %H:%M} 적용"
            + (f", {datetime.fromtimestamp(r['off_ts']):%H:%M} 자동 원복: {r.get('off_why')}" if r.get("off_ts") else "")
            + ", 후 vs 직전 같은 길이)")
    return "\n".join([
        head,
        f"- 일반 판매/h {a[0] / h:.1f} vs {b[0] / h:.1f} (매출/h {a[1] / h:,.0f} vs {b[1] / h:,.0f})",
        f"- 일반 이익 {a[2]:+,} vs {b[2]:+,} (시간당 {a[2] / h:+,.0f} vs {b[2] / h:+,.0f})",
        f"- 12시간+ 미판매 일반 재고: 지금 {n}개 {cost:,} vs 적용 때 {ba[0]}개 {ba[1]:,}" if ba[1] is not None else
        f"- 12시간+ 미판매 일반 재고: 지금 {n}개 {cost:,}",
        f"- 원복 기준: 12시간+ 미판매 일반 재고 원가 {int(r.get('max_aged', 10_000_000)):,} 초과"])


def report_due(units, now=None, send=None, path=None, db_path=None, log=print):
    r = rules(path)
    now = now or time.time()
    if not r.get("six_since") or r.get("six_reported") or now < r["six_since"] + float(r.get("report_h", 6)) * 3600:
        return False
    if send is None:
        import alerts
        send = alerts.send
    send("daily", f"request_six_report:{r['six_since']}", report(now, units, path, db_path))
    r = rules(path)
    r["six_reported"] = round(now)
    save(r, path)
    log("[request six] 6 h report sent")
    return True


def check(units, log=print, now=None, send=None, path=None, db_path=None):
    try:
        guard(units, now=now, send=send, path=path, log=log)
    except Exception as e:
        log(f"[request six] guard failed: {type(e).__name__}: {e}")
    try:
        report_due(units, now=now, send=send, path=path, db_path=db_path, log=log)
    except Exception as e:
        log(f"[request six] report failed: {type(e).__name__}: {e}")
