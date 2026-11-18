"""Rollout of seller concurrency (option A, person's decision 11/06). Runs once, detached:

  19:01, 20:01  seller busy share of 18:00 and 19:00 -> Telegram
                either under 70% -> concurrency held ("confirm first"); code accept (98%) still goes on at 22:00
  22:00         seller_concurrency.json {"enabled": <both >= 70%>, "limit": 3, "code_accept": true}; restart the seller only (the kit starts it
                again; the 15-min receipt hold is restored from shop_deals); check it came back -> Telegram
  11/07 09:00   before / after: harness calls per hour, 'timed out' reviews per hour, profit per negotiation -> Telegram
Log: logs/seller_conc_rollout.log
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
import alerts  # noqa: E402

LOG = os.path.join(HERE, "logs", "seller_conc_rollout.log")
FLAG = os.path.join(HERE, "seller_concurrency.json")
APPLIED = os.path.join(HERE, "logs", "seller_concurrency_applied.json")
T = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M").timestamp()


def pc(x):
    return "?" if x is None else f"{x:.0%}"


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def until(ts):
    while time.time() < ts:
        time.sleep(min(60, ts - time.time()))


def util_of(hour_start):
    u = alerts.seller_utilization(hour_start + 3600 + 30)
    return u["busy"] if u else None, u


def restart_seller():
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cmd.exe' -and $_.CommandLine -like '*seller_r3*' }"
          " | ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null; $_.ProcessId }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def seller_back(since):
    """A '[seller] ... held back' line after since in the newest kit console log."""
    import glob
    f = sorted(glob.glob(os.path.join(HERE, "logs", "kit-console-*.txt")), key=os.path.getmtime)[-1]
    with open(f, "rb") as fh:
        fh.seek(max(0, os.path.getsize(f) - 2_000_000))
        tail = fh.read().decode("utf-8", errors="replace").splitlines()
    day0 = time.mktime(time.localtime(since)[:3] + (0, 0, 0, 0, 0, -1))
    for ln in tail:
        if "[seller] [seller]" in ln and "held back" in ln:
            h, m, s = map(int, ln[:8].split(":"))
            if day0 + h * 3600 + m * 60 + s >= since - 5:
                return ln[:120]
    return None


def metrics(t0, t1):
    """Per hour in [t0, t1): harness calls (log), 'timed out' reviews, negotiations, profit per negotiation."""
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    try:
        neg = db.execute("SELECT COUNT(*) FROM shop_deals WHERE ts>=? AND ts<?", (t0, t1)).fetchone()[0]
        profit = db.execute("SELECT COALESCE(SUM(s.amount-m.cost),0), COUNT(*) FROM settles s JOIN my_stock m ON "
                            "m.unit=s.unit WHERE s.shop='choo-chu' AND s.ts>=? AND s.ts<?", (t0, t1)).fetchone()
        tout = db.execute("SELECT COUNT(*) FROM my_results WHERE ts>=? AND ts<? AND review LIKE '%timed out%'",
                          (t0, t1)).fetchone()[0]
    finally:
        db.close()
    hours = (t1 - t0) / 3600
    calls = 0
    h = t0 // 3600 * 3600
    while h + 3600 <= t1 + 1:
        u = alerts.seller_utilization(h + 3600 + 30)
        calls += (u or {}).get("calls", 0)
        h += 3600
    return {"hours": round(hours, 1), "calls_per_h": round(calls / max(1, round(hours)), 1),
            "timed_out_per_h": round(tout / hours, 2), "negotiations_per_h": round(neg / hours, 1),
            "profit_per_negotiation": round(profit[0] / neg) if neg else None, "sales": profit[1]}


def main():
    log("rollout started")
    until(T("2026-11-06 19:01"))
    u18, _ = util_of(T("2026-11-06 18:00"))
    alerts.send("info", "conc_util18", f"seller 사용률 18시 {pc(u18)} (A 적용 조건: 18·19시 모두 70% 이상)", log)
    until(T("2026-11-06 20:01"))
    u19, _ = util_of(T("2026-11-06 19:00"))
    log(f"18h {u18}, 19h {u19}")
    conc = not (u18 is None or u19 is None or u18 < 0.70 or u19 < 0.70)
    if not conc:
        alerts.send("urgent", "conc_hold", f"seller 사용률 18시 {pc(u18)}, 19시 {pc(u19)}: 70% 밑이 있어 A(동시 처리) "
                    "적용 보류 (적용하려면 확인 필요). 22:00 재시작은 코드 수락(98%)만 켜고 진행", log)
        log("concurrency held: under 70%; code accept only")
    else:
        alerts.send("info", "conc_go", f"seller 사용률 18시 {pc(u18)}, 19시 {pc(u19)}: 22:00에 A(동시 처리, 3개) + "
                    "코드 수락(98%) 적용 예정", log)
    until(T("2026-11-06 22:00"))
    with open(FLAG, "w", encoding="utf-8") as f:                 # code accept: person's decision, regardless of 70%
        json.dump({"enabled": conc, "limit": 3, "code_accept": True, "since": round(time.time())}, f)
    t = time.time()
    killed = restart_seller()
    log(f"flag on, seller restarted (killed {killed})")
    time.sleep(120)
    back = seller_back(t)
    json.dump({"ts": t}, open(APPLIED, "w"))
    alerts.send("urgent" if not back else "info", "conc_applied",
                f"{'A + ' if conc else ''}코드 수락 적용 {datetime.fromtimestamp(t):%H:%M}: seller 재시작 " + (f"정상 ({back})" if back else "후 확인 안 됨, 점검 필요"), log)
    report(t, conc)


def report(t, conc):
    """11/07 09:00: before / after the 22:00 restart, plus the sofa rules' results (decision 11/07 01:30)."""
    until(T("2026-11-07 09:00"))
    before, after = metrics(t - 6 * 3600, t), metrics(t, time.time())
    text = (f"{'A(동시 처리) + ' if conc else ''}코드 수락 전후 비교 (시간대가 달라 참고용)\n"
            f"전 {before['hours']}h: 호출 {before['calls_per_h']}/h, timed out {before['timed_out_per_h']}/h, "
            f"협상 {before['negotiations_per_h']}/h, 협상당 이익 {before['profit_per_negotiation']}\n"
            f"후 {after['hours']}h: 호출 {after['calls_per_h']}/h, timed out {after['timed_out_per_h']}/h, "
            f"협상 {after['negotiations_per_h']}/h, 협상당 이익 {after['profit_per_negotiation']}")
    try:
        from depts import sofa
        r = sofa.rules()
        if r.get("since"):
            since, span = r["since"], time.time() - r["since"]
            text += (f"\n\n소파 새 규칙 ({datetime.fromtimestamp(since):%m-%d %H:%M}~, {span / 3600:.0f}h): "
                     + alerts.sofa_text(alerts.sofa_numbers(since))
                     + f"\n소파 이전 같은 길이: " + alerts.sofa_text(alerts.sofa_numbers(since - span, since))
                     + ("" if r.get("warranty") else f"\n보증 해제됨: {r.get('warranty_off_reason')}"))
    except Exception as e:
        text += f"\n소파 결과 계산 실패: {type(e).__name__}"
    try:
        from depts import volume
        r = volume.rules()
        if r.get("since"):
            since, span = r["since"], time.time() - r["since"]
            text += (f"\n\n박리다매 ({datetime.fromtimestamp(since):%m-%d %H:%M}~, {span / 3600:.1f}h"
                     + ("" if r.get("enabled") else ", 원복됨") + ")\n후: " + alerts.shop_text(alerts.shop_numbers(since, time.time()))
                     + "\n전 같은 길이: " + alerts.shop_text(alerts.shop_numbers(since - span, since))
                     + "\n하루 전 같은 시간: " + alerts.shop_text(alerts.shop_numbers(since - 86400, time.time() - 86400)))
    except Exception as e:
        text += f"\n박리다매 결과 계산 실패: {type(e).__name__}"
    try:                                             # 11/07: the 1-star 'grade / quality / broken' bucket, split
        from depts import insights
        cat = json.load(open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8"))
        day0 = T("2026-11-07 00:00")
        db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
        try:
            text += ("\n\n" + insights.grade_detail_text(insights.grade_detail(db, day0 - 86400, day0, cat), "11/06")
                     + "\n" + insights.grade_detail_text(insights.grade_detail(db, day0, time.time(), cat), "11/07 00~09시"))
        finally:
            db.close()
    except Exception as e:
        text += f"\n1점 등급 분석 실패: {type(e).__name__}"
    alerts.send("daily", "conc_report", text, log)
    log("report sent; done")


if __name__ == "__main__":
    if "--report-only" in sys.argv:            # restarted after 22:00 (11/07 01:30): only the 09:00 report is left
        log("rollout restarted: report only")
        report(json.load(open(APPLIED))["ts"], bool(json.load(open(FLAG)).get("enabled")))
    else:
        main()
