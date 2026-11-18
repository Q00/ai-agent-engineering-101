"""3-hour check (person 11/13 ~10:20, after the sofa warranty went on again; high 1 -> 2 and high_max 200 kept, buyer
unchanged). One Telegram ("daily") at every 3rd hour (12, 15, 18 ...), each number for the last 3 h vs the 3 h before:
  1. receipts / negotiations: high-price (cost > qc.HIGH_MIN) and sofa
  2. purchases from my shop by inextro, cheapest-in-town, nocha12 (count, amount)
  3. sofa revenue, profit and warranty refunds (3 h; and the 48 h auto-off rule: refunds vs profit)
  4. high-price revenue and profit
  5. cash and stock at cost
  6. 12 h+ unsold high-price units (live inventory) vs the 60M guard
  7. student rank, student total, gap to the 9th and 11th
  8. (11/13 ~10:40) high-price A SKUs on the 3% list (depts/a3_warranty.py) sold with a warranty: negotiations,
     receipts, sales, refunds
  9. inextro / cheapest-in-town / nocha12 purchases per hour since that warranty went live vs the 24 h before
  10-13 (11/13 ~13:10): student saved rank with the gaps above / below, my buys' score and count by grade, top shops
     by my saved score (grade_rule.shop_scores, the buyer's shop order), Book per hour
  14 (11/13 ~13:40): the kit's payment refusals ('[acp] not paying') by reason, from logs/market-kit.log(.1)
  15 (11/13 ~15:20, for 18:00): the 3% list SKUs (any warranty) since the A warranty went live vs the same length before,
     and the 24 h before scaled to that length: negotiations, receipts, sales, refunds
  16 (11/13 ~18:10, for 21:00): my deals per hour, grade C deals / score since the C >= 0.75 rule vs the same length
     before (10 and 11 already give the student saved 2nd / 3rd gap and the C deals / score of the 3 h)
  17 (11/14 ~16:10, for 18:00): shop score parts among the student shops - Book, revenue and stars: value, student
     rank and percentile now (live board) vs 3 h ago (logs/board_full.jsonl), the shop ALL from them, who passed me
     and whom I passed; cash, stock at cost and their sum: change over the 3 h vs the 3 h before
Read-only.
    python tools/check_1113.py [--once | --dry]
"""
import os
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
H = 3
BUYERS = ("inextro", "cheapest-in-town", "nocha12")
ME = "choo-chu"
END = datetime(2026, 11, 17, 23, 59).timestamp()


def _db(db_path):
    return sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=10)


def shop(t0, t1, db_path=None, is_sofa=None):
    """{high: (receipts, negotiations), sofa: (...), buyers: {name: (n, amount)}, high_sales: (n, revenue, profit)}"""
    from depts.qc import HIGH_MIN
    if is_sofa is None:
        from depts.sofa import is_sofa
    d = _db(db_path)
    try:
        deals = d.execute("SELECT sku, cost, outcome FROM shop_deals WHERE ts>=? AND ts<? AND outcome!='open'",
                          (t0, t1)).fetchall()
        sales = d.execute("SELECT s.buyer, s.amount, m.cost FROM settles s LEFT JOIN my_stock m ON m.unit=s.unit "
                          "WHERE s.shop=? AND s.ts>=? AND s.ts<?", (ME, t0, t1)).fetchall()
    finally:
        d.close()
    hi = [o for _, c, o in deals if (c or 0) > HIGH_MIN]
    so = [o for k, _, o in deals if is_sofa(k)]
    hs = [(a or 0, c or 0) for _, a, c in sales if (c or 0) > HIGH_MIN]
    return {"high": (hi.count("receipt"), len(hi)), "sofa": (so.count("receipt"), len(so)),
            "buyers": {b: (sum(1 for x in sales if x[0] == b), sum(x[1] or 0 for x in sales if x[0] == b)) for b in BUYERS},
            "high_sales": (len(hs), sum(a for a, _ in hs), sum(a - c for a, c in hs))}


def cash(now, db_path=None):
    d = _db(db_path)
    try:
        return d.execute("SELECT balance, stock_cost FROM cash WHERE ts<=? ORDER BY ts DESC LIMIT 1", (now,)).fetchone()
    finally:
        d.close()


def rank(j, me="Choo-chu"):
    st = sorted((p for p in j.get("players") or [] if p.get("student")), key=lambda p: -(p.get("student_total") or 0))
    i = next(k for k, p in enumerate(st) if p["player"] == me)
    tot = lambda k: st[k]["student_total"] if 0 <= k < len(st) else None
    return {"rank": i + 1, "n": len(st), "total": st[i]["student_total"], "shop": st[i].get("student_shop"),
            "buyer": st[i].get("student_buyer"), "ninth": tot(8), "eleventh": tot(10),
            "ninth_name": st[8]["player"] if len(st) > 8 else None, "eleventh_name": st[10]["player"] if len(st) > 10 else None}


def text(now, cur, prev, sofa3, sofa3p, sofa48, cash_now, cash_prev, aged, guard, rk):
    r = lambda v: f"{v[0]}/{v[1]} ({v[0] / v[1]:.1%})" if v[1] else f"{v[0]}/0"
    out = [f"3시간 점검 {datetime.fromtimestamp(now):%m-%d %H:%M} (최근 {H}시간 vs 그 전 {H}시간)",
           f"1. 영수증/협상: 고가 {r(cur['high'])} vs {r(prev['high'])}; 소파 {r(cur['sofa'])} vs {r(prev['sofa'])}",
           "2. 구매량(우리 가게): " + ", ".join(f"{b} {cur['buyers'][b][0]}건 {cur['buyers'][b][1]:,} vs {prev['buyers'][b][0]}건"
                                         for b in BUYERS),
           f"3. 소파: 매출 {sofa3['revenue']:,} 이익 {sofa3['profit']:+,} 환불 {sofa3['refunds']}건 {sofa3['refund_amount']:,} "
           f"vs 매출 {sofa3p['revenue']:,} 환불 {sofa3p['refunds']}건 | 48시간 환불 {sofa48['refund_amount']:,} vs 이익 "
           f"{sofa48['profit']:,} (환불 > 이익이면 보증 자동 해제)",
           f"4. 고가 판매: {cur['high_sales'][0]}개 매출 {cur['high_sales'][1]:,} 이익 {cur['high_sales'][2]:+,} vs "
           f"{prev['high_sales'][0]}개 {prev['high_sales'][1]:,} {prev['high_sales'][2]:+,}",
           "5. 현금: " + (f"{cash_now[0]:,} (재고 원가 {cash_now[1]:,})" if cash_now else "-")
           + (f" vs 3시간 전 {cash_prev[0]:,} ({cash_prev[1]:,})" if cash_prev else ""),
           f"6. 12시간+ 미판매 고가: {aged[0]}개 {aged[1]:,} (끄는 기준 {guard:,} 초과)"]
    if rk:
        g = lambda v: "-" if v is None else f"{rk['total'] - v:+.3f}"
        out.append(f"7. 학생 순위 {rk['rank']}위/{rk['n']} 합계 {rk['total']:.3f} (shop {rk['shop']}, buyer {rk['buyer']}); "
                   f"9위 {rk['ninth_name']} {rk['ninth']} ({g(rk['ninth'])}), 11위 {rk['eleventh_name']} {rk['eleventh']} "
                   f"({g(rk['eleventh'])})")
    else:
        out.append("7. 학생 순위: board 읽기 실패")
    return "\n".join(out)


def build(now, db_path=None, board=None, units=None, sofa_numbers=None, is_sofa=None, a3_path=None, catalog=None,
          shop_scores=None, board_path=None, kit_logs=None):
    import alerts
    from depts import wide_trial
    sofa_numbers = sofa_numbers or alerts.sofa_numbers
    t0, tp = now - H * 3600, now - 2 * H * 3600
    cur, prev = shop(t0, now, db_path, is_sofa), shop(tp, t0, db_path, is_sofa)
    aged = wide_trial.aged_high(units if units is not None else wide_trial.live_units(), now)
    guard = int(wide_trial.rules().get("max_cost") or 60_000_000)
    try:
        if board is None:
            import student_rank_watch
            board = student_rank_watch.fetch()
        rk = rank(board)
    except Exception:
        rk = None
    out = text(now, cur, prev, sofa_numbers(t0, now), sofa_numbers(tp, t0), sofa_numbers(now - 48 * 3600, now),
               cash(now, db_path), cash(t0, db_path), aged, guard, rk)
    from depts import a3_warranty
    r = a3_warranty.rules(a3_path)
    out += "\n" + a3_text(now, r, a3(t0, now, set(r.get("skus") or {}), db_path),
                          a3(tp, t0, set(r.get("skus") or {}), db_path), db_path)
    out += "\n" + a3_since_text(now, r, db_path)
    out += "\n" + buyer_text(now, board, db_path, catalog, shop_scores, board_path, kit_logs)
    return out + "\n" + score_text(now, board or {}, db_path, board_path)      # 11/14 ~16:10: 17


SCORE_KEYS = (("book", "Book", 0.5), ("revenue", "매출", 0.3), ("stars_rank", "별점", 0.2))


def _snap(t, path=None):
    """The logs/board_full.jsonl row nearest t (within 1800 s), or None."""
    import json
    best = None
    with open(path or os.path.join(HERE, "logs", "board_full.jsonl"), encoding="utf-8") as f:
        for ln in f:
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(e, dict) or "ts" not in e or "s" not in e:
                continue
            if abs(e["ts"] - t) <= 1800 and (best is None or abs(e["ts"] - t) < abs(best["ts"] - t)):
                best = e
    return best


def shop_parts(S, me=ME):
    """{key: (my value, student rank, percentile)} and "all" for {student shop: {book, revenue, stars_rank}}; the
    percentile and ALL as the board's (board_speed.pct / shop_all)."""
    import board_speed
    out, tot = {}, 0.0
    for k, _, w in SCORE_KEYS:
        vals = [x[k] for x in S.values()]
        v = S[me][k]
        out[k] = (v, 1 + sum(1 for x in vals if x > v), board_speed.pct(vals, v))
        tot += w * out[k][2]
    out["all"] = tot
    return out


def score_text(now, board, db_path=None, board_path=None, me=ME):
    """17. shop score parts among the students now vs 3 h ago, who passed whom; cash / stock change."""
    t0, tp = now - H * 3600, now - 2 * H * 3600
    out = []
    try:
        stud = {(p.get("shop") or {}).get("name") for p in board.get("players") or [] if p.get("student")}
        cur = {x["name"]: x for x in board.get("shops") or [] if x.get("name") in stud}
        row = _snap(t0, board_path)
        prev = {n: x for n, x in (row or {}).get("s", {}).items() if n in stud}
        a = shop_parts(cur, me)
        b = shop_parts(prev, me) if me in prev else None
        f = lambda k, v: f"{v:,.0f}" if k != "stars_rank" else f"{v}"
        parts = []
        for k, name, _ in SCORE_KEYS:
            x = f"{name} {f(k, a[k][0])} {a[k][1]}위({a[k][2]:.3f})"
            if b:
                x += f" vs {f(k, b[k][0])} {b[k][1]}위({b[k][2]:.3f})"
                up = [n for n in cur if n != me and n in prev and prev[n][k] <= prev[me][k] and cur[n][k] > cur[me][k]]
                dn = [n for n in cur if n != me and n in prev and prev[n][k] > prev[me][k] and cur[n][k] <= cur[me][k]]
                if up:
                    x += " [우리를 넘음: " + ", ".join(up) + "]"
                if dn:
                    x += " [우리가 넘음: " + ", ".join(dn) + "]"
            parts.append(x)
        out.append(f"17. shop 점수(학생 {len(cur)}곳 기준) {a['all']:.3f}" + (f" vs 3시간 전 {b['all']:.3f}" if b else "")
                   + ": " + "; ".join(parts))
    except Exception as e:
        out.append(f"17. shop 점수 구성: 실패 {type(e).__name__}")
    c1, c0, cp = cash(now, db_path), cash(t0, db_path), cash(tp, db_path)
    d = lambda x, y: f"현금 {x[0] - y[0]:+,} 재고 원가 {x[1] - y[1]:+,} 합 {x[0] + x[1] - y[0] - y[1]:+,}"
    if c1 and c0:
        out.append(f"    현금·재고 변화: 최근 3시간 {d(c1, c0)}" + (f" | 그 전 3시간 {d(c0, cp)}" if cp else "")
                   + f" (지금 현금 {c1[0]:,}, 재고 원가 {c1[1]:,})")
    else:
        out.append("    현금·재고 변화: 읽기 실패")
    return "\n".join(out)


def saved_line(j, me=ME):
    """10. my saved vs the students' 1st / 3rd (or the one above / below me)."""
    names = {(p.get("buyer") or {}).get("name") for p in j.get("players") or [] if p.get("student")}
    st = sorted((b for b in j.get("buyers") or [] if b.get("name") in names), key=lambda b: -(b.get("saved") or 0))
    i = next(k for k, b in enumerate(st) if b["name"] == me)
    v = st[i]["saved"]
    nb = lambda k: f"{k + 1}위 {st[k]['name']} {st[k]['saved']} ({v - st[k]['saved']:+.4f})" if 0 <= k < len(st) else "-"
    return f"10. 학생 saved {i + 1}위 {v}: 위 {nb(i - 1)}, 아래 {nb(i + 1)}"


def grade_scores(t0, t1, db_path=None, catalog=None, me=ME):
    """{grade: (n, mean saved score)} of my buys in [t0, t1): min(1, (list - paid) / (list - cost_share x list))."""
    from depts import grade_rule
    if catalog is None:
        from depts.substitute import catalog as _cat
        catalog = _cat()
    cs = grade_rule.rules().get("cost_share") or grade_rule.DEFAULT["cost_share"]
    d = _db(db_path)
    try:
        rows = d.execute("SELECT sku, grade, amount FROM settles WHERE buyer=? AND ts>=? AND ts<? AND amount IS NOT NULL",
                         (me, t0, t1)).fetchall()
    finally:
        d.close()
    by = {}
    for sku, g, a in rows:
        lp = (catalog.get(sku) or {}).get("list_price")
        if not lp:
            continue
        g = g if g in cs else "B"
        by.setdefault(g, []).append(min(1.0, (lp - a) / (lp * (1 - cs[g]))))
    return {g: (len(v), sum(v) / len(v)) for g, v in by.items()}


def book_rate(t0, t1, path=None, me=ME):
    """My board book change per hour between the snapshots nearest t0 and t1 (logs/board_full.jsonl)."""
    import json
    best = [None, None]
    with open(path or os.path.join(HERE, "logs", "board_full.jsonl"), encoding="utf-8") as f:
        for ln in f:
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            for k, t in enumerate((t0, t1)):
                if abs(e["ts"] - t) <= 1800 and (best[k] is None or abs(e["ts"] - t) < abs(best[k]["ts"] - t)):
                    best[k] = e
    a, b = best
    if not a or not b or b["ts"] <= a["ts"] or me not in a["s"] or me not in b["s"]:
        return None
    return (b["s"][me]["book"] - a["s"][me]["book"]) / ((b["ts"] - a["ts"]) / 3600)


def buyer_text(now, board, db_path=None, catalog=None, shop_scores=None, board_path=None, kit_logs=None):
    """11/13 ~13:10 (person): 10. student saved 2nd / 3rd gap, 11. score and deals by grade, 12. top shops by my
    score, 13. Book per hour (3 h vs the 3 h before)."""
    t0, tp = now - H * 3600, now - 2 * H * 3600
    out = []
    try:
        out.append(saved_line(board))
    except Exception:
        out.append("10. 학생 saved: board 읽기 실패")
    cur, prev = grade_scores(t0, now, db_path, catalog), grade_scores(tp, t0, db_path, catalog)
    f = lambda x, g: f"{x[g][0]}건 {x[g][1]:.3f}" if g in x else "0건"
    out.append("11. 등급별 거래·점수(우리 구매): " + ", ".join(f"{g} {f(cur, g)} vs {f(prev, g)}" for g in "ABC"))
    try:
        if shop_scores is None:
            from depts import grade_rule
            shop_scores = grade_rule.shop_scores
        sc, med = shop_scores()
        top = sorted(sc.items(), key=lambda kv: -kv[1])[:5]
        out.append(f"12. 가게별 점수 상위(24시간, 10건 미만은 중앙값 {med}): " + ", ".join(f"{k} {v}" for k, v in top))
    except Exception as e:
        out.append(f"12. 가게별 점수: 실패 {type(e).__name__}")
    try:
        a, b = book_rate(t0, now, board_path), book_rate(tp, t0, board_path)
        r = lambda v: "-" if v is None else f"{v:+,.0f}"
        out.append(f"13. Book 시간당: {r(a)} vs {r(b)}")
    except OSError:
        out.append("13. Book 시간당: 읽기 실패")
    try:
        out.append(acp_text(now, kit_logs))
    except OSError:
        out.append("14. kit 결제 거절: 로그 읽기 실패")
    try:
        out.append(deals_text(now, cur, prev, db_path, catalog))
    except Exception as e:
        out.append(f"16. 시간당 deals: 실패 {type(e).__name__}")
    return "\n".join(out)


def deals_text(now, cur, prev, db_path=None, catalog=None, c_since=None):
    """11/13 ~18:10 (person, for 21:00): my deals per hour (3 h vs the 3 h before) and grade C deals / score since the
    C >= 0.75 rule (grade_rule.json c_min_ts) vs the same length before it."""
    n = lambda x: sum(v[0] for v in x.values()) / H
    s = f"16. 시간당 deals(우리 구매): {n(cur):.1f} vs {n(prev):.1f}"
    if c_since is None:
        from depts import grade_rule
        c_since = grade_rule.rules().get("c_min_ts")
    if c_since and now > c_since:
        L = now - c_since
        a, b = grade_scores(c_since, now, db_path, catalog), grade_scores(c_since - L, c_since, db_path, catalog)
        f = lambda x: (f"{x['C'][0]}건 {x['C'][1]:.3f} (시간당 {x['C'][0] / (L / 3600):.1f})" if "C" in x else "0건")
        s += (f"; C등급 0.75 기준 적용({datetime.fromtimestamp(c_since):%H:%M}) 후 {L / 3600:.1f}시간 {f(a)} vs 직전 같은 "
              f"길이 {f(b)}")
    return s


KIT_LOGS = [os.path.join(HERE, "logs", "market-kit.log.1"), os.path.join(HERE, "logs", "market-kit.log")]


def acp_lines(now, paths=None):
    """[(ts, line)] of the kit's '[acp] not paying: <reason>' lines. The log has only HH:MM:SS: dated backwards from
    the newest line (today, or yesterday when its clock is past now), one day earlier at each wrap."""
    import re
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
        if "[acp] not paying:" in ln:
            out.append((day0 + sec, ln))
    return out[::-1]


def acp_reasons(t0, t1, lines):
    """{reason with numbers as N: count} of the kit's payment refusals in [t0, t1)."""
    import re
    from collections import Counter
    return Counter(re.sub(r"\d+(?:\.\d+)?", "N", ln.split("not paying:", 1)[1].strip())[:60]
                   for ts, ln in lines if t0 <= ts < t1)


def acp_text(now, paths=None):
    """14. (11/13 ~13:40) the kit's payment refusals by reason, 3 h vs the 3 h before."""
    lines = acp_lines(now, paths)
    cur, prev = acp_reasons(now - H * 3600, now, lines), acp_reasons(now - 2 * H * 3600, now - H * 3600, lines)
    keys = sorted(set(cur) | set(prev), key=lambda k: -(cur[k] + prev[k]))
    return (f"14. kit 결제 거절: {sum(cur.values())}건 vs {sum(prev.values())}건"
            + ("; " + ", ".join(f"{k} {cur[k]} vs {prev[k]}" for k in keys[:5]) if keys else ""))


def a3(t0, t1, skus, db_path=None, warranty_only=True):
    """11/13 ~10:40: listed high-price A SKUs with a warranty in [t0, t1): negotiations, receipts, sales (n, revenue),
    refunds (n, amount). warranty_only=False (11/13 ~15:20, item 15): the same SKUs with or without a warranty."""
    from depts.qc import HIGH_MIN
    if not skus:
        return {"nego": 0, "rec": 0, "sold": 0, "rev": 0, "ref": 0, "ref_amt": 0}
    q = ",".join("?" * len(skus))
    w = " AND warranty_hours>0" if warranty_only else ""
    d = _db(db_path)
    try:
        nego, rec = d.execute(f"SELECT COUNT(*), COALESCE(SUM(outcome='receipt'), 0) FROM shop_deals WHERE sku IN ({q}) "
                              f"AND cost>?{w} AND outcome!='open' AND ts>=? AND ts<?",
                              (*skus, HIGH_MIN, t0, t1)).fetchone()
        sold, rev = d.execute(f"SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM settles WHERE shop=? AND sku IN ({q})"
                              f"{w} AND ts>=? AND ts<?", (ME, *skus, t0, t1)).fetchone()
        ref, amt = d.execute(f"SELECT COUNT(*), COALESCE(SUM(refunded), 0) FROM broken WHERE shop=? AND sku IN ({q}) "
                             "AND refunded>0 AND ts>=? AND ts<?", (ME, *skus, t0, t1)).fetchone()
    finally:
        d.close()
    return {"nego": nego, "rec": rec, "sold": sold, "rev": rev, "ref": ref, "ref_amt": amt}


def buyers_per_h(t0, t1, db_path=None):
    d = _db(db_path)
    try:
        return {b: d.execute("SELECT COUNT(*) FROM settles WHERE shop=? AND buyer=? AND ts>=? AND ts<?",
                             (ME, b, t0, t1)).fetchone()[0] / max((t1 - t0) / 3600, 1e-9) for b in BUYERS}
    finally:
        d.close()


def a3_text(now, r, cur, prev, db_path=None):
    rate = lambda x: f"{x['rec']}/{x['nego']} ({x['rec'] / x['nego']:.1%})" if x["nego"] else "0/0"
    since = r.get("applied_ts")
    head = (f"8. 고가 A등급 보증(3% 목록 {len(r.get('skus') or {})}개, "
            + (f"{datetime.fromtimestamp(since):%m-%d %H:%M} 적용" if since and r.get("enabled") else "적용 전") + "): ")
    out = [head + f"보증 협상 영수증 {rate(cur)} vs {rate(prev)}; 판매 {cur['sold']}개 {cur['rev']:,} vs {prev['sold']}개 "
                  f"{prev['rev']:,}; 환불 {cur['ref']}건 {cur['ref_amt']:,} vs {prev['ref']}건 {prev['ref_amt']:,}"]
    if since and r.get("enabled") and now > since:
        a, b = buyers_per_h(since, now, db_path), buyers_per_h(since - 86400, since, db_path)
        out.append("9. 구매량 변화(우리 가게, 시간당; 보증 적용 후 vs 적용 전 24시간): "
                   + ", ".join(f"{k} {a[k]:.1f} vs {b[k]:.1f}" for k in BUYERS))
    return "\n".join(out)


def a3_since_text(now, r, db_path=None):
    """11/13 ~15:20 (person, for the 18:00 check): the 3% list SKUs (with or without a warranty) since the warranty went
    live (L h) vs the same L h right before it, and the 24 h before it scaled to L h."""
    since, skus = r.get("applied_ts"), set(r.get("skus") or {})
    if not (since and r.get("enabled") and now > since and skus):
        return "15. 보증 SKU 적용 후 비교: 적용 전"
    L = now - since
    a, b, c = (a3(since, now, skus, db_path, False), a3(since - L, since, skus, db_path, False),
               a3(since - 86400, since, skus, db_path, False))
    f = L / 86400
    one = lambda x, k=1.0: (f"협상 {x['nego'] * k:.1f} 영수증 {x['rec'] * k:.1f}"
                            + (f" ({x['rec'] / x['nego']:.1%})" if x["nego"] else "")
                            + f" 판매 {x['sold'] * k:.1f}개 {x['rev'] * k:,.0f} 환불 {x['ref'] * k:.1f}건 {x['ref_amt'] * k:,.0f}")
    return (f"15. 보증 SKU {len(skus)}개(보증 유무 무관) 적용 후 {L / 3600:.1f}시간: {one(a)} | 직전 같은 길이: {one(b)} | "
            f"적용 전 24시간을 {L / 3600:.1f}시간으로 환산: {one(c, f)}")


def main():
    if "--dry" in sys.argv:
        print(build(time.time()))
        return
    import alerts
    import student_rank_watch as W
    last = None
    while time.time() < END + 3600:
        now = time.time()
        mark = W.due(now, last)
        if mark and now - mark < 1800:
            try:
                alerts.send("daily", f"check_1113:{int(mark)}", build(now))
                last = mark
            except Exception as e:
                print(f"{datetime.now():%H:%M:%S} failed: {type(e).__name__}: {e}", flush=True)
        elif mark:
            last = mark
        if "--once" in sys.argv:
            return
        time.sleep(60)


if __name__ == "__main__":
    main()
