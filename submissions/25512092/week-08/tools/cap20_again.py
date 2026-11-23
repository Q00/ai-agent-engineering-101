"""STOCK_CAP 15M -> 20M again (person's decision 11/08 ~11:00) with brake condition 2 as a share: of the units
supplied after the raise that are at least 6 h old, the share of their cost still unsold over 25% (needs 20 such
units). Median 3 h and cash 2M unchanged. 3 h later one Telegram: revenue, margin, buying held back by cause, the
brake numbers (cap20_rollout.window for the money and turnover).
    python tools/cap20_again.py               (log in logs/cap20_rollout.log)
    python tools/cap20_again.py --report-dry  (the report for the last hour, printed)
"""
import json
import os
import re
import sqlite3
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
from cap20_rollout import log, window  # noqa: E402

WHY = "person's decision 11/08 ~11:00: STOCK_CAP 15M -> 20M again, brake condition 2 as a 25% share"
NEW = 20_000_000
H = 3


def causes(t0, t1):
    """Buying held back, per cause, in [t0, t1): runs (restock runs) or lines (one SKU in one run)."""
    c = Counter()
    runs = {}
    with open(os.path.join(HERE, "logs", "decisions.jsonl"), encoding="utf-8") as f:
        for ln in f:
            if '"purchasing"' not in ln:
                continue
            d = json.loads(ln)
            if not (t0 <= d["ts"] < t1) or d.get("dept") != "purchasing":
                continue
            a, r = d.get("action"), d.get("reason") or ""
            if a == "cap":
                c["STOCK_CAP (run 예산 깎임/막힘)"] += 1
            elif a == "no_restock":
                c[{"STOCK_CAP": "STOCK_CAP (run 매입 없음)", "CASH_RESERVE": "CASH_RESERVE (run 매입 없음)"}.get(
                    d.get("limit_hit"), f"{d.get('limit_hit')} (run 매입 없음)")] += 1
            elif a == "buy_guard_skip":
                c["고가 가드 (줄)"] += 1
            elif a == "skip" and r.startswith(("[low]", "[high]")):
                c["부패주의 내 판매량 (줄)" if "rule 'my sales'" in r else "부패주의 시험 (줄)" if "rule 'trial'" in r
                  else "기타 skip (줄)"] += 1
            if a in ("buy", "sofa_trial") and r.startswith(("[low]", "[high]", "[sofa trial]")):
                runs[round(d["ts"])] = runs.get(round(d["ts"]), 0) + 1
    c["10줄 한도 찬 run"] = sum(1 for n in runs.values() if n >= 10)
    from strict_mcp_rollout import timed_lines
    full = 0
    for name in ("market-kit.log.1", "market-kit.log"):
        p = os.path.join(HERE, "logs", name)
        if os.path.exists(p):
            for _, ln in timed_lines(p, "[restock] budget ", t0, t1, tail_bytes=60_000_000):
                m = re.search(r"high-price units (\d+)/(\d+)", ln)
                full += bool(m and int(m[1]) >= int(m[2]))
    c["고가 개수 한도 찬 run"] = full
    return c


def spend_history(t0, t1):
    """purchasing.spend_share changes in [t0, t1) (person 11/08 ~11:15: shown in the 3 h check), and the pin."""
    from depts import policy_notify
    out = []
    with open(os.path.join(HERE, "logs", "decisions.jsonl"), encoding="utf-8") as f:
        for ln in f:
            if "purchasing.spend_share" not in ln:
                continue
            d = json.loads(ln)
            if t0 <= d["ts"] < t1 and d.get("action") in ("policy", "approved", "spend_share_pin", "pinned", "held"):
                out.append(f"{datetime.fromtimestamp(d['ts']):%H:%M} {d['dept']} {d['action']} {d.get('value')}"
                           f" ({(d.get('reason') or '')[:60]})")
    until = policy_notify._pins().get("purchasing.spend_share")
    return out, until


def report(t, now=None):
    t1 = min(t + H * 3600, now or time.time())
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    try:
        a, b = window(db, t, t1), window(db, t - (t1 - t), t)
    finally:
        db.close()
    ca, cb = causes(t, t1), causes(t - (t1 - t), t)
    try:
        st = json.load(open(os.path.join(HERE, "logs", "cap_brake_state.json"), encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    br = json.load(open(os.path.join(HERE, "cap_brake.json"), encoding="utf-8"))
    fired = br.get("fired") and br["fired"] >= t
    share = st.get("old_share")
    sh = spend_history(t - (t1 - t), t1)
    return "\n".join([
        f"STOCK_CAP 20M 재상향 {(t1 - t) / 3600:.1f}시간 점검 ({datetime.fromtimestamp(t):%H:%M} ~ {datetime.fromtimestamp(t1):%H:%M}), "
        "후 vs 전 같은 길이",
        f"- 매출 {a['revenue']:,} vs {b['revenue']:,} / 판매 {a['sales']} vs {b['sales']}",
        f"- 마진 {a['margin']:,} vs {b['margin']:,}",
        "- 막힌 매입 원인별 (후 vs 전): " + ", ".join(f"{k} {ca.get(k, 0)} vs {cb.get(k, 0)}"
                                            for k in sorted(set(ca) | set(cb), key=lambda k: -(ca.get(k, 0) + cb.get(k, 0)))),
        f"- 회전: 입고→판매 중앙값 {a['median_h']} vs {b['median_h']}시간; 현금 "
        + (f"{a['cash'][0]:,} -> {a['cash'][1]:,}, 재고 원가 {a['stock'][0]:,} -> {a['stock'][1]:,}" if a["cash"] else "-"),
        f"- 브레이크: {'걸림 ' + datetime.fromtimestamp(br['fired']).strftime('%H:%M') + ' ' + '; '.join(br.get('fired_why', [])) if fired else '안 걸림'}"
        f"; 6시간+ 미판매 비율 {('%.1f%%' % (100 * share)) if share is not None else '- (상향 후 6시간 지난 입고분 없음)'}"
        f" (기준 25%, {st.get('old_eligible_n', 0)}개 / 원가 {st.get('old_eligible_cost', 0):,} 중 {st.get('old_unsold_cost', 0):,}),"
        f" 중앙값 {st.get('median_h')}시간, 현금 {st.get('cash', 0):,}",
        "- spend_share 이력: " + ("; ".join(sh[0]) or "변경 없음")
        + (f" | 고정(CEO 변경 금지) {datetime.fromtimestamp(float(sh[1])):%m-%d %H:%M}까지" if sh[1] else "")])


def main():
    if "--report-at" in sys.argv:                 # 11/08 ~11:20: the waiting report restarted with the spend_share line
        t = float(sys.argv[sys.argv.index("--report-at") + 1])
        while time.time() < t + H * 3600 + 60:
            time.sleep(60)
        alerts.send("daily", f"cap20_again_report:{int(t)}", report(t), log=log)
        log("again report sent")
        return
    if "--report-dry" in sys.argv:
        print(report(time.time() - 3600))
        return
    from depts import analyst, analyst_apply
    now = time.time()
    d = analyst.db()
    try:
        analyst_apply._cols(d)
        cur = d.execute("INSERT INTO proposals (ts,kind,key,value,reason,evidence,status,expires,decided_ts,result,asked,"
                        "checked) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)", (now, "limit", "limits.STOCK_CAP", json.dumps(NEW),
                                                                      WHY, "person", "approved", now, now, "applying", 1))
        pid = cur.lastrowid
        d.commit()
    finally:
        d.close()
    try:
        res = analyst_apply.apply_limit("limits.STOCK_CAP", NEW, pid, why="person's decision 11/08 ~11:00")
    except Exception as e:
        d = analyst.db()
        d.execute("UPDATE proposals SET status='failed', result=? WHERE id=?", (str(e)[:300], pid))
        d.commit()
        d.close()
        alerts.send("urgent", "cap20_again_fail", f"STOCK_CAP 20M 재상향 실패 ({type(e).__name__}: {str(e)[:150]}). 15M 그대로", log=log)
        log(f"again failed: {e}")
        return
    analyst_apply._store(pid, res)
    d = analyst.db()
    d.execute("UPDATE proposals SET result=? WHERE id=?", (res["text"], pid))
    d.commit()
    d.close()
    t = time.time()
    path = os.path.join(HERE, "cap_brake.json")
    br = json.load(open(path, encoding="utf-8"))
    prev = {k: br.pop(k) for k in ("fired", "fired_why") if k in br}
    br.update(enabled=True, since=round(t), pid=pid, old_share=0.25, min_old_n=20, history=br.get("history", []) + [prev],
              why="person 11/07 ~20:40; 11/08 ~11:00 raised again, condition 2 = share of cost unsold > 25%")
    json.dump(br, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    decisions.record("analyst", "approved_by_person", "limits.STOCK_CAP", NEW, f"#{pid}: {WHY} | {res['text']}")
    log(f"again #{pid}: {res['text']}; brake (share 25%) since {t:.0f}")
    alerts.send("info", "cap20_again_on", f"STOCK_CAP 15M -> 20M 재상향 {datetime.now():%H:%M} (#{pid}, {res['text'][:140]}). "
                "브레이크 조건 2: 상향 후 입고분(6시간 지난 것) 원가 중 미판매 비율 25% 초과 (새벽 07:29 걸린 시점 19.6%). "
                "중앙값 3시간, 현금 2M 그대로. 3시간 점검 보고 예정", log=log)
    while time.time() < t + H * 3600 + 60:
        time.sleep(60)
    alerts.send("daily", f"cap20_again_report:{int(t)}", report(t), log=log)
    log("again report sent")


if __name__ == "__main__":
    main()
