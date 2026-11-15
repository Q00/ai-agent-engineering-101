"""Request-based lines 4 -> 6 (person 11/15 ~14:20: 5 regular + 1 new-SKU trial; the 1 h turnover target and the
shelf-life rules unchanged). request_lines.json holds the counts (depts/request_buy.lines_cfg) and this trial's keys:
  six_since      when 6 lines went on
  max_aged       12 h+ unsold normal stock (live inventory, cost <= qc.HIGH_MIN, not expired) over this cost
                 (10,000,000) -> back to base_lines (4 = 3 + 1) by itself, off_ts / off_why, one urgent Telegram
  report_h       one Telegram report_h (6) hours after six_since: normal sales per hour, profit and the 12 h+ unsold
                 normal stock, after vs the same length before (base_aged = the stock when it went on)
check() runs both, at every buy plan (depts/purchasing.make_plan, after the request-based lines), and note_plan()
keeps each plan's request lines (regular / trial, SKUs never bought this way before) in logs/request_plans.jsonl.
11/15 ~15:00 (person): the report adds 1) the five buyers' (BUYERS) low-price buys at SHOPS whose SKU my shop held
then, 2) my first reply time to those buyers (median / 90%), 3) the request lines per plan since six_since, the
normal SKU count vs base_skus (184), and the five buyers' deals per hour with me vs before and vs 11/14 09-15.
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
PLANS = os.path.join(HERE, "logs", "request_plans.jsonl")
BUYERS = ("inextro", "cheapest-in-town", "olstjr", "buyer-03", "dragonash")
SHOPS = ("nocha12", "nohaggle-hero", "25620027", "11im")
BASE_0914 = (datetime(2026, 11, 14, 9).timestamp(), datetime(2026, 11, 14, 15).timestamp())


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
    bl, bt = int(r.get("base_lines", 4)), int(r.get("base_trial", 1))     # 11/15: back to 3 + 1, whatever the trial
    r.update(lines=bl, trial=bt, regular=bl - bt, off_ts=round(now), off_why=why, six=six)
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
        try:                                          # 11/15 ~15:00 (person): 1) - 3), SKUs, the five buyers
            extra = extra_lines(t0, t1, L, d, units, r)
        except Exception as e:
            extra = [f"- 추가 항목 계산 실패 {type(e).__name__}: {e}"]
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
        f"- 원복 기준: 12시간+ 미판매 일반 재고 원가 {int(r.get('max_aged', 10_000_000)):,} 초과"] + extra)


def note_plan(planned, state_before, now=None, path=None):
    """planned: [(sku, why)] request lines this plan; state_before: request_buy state before the plan (new SKU = no
    first_buy there)."""
    row = {"ts": round(now or time.time()), "regular": sum(1 for _, w in planned if "trial" not in w),
           "trial": sum(1 for _, w in planned if "trial" in w),
           "new": [k for k, _ in planned if not (state_before.get(k) or {}).get("first_buy")],
           "skus": [k for k, _ in planned]}
    with open(path or PLANS, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def plans_text(t0, t1, path=None):
    xs = []
    try:
        with open(path or PLANS, encoding="utf-8") as f:
            for ln in f:
                try:
                    x = json.loads(ln)
                except ValueError:
                    continue
                if t0 <= x.get("ts", 0) <= t1 + 60:          # this plan's own row (rounded ts) included
                    xs.append(x)
    except OSError:
        pass
    if not xs:
        return "계획 기록 없음"
    new = sorted({k for x in xs for k in x["new"]})
    full = sum(1 for x in xs if x["regular"] >= 5 and x["trial"] >= 1)
    return (f"계획 {len(xs)}번, 요청 줄 정식 {sum(x['regular'] for x in xs)} / 시험 {sum(x['trial'] for x in xs)} "
            f"(5+1 다 찬 계획 {full}번), 새 SKU {len(new)}개" + (f" ({', '.join(new[:6])})" if new else ""))


def held_at(d, since):
    """{sku: [(supplied, sold or inf)]} of my units supplied after since (for 'held at t')."""
    from collections import defaultdict
    iv = defaultdict(list)
    for sku, sup, sold in d.execute("SELECT m.sku, m.supplied_at, (SELECT MIN(ts) FROM settles x WHERE x.unit=m.unit) "
                                    "FROM my_stock m WHERE m.supplied_at>=?", (since,)):
        iv[sku].append((sup, sold or 9e18))
    return iv


def elsewhere_held(t0, t1, d, iv):
    """(n, n whose SKU I held then) of BUYERS' low-price buys at SHOPS in [t0, t1)."""
    q = (f"SELECT ts, sku FROM settles WHERE buyer IN ({','.join('?' * len(BUYERS))}) AND shop IN "
         f"({','.join('?' * len(SHOPS))}) AND ts>=? AND ts<? AND amount<=150000")
    rs = d.execute(q, (*BUYERS, *SHOPS, t0, t1)).fetchall()
    have = sum(1 for ts, sku in rs if any(a <= ts < b and ts - a < 7 * 86400 for a, b in iv.get(sku, ())))
    return len(rs), have


def first_reply(t0, t1, d):
    """My first reply seconds (shop_msgs: first shop line - first buyer line) on my settled deals with BUYERS."""
    from collections import defaultdict
    task_of = {u: t for t, u in d.execute("SELECT task, unit FROM shop_deals WHERE outcome='receipt' AND ts>=? AND ts<?",
                                          (t0 - 600, t1))}
    msgs = defaultdict(list)
    for t, w, ts in d.execute("SELECT task, who, ts FROM shop_msgs WHERE ts>=? AND ts<?", (t0 - 600, t1 + 600)):
        msgs[t].append((ts, w))
    out = []
    for buyer, unit in d.execute(f"SELECT buyer, unit FROM settles WHERE shop=? AND buyer IN "
                                 f"({','.join('?' * len(BUYERS))}) AND ts>=? AND ts<?", (ME, *BUYERS, t0, t1)):
        m = sorted(msgs.get(task_of.get(unit), ()))
        fb = next((ts for ts, w in m if w == "buyer"), None)
        fs = next((ts for ts, w in m if w == "shop"), None)
        if fb and fs and fs >= fb:
            out.append(fs - fb)
    out.sort()
    return (len(out), out[len(out) // 2], out[int(0.9 * (len(out) - 1))]) if out else (0, None, None)


def buyers_per_h(t0, t1, d):
    h = max((t1 - t0) / 3600, 0.01)
    return {b: d.execute("SELECT COUNT(*) FROM settles WHERE shop=? AND buyer=? AND ts>=? AND ts<?",
                         (ME, b, t0, t1)).fetchone()[0] / h for b in BUYERS}


def normal_skus(units):
    from depts.qc import HIGH_MIN
    return len({u["sku"] for u in units if u.get("grade") != "expired" and 0 < (u.get("cost") or 0) <= HIGH_MIN})


def extra_lines(t0, t1, L, d, units, r, plans_path=None):
    iv = held_at(d, t0 - L - 7 * 86400)
    ea, eb = elsewhere_held(t0, t1, d, iv), elsewhere_held(t0 - L, t0, d, iv)
    pct = lambda x: f"{x[1]}/{x[0]} ({x[1] / x[0]:.1%})" if x[0] else "0/0"
    fa, fb = first_reply(t0, t1, d), first_reply(t0 - L, t0, d)
    sec = lambda x: f"중앙 {x[1]:.2f}s / 90% {x[2]:.2f}s (n {x[0]})" if x[0] else "-"
    pa, pb, p0 = buyers_per_h(t0, t1, d), buyers_per_h(t0 - L, t0, d), buyers_per_h(*BASE_0914, d)
    rec = ", ".join(f"{b} {pa[b]:.1f} vs {pb[b]:.1f} (11/14 09-15 {p0[b]:.1f})" for b in BUYERS)
    sa, s0 = sum(pa.values()), sum(p0.values())
    return [
        f"- 1) 다섯 buyer가 {'/'.join(SHOPS)}에서 산 저가 거래 중 그때 우리 재고에 있던 SKU: {pct(ea)} vs {pct(eb)}",
        f"- 2) 다섯 buyer 첫 메시지에 대한 우리 첫 응답(판매자 내부 기록): {sec(fa)} vs {sec(fb)}",
        f"- 3) 요청 기반 줄: {plans_text(t0, t1, plans_path)}",
        f"- 일반 SKU 수: 지금 {normal_skus(units)}개 vs 기준 {r.get('base_skus', 184)}개",
        f"- 다섯 buyer 우리 몫(시간당 건수) 합 {sa:.1f} vs 11/14 09-15 {s0:.1f} "
        f"({'회복' if s0 and sa >= 0.9 * s0 else '미회복'}): {rec}"]


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
