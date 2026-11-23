"""Average order value 1+2 (person's decision 11/08 ~13:30): high-price units held 30 -> 60 (policy.json and the QC
table; POLICY_RANGES, CEO and QC caps were raised to 60 in 9743640) and HIGH_MAX 1.0M -> 1.5M (the analyst path:
proposal row, b0 before/after, commit + push, its own 3 h check). The buy guard, the STOCK_CAP 20M brake and the
high-price share 0.6 do not change. 3 h later one Telegram, 3 h after vs 3 h before:
  high-price buys (lines / units) and sales, share of sales >= 500,000 (count, revenue), average sale price,
  high-price stock at cost, turnover of high-price units, buying held back by cause (tools/cap20_again.causes)
  + read-only: the 30 SKUs buyers asked for most in the last 24 h that I had no unit of (substitute offers and
  "not in stock"), with list price, my sales and the top-10 shops' sales of each in 24 h
    python tools/aov_rollout.py           (log in logs/aov_rollout.log)
    python tools/aov_rollout.py --report-dry
"""
import json
import os
import re
import sqlite3
import statistics
import sys
import time
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
import alerts  # noqa: E402
import decisions  # noqa: E402

LOG = os.path.join(HERE, "logs", "aov_rollout.log")
WHY = "person's decision 11/08 ~13:30: average order value 1+2 (high_max 60, HIGH_MAX 1.5M)"
ME = "choo-chu"
H = 3
BIG = 500_000


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def window(db, t0, t1):
    from depts.qc import HIGH_MIN
    cost = dict(db.execute("SELECT unit, cost FROM my_stock").fetchall())
    sup = dict(db.execute("SELECT unit, supplied_at FROM my_stock").fetchall())
    s = db.execute("SELECT unit, amount, ts FROM settles WHERE shop=? AND ts>=? AND ts<?", (ME, t0, t1)).fetchall()
    amt = [a for _, a, _ in s]
    high = [(u, a, ts) for u, a, ts in s if (cost.get(u) or 0) > HIGH_MIN]
    turn = [(ts - sup[u]) / 3600 for u, a, ts in high if sup.get(u)]
    lines = units = 0
    with open(os.path.join(HERE, "logs", "decisions.jsonl"), encoding="utf-8") as f:
        for ln in f:
            if '"buy"' not in ln or "[high]" not in ln:
                continue
            d = json.loads(ln)
            if t0 <= d["ts"] < t1 and d.get("action") == "buy" and (d.get("reason") or "").startswith("[high]"):
                lines += 1
                m = re.search(r"buy (\d+)", str(d.get("value")))
                units += int(m.group(1)) if m else 1
    held = db.execute("SELECT m.unit, m.cost FROM my_stock m WHERE m.cost>? AND m.supplied_at<? AND NOT EXISTS "
                      "(SELECT 1 FROM settles x WHERE x.unit=m.unit AND x.ts<?)", (HIGH_MIN, t1, t1)).fetchall()
    return {"sales": len(s), "revenue": sum(amt), "avg": round(sum(amt) / len(amt)) if amt else 0,
            "big_n": sum(a >= BIG for a in amt), "big_rev": sum(a for a in amt if a >= BIG),
            "high_sales": len(high), "high_rev": sum(a for _, a, _ in high),
            "high_buy_lines": lines, "high_buy_units": units, "high_held_cost_end": sum(c for _, c in held),
            "high_held_n_end": len(held), "high_turn_h": round(statistics.median(turn), 2) if turn else None}


def stockouts(db, t1, n=30):
    """SKUs buyers named that I had no unit of, last 24 h: substitute offers (shop_subst) + 'not in stock' answers
    of the substitute experiment (logs/subst_exp.jsonl no_offer)."""
    t0 = t1 - 86400
    c = Counter(x for (x,) in db.execute("SELECT x FROM shop_subst WHERE ts>=? AND ts<?", (t0, t1)))
    try:
        with open(os.path.join(HERE, "logs", "subst_exp.jsonl"), encoding="utf-8") as f:
            for ln in f:
                e = json.loads(ln)
                if e.get("event") == "no_offer" and t0 <= e["ts"] < t1:
                    c[e.get("x")] += 1
    except OSError:
        pass
    cat = json.load(open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8"))
    try:
        import httpx
        b = httpx.get("https://market.ourolabs.ai/api/board", params={"round": 3}, timeout=30).json()["shops"]
        top = [s["name"] for s in sorted((s for s in b if s.get("all") is not None), key=lambda s: -s["all"])[:10]]
    except Exception:
        top = []
    q = ",".join("?" * len(top)) or "''"
    out = []
    for sku, k in c.most_common(n):
        it = cat.get(sku) or {}
        mine = db.execute("SELECT COUNT(*) FROM settles WHERE shop=? AND sku=? AND ts>=? AND ts<?", (ME, sku, t0, t1)).fetchone()[0]
        theirs = db.execute(f"SELECT COUNT(*) FROM settles WHERE sku=? AND ts>=? AND ts<? AND shop IN ({q})",
                            (sku, t0, t1, *top)).fetchone()[0]
        out.append((sku, k, it.get("list_price"), (it.get("category") or "").split(">")[-1], mine, theirs,
                    (it.get("name") or "")[:18]))
    return out


def report(t, now=None):
    from cap20_again import causes
    t1 = min(t + H * 3600, now or time.time())
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        a, b = window(db, t, t1), window(db, t - (t1 - t), t)
        so = stockouts(db, t1)
    finally:
        db.close()
    ca, cb = causes(t, t1), causes(t - (t1 - t), t)
    pct = lambda x, y: f"{x / y:.0%}" if y else "-"
    lines = [
        f"객단가 1+2 {(t1 - t) / 3600:.1f}시간 점검 (high_max 60, HIGH_MAX 1.5M; {datetime.fromtimestamp(t):%H:%M} ~ "
        f"{datetime.fromtimestamp(t1):%H:%M}, 후 vs 전 같은 길이)",
        "주의: 13:45부터 hot 매입 대상 30 -> 60, 요청 기반 매입(run당 최대 3줄)이 같이 돌았음. 아래 [고가 전용]은 원가 10만 초과 "
        "단위만이라 이 두 변경(저가 위주)의 영향이 적고, 그 아래 전체 지표(객단가, 50만 이상 비중, 막힌 매입)는 섞여 있음",
        "[고가 전용 - 이번 변경의 직접 효과]",
        f"  고가 매입 {a['high_buy_lines']}줄 {a['high_buy_units']}개 vs {b['high_buy_lines']}줄 {b['high_buy_units']}개",
        f"  고가 판매 {a['high_sales']}개 vs {b['high_sales']}개",
        f"  고가 매출 {a['high_rev']:,} vs {b['high_rev']:,}",
        "[전체 - 다른 변경과 섞임]",
        f"- 50만 이상 판매: 건수 {a['big_n']}/{a['sales']} ({pct(a['big_n'], a['sales'])}) vs {b['big_n']}/{b['sales']} "
        f"({pct(b['big_n'], b['sales'])}), 매출 비중 {pct(a['big_rev'], a['revenue'])} vs {pct(b['big_rev'], b['revenue'])}",
        f"- 객단가 {a['avg']:,} vs {b['avg']:,}, 매출 {a['revenue']:,} vs {b['revenue']:,}",
        f"- 고가 재고 원가(끝 시점) {a['high_held_cost_end']:,} ({a['high_held_n_end']}개) vs {b['high_held_cost_end']:,} "
        f"({b['high_held_n_end']}개); 고가 입고->판매 중앙값 {a['high_turn_h']} vs {b['high_turn_h']}시간",
        "- 막힌 매입 원인별 (후 vs 전): " + ", ".join(f"{k} {ca.get(k, 0)} vs {cb.get(k, 0)}" for k in
                                            sorted(set(ca) | set(cb), key=lambda k: -(ca.get(k, 0) + cb.get(k, 0)))),
        "재고 없어서 못 판 SKU 상위 30 (최근 24시간; 요청 / 정가 / 우리 판매 / 상위 10개 가게 판매):"]
    lines += [f" {i}. {sku} {nm} [{cat}] {k} / {lp:,} / {mine} / {theirs}" if lp else f" {i}. {sku} {k} / - / {mine} / {theirs}"
              for i, (sku, k, lp, cat, mine, theirs, nm) in enumerate(so, 1)]
    return "\n".join(lines)


def main():
    if "--report-dry" in sys.argv:
        print(report(time.time() - 3600))
        return
    if "--report-at" in sys.argv:                 # 11/08 ~13:50: the waiting report restarted with the new layout
        t = float(sys.argv[sys.argv.index("--report-at") + 1])
        while time.time() < t + H * 3600 + 60:
            time.sleep(60)
        alerts.send("daily", f"aov_report:{int(t)}", report(t), log=log)
        log("report sent")
        return
    import policy
    from depts import analyst, analyst_apply
    from market_db import connect
    now = time.time()
    old_hm = policy.get("purchasing", "high_max")
    v = policy.write({"purchasing": {"high_max": 60}}, WHY + ": high_max 30 -> 60", by="person")
    db = connect()
    try:
        row = db.execute("SELECT high_share FROM policy ORDER BY ts DESC LIMIT 1").fetchone()
        db.execute("INSERT INTO policy VALUES (?,?,?,?)", (time.time(), row[0] if row else 0.6, 60,
                                                           "11/08 person's decision ~13:30: high-price units 30 -> 60"))
        db.commit()
    finally:
        db.close()
    decisions.record("purchasing", "high_max", "purchasing.high_max", [old_hm, 60], WHY + f"; policy v{v.get('version')}")
    log(f"high_max {old_hm} -> 60 (policy v{v.get('version')}, QC table)")
    d = analyst.db()
    try:
        analyst_apply._cols(d)
        cur = d.execute("INSERT INTO proposals (ts,kind,key,value,reason,evidence,status,expires,decided_ts,result,asked,"
                        "checked) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)", (now, "limit", "limits.HIGH_MAX", json.dumps(1_500_000),
                                                                      WHY, "person", "approved", now, now, "applying", 1))
        pid = cur.lastrowid
        d.commit()
    finally:
        d.close()
    try:
        res = analyst_apply.apply_limit("limits.HIGH_MAX", 1_500_000, pid, why="person's decision 11/08 ~13:30")
        analyst_apply._store(pid, res)
        d = analyst.db()
        d.execute("UPDATE proposals SET result=? WHERE id=?", (res["text"], pid))
        d.commit()
        d.close()
        decisions.record("analyst", "approved_by_person", "limits.HIGH_MAX", 1_500_000, f"#{pid}: {WHY} | {res['text']}")
        hm_text = res["text"]
    except Exception as e:
        d = analyst.db()
        d.execute("UPDATE proposals SET status='failed', result=? WHERE id=?", (str(e)[:300], pid))
        d.commit()
        d.close()
        hm_text = f"HIGH_MAX 1.5M 실패 ({type(e).__name__}: {str(e)[:120]}), 1.0M 그대로"
        alerts.send("urgent", "aov_hm_fail", hm_text, log=log)
    log(f"HIGH_MAX: {hm_text}")
    t = time.time()
    alerts.send("info", "aov_on", f"객단가 1+2 적용 {datetime.now():%H:%M}: high_max 30 -> 60 (policy v{v.get('version')}), "
                f"HIGH_MAX: {hm_text[:140]}. 고가 가드, STOCK_CAP 20M 브레이크, 고가 비중 0.6 그대로. 3시간 점검 예정", log=log)
    while time.time() < t + H * 3600 + 60:
        time.sleep(60)
    alerts.send("daily", f"aov_report:{int(t)}", report(t), log=log)
    log("report sent")


if __name__ == "__main__":
    main()
