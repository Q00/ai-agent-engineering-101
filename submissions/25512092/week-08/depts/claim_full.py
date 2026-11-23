"""Grade claims on every non-food receipt (person 11/15 ~16:00: receipt_rules.json claim_share 0.5 -> 1.0, no seller
restart: depts/receipt_exp.claim_arm reads it on every negotiation; food stays without a claim). Keys in
receipt_rules.json:
  full_since       when claim_share went to 1.0;  full_prev  the share to go back to (0.5)
  guard            one false claim of mine in the ledger since full_since (market.db settles claim_honest = 0)
                   -> claim_share back to full_prev, full_off_ts / full_off_why, one urgent Telegram
  report           one Telegram report_h (6) hours after full_since: my non-food sales, false claims, the share of
                   non-food sales with a claim, my checkout refusals ('[acp] checkout refused' in the kit log), the
                   five buyers' deals per hour with me and normal sales per hour - after vs the same length before
check() runs both, at every buy plan (depts/purchasing.make_plan).
"""
import json
import os
import re
import sqlite3
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "receipt_rules.json")
KIT_LOGS = [os.path.join(HERE, "logs", "market-kit.log.1"), os.path.join(HERE, "logs", "market-kit.log")]
ME = "choo-chu"


def rules(path=None):
    try:
        with open(path or RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(r, path=None):
    tmp = (path or RULES) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False)
    os.replace(tmp, path or RULES)


def _db(db_path=None):
    return sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=60)


def false_claims(t0, t1, d):
    return d.execute("SELECT COUNT(*) FROM settles WHERE shop=? AND claim_honest=0 AND ts>=? AND ts<?",
                     (ME, t0, t1)).fetchone()[0]


def _catalog():
    with open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8") as f:
        return json.load(f)


def guard(now=None, send=None, path=None, db_path=None, log=print, catalog=None):
    """'tripped', 'ok' or 'off'. 11/15 ~16:40 (person): a false claim on a food SKU since food_since -> food claims off
    (food_claim false, non-food kept); a false claim on a non-food SKU since full_since -> claim_share back to
    full_prev (0.5). One urgent Telegram each."""
    r = rules(path)
    goods_on = r.get("full_since") and not r.get("full_off_ts")
    food_on = r.get("food_claim") and r.get("food_since")
    if not goods_on and not food_on:
        return "off"
    now = now or time.time()
    t0 = min(x for x in (r.get("full_since") if goods_on else None, r.get("food_since") if food_on else None) if x)
    d = _db(db_path)
    try:
        rows = d.execute("SELECT seq, ts, sku, grade FROM settles WHERE shop=? AND claim_honest=0 AND ts>=? ORDER BY ts",
                         (ME, t0)).fetchall()
    finally:
        d.close()
    if not rows:
        return "ok"
    cat = _catalog() if catalog is None else catalog
    is_food = lambda sku: (cat.get(sku) or {}).get("kind") == "food"
    food = [x for x in rows if food_on and is_food(x[2]) and x[1] >= r["food_since"]]
    goods = [x for x in rows if goods_on and not is_food(x[2]) and x[1] >= r["full_since"]]
    if not food and not goods:
        return "ok"
    if send is None:
        import alerts
        send = alerts.send
    txt = lambda xs: f"거짓 등급 주장 {len(xs)}건 (원장 " + ", ".join(f"#{s} {k} 실제 {g}" for s, _, k, g in xs[:3]) + ")"
    msgs = []
    if food:
        why = txt(food)
        r.update(food_claim=False, food_off_ts=round(now), food_off_why=why)
        msgs.append(("claim_food_off", f"식품 등급 주장 자동 끔: {why}. 비식품 A·B 주장은 그대로"))
        log(f"[claim full] food guard tripped: {why} -> food claims off")
    if goods:
        why = txt(goods)
        r.update(claim_share=float(r.get("full_prev", 0.5)), full_off_ts=round(now), full_off_why=why)
        msgs.append(("claim_full_off", f"비식품 등급 주장 1.0 자동 원복: {why}. claim_share {r['claim_share']}로 돌아감"))
        log(f"[claim full] guard tripped: {why} -> claim_share {r['claim_share']}")
    save(r, path)
    for key, m in msgs:
        try:
            send("urgent", f"{key}:{round(now)}", m)
        except Exception as e:
            log(f"[claim full] alert failed: {type(e).__name__}: {e}")
    return "tripped"


def food_sales(t0, t1, d, catalog):
    rs = d.execute("SELECT sku, claim_honest FROM settles WHERE shop=? AND ts>=? AND ts<?", (ME, t0, t1)).fetchall()
    g = [c for sku, c in rs if (catalog.get(sku) or {}).get("kind") == "food"]
    return len(g), sum(1 for c in g if c is not None)


def food_report(now, path=None, db_path=None, catalog=None, kit_paths=None):
    """11/15 ~16:40 (person): food claims (A / B, next drop > food_n_h) report_h after food_since."""
    from depts import request_six
    r = rules(path)
    t0 = r["food_since"]
    t1 = min(now, r["food_off_ts"]) if r.get("food_off_ts") else now
    L = max(t1 - t0, 60)
    h = L / 3600
    catalog = _catalog() if catalog is None else catalog
    ref = kit_lines(now, "[acp] checkout refused", kit_paths)
    nref = lambda a, b: sum(1 for ts, _ in ref if a <= ts < b)
    d = _db(db_path)
    try:
        fa, fb = food_sales(t0, t1, d, catalog), food_sales(t0 - L, t0, d, catalog)
        xa, xb = false_claims(t0, t1, d), false_claims(t0 - L, t0, d)
        na, nb = request_six.window(t0, t1, d), request_six.window(t0 - L, t0, d)
        pa, pb = request_six.buyers_per_h(t0, t1, d), request_six.buyers_per_h(t0 - L, t0, d)
    finally:
        d.close()
    share = lambda x: f"{x[1]}/{x[0]} ({x[1] / x[0]:.0%})" if x[0] else "0/0"
    head = (f"식품 등급 주장 {h:.1f}시간 점검 ({datetime.fromtimestamp(t0):%m-%d %H:%M} 적용, A·B만, 다음 하락까지 "
            f"{r.get('food_n_h', 3)}시간 이내 제외"
            + (f", {datetime.fromtimestamp(r['food_off_ts']):%H:%M} 자동 끔: {r.get('food_off_why')}"
               if r.get("food_off_ts") else "") + ", 후 vs 직전 같은 길이)")
    return "\n".join([
        head,
        f"- 식품 판매 중 주장 있음 {share(fa)} vs {share(fb)}",
        f"- 거짓 주장 기록 {xa}건 vs {xb}건",
        f"- 결제 거절(우리 가게 checkout refused) {nref(t0, t1)}건 vs {nref(t0 - L, t0)}건",
        f"- 일반 판매/h {na[0] / h:.1f} vs {nb[0] / h:.1f}",
        f"- 다섯 buyer 우리 몫 합 {sum(pa.values()):.1f}/h vs {sum(pb.values()):.1f}/h: "
        + ", ".join(f"{b} {pa[b]:.1f} vs {pb[b]:.1f}" for b in pa)])


def food_report_due(now=None, send=None, path=None, log=print, **kw):
    r = rules(path)
    now = now or time.time()
    if not r.get("food_since") or r.get("food_reported") or now < r["food_since"] + float(r.get("report_h", 6)) * 3600:
        return False
    if send is None:
        import alerts
        send = alerts.send
    send("daily", f"claim_food_report:{r['food_since']}", food_report(now, path=path, **kw))
    r = rules(path)
    r["food_reported"] = round(now)
    save(r, path)
    log("[claim full] food 6 h report sent")
    return True


def kit_lines(now, pattern, paths=None):
    """[(ts, line)] of kit log lines with `pattern`; the log has only HH:MM:SS: dated backwards from the newest line
    (as tools/check_1113.acp_lines)."""
    rows = []
    for p in paths or KIT_LOGS:
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8", errors="replace") as f:
            for ln in f:
                m = re.match(r"(\d\d):(\d\d):(\d\d) ", ln)
                if m:
                    rows.append((int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]), ln))
    out = []
    d = datetime.fromtimestamp(now)
    day0 = datetime(d.year, d.month, d.day).timestamp()
    if rows and rows[-1][0] > (now - day0) + 300:
        day0 -= 86400
    nxt = None
    for sec, ln in reversed(rows):
        if nxt is not None and sec > nxt + 3600:
            day0 -= 86400
        nxt = sec
        if pattern in ln:
            out.append((day0 + sec, ln))
    return out[::-1]


def goods_sales(t0, t1, d, catalog):
    rs = d.execute("SELECT sku, claim_honest FROM settles WHERE shop=? AND ts>=? AND ts<?", (ME, t0, t1)).fetchall()
    g = [c for sku, c in rs if (catalog.get(sku) or {}).get("kind") == "goods"]
    return len(g), sum(1 for c in g if c is not None)


def report(now, path=None, db_path=None, catalog=None, kit_paths=None):
    from depts import request_six
    r = rules(path)
    t0 = r["full_since"]
    t1 = min(now, r["full_off_ts"]) if r.get("full_off_ts") else now
    L = max(t1 - t0, 60)
    h = L / 3600
    if catalog is None:
        with open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8") as f:
            catalog = json.load(f)
    ref = kit_lines(now, "[acp] checkout refused", kit_paths)
    nref = lambda a, b: sum(1 for ts, _ in ref if a <= ts < b)
    d = _db(db_path)
    try:
        ga, gb = goods_sales(t0, t1, d, catalog), goods_sales(t0 - L, t0, d, catalog)
        fa, fb = false_claims(t0, t1, d), false_claims(t0 - L, t0, d)
        na, nb = request_six.window(t0, t1, d), request_six.window(t0 - L, t0, d)
        pa, pb = request_six.buyers_per_h(t0, t1, d), request_six.buyers_per_h(t0 - L, t0, d)
    finally:
        d.close()
    share = lambda x: f"{x[1] / x[0]:.0%}" if x[0] else "-"
    head = (f"비식품 등급 주장 1.0 {h:.1f}시간 점검 ({datetime.fromtimestamp(t0):%m-%d %H:%M} 적용"
            + (f", {datetime.fromtimestamp(r['full_off_ts']):%H:%M} 자동 원복: {r.get('full_off_why')}"
               if r.get("full_off_ts") else "") + ", 후 vs 직전 같은 길이)")
    return "\n".join([
        head,
        f"- 비식품 판매 {ga[0]}건 (주장 있음 {share(ga)}) vs {gb[0]}건 ({share(gb)})",
        f"- 거짓 주장 기록 {fa}건 vs {fb}건",
        f"- 결제 거절(우리 가게 checkout refused) {nref(t0, t1)}건 vs {nref(t0 - L, t0)}건",
        f"- 일반 판매/h {na[0] / h:.1f} vs {nb[0] / h:.1f}",
        f"- 다섯 buyer 우리 몫 합 {sum(pa.values()):.1f}/h vs {sum(pb.values()):.1f}/h: "
        + ", ".join(f"{b} {pa[b]:.1f} vs {pb[b]:.1f}" for b in pa)])


def report_due(now=None, send=None, path=None, log=print, **kw):
    r = rules(path)
    now = now or time.time()
    if not r.get("full_since") or r.get("full_reported") or now < r["full_since"] + float(r.get("report_h", 6)) * 3600:
        return False
    if send is None:
        import alerts
        send = alerts.send
    send("daily", f"claim_full_report:{r['full_since']}", report(now, path=path, **kw))
    r = rules(path)
    r["full_reported"] = round(now)
    save(r, path)
    log("[claim full] 6 h report sent")
    return True


def check(log=print, **kw):
    try:
        guard(log=log, **{k: v for k, v in kw.items() if k in ("now", "send", "path", "db_path", "catalog")})
    except Exception as e:
        log(f"[claim full] guard failed: {type(e).__name__}: {e}")
    for f in (report_due, food_report_due):
        try:
            f(log=log, **kw)
        except Exception as e:
            log(f"[claim full] report failed: {type(e).__name__}: {e}")
