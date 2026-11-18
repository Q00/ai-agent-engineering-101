"""Person's decision 11/12 ~15:20: right after the floor-rule 3 h report (logs/loss_rule_rollout.log "report sent" or a
report failure, due ~17:59; at the latest LATEST), in one go:
  1. db_safe.json on (dbutil: busy 10 s + 3 retries on "database is locked"; refresh commits before its long part and
     calls the board outside the write)
  2. market.db to WAL (readers no longer block writers' commits)
  3. the seller restarted (the kit starts it again) and the analyst restarted (both keep connections for hours)
  4. (person 11/12 ~16:20, with the same restarts) end_rules.json on: restock stops 6 h before the end (11/17 17:59),
     the scheduled end cap and the automatic cap end then too, the endgame markdown 12 h before the end (11/17 11:59);
     the values read back in a fresh process go into the Telegram
  5. (person 11/12 ~16:45) food_need.json on: the seller sells food only with the shelf life the buyer asked for
One Telegram when applied; 1 h later one Telegram: lock errors since (kit log, analyst log), my sales, supplies and
buyer deals in the hour vs the hour before.
    python tools/db_safe_rollout.py [--now] [--report-dry=EPOCH]      (log in logs/db_safe_rollout.log)
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
import alerts  # noqa: E402
import dbutil  # noqa: E402

LOG = os.path.join(HERE, "logs", "db_safe_rollout.log")
LOSS_LOG = os.path.join(HERE, "logs", "loss_rule_rollout.log")
KIT_LOG = os.path.join(HERE, "logs", "market-kit.log")
ANALYST_LOG = os.path.join(HERE, "logs", "analyst.log")
DB = os.path.join(HERE, "market.db")
END_RULES = os.path.join(HERE, "end_rules.json")
FOOD_NEED = os.path.join(HERE, "food_need.json")
FROM = datetime(2026, 11, 12, 17, 59).timestamp()
LATEST = datetime(2026, 11, 12, 18, 30).timestamp()
PY = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".venv", "Scripts", "python.exe"))
ME = "choo-chu"


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def report_out(path=LOSS_LOG, since=FROM):
    """True once the floor-rule report went out (or failed) at or after `since`."""
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                if "report sent" in ln or "loss_rule_report_fail" in ln:
                    t = datetime.strptime(f"2026-{ln[:14]}", "%Y-%m-%d %H:%M:%S").timestamp()
                    if t >= since - 60:
                        return True
    except (OSError, ValueError):
        pass
    return False


def restart_analyst():
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like "
          "'*analyst_main.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
    time.sleep(2)
    # 11/12 ~23:45 (person): launched exactly like the watchdog (new process group, stdin too to DEVNULL); the 18:01
    # launch (DETACHED | CREATE_NO_WINDOW, stdin inherited) exited at once and the watchdog restarted it
    import watchdog
    p = watchdog.start_analyst()
    return r.stdout.split(), p.pid


def apply_end(path=END_RULES, now=None):
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "restock_stop_h": 6, "endgame_h": 12, "since": round(now or time.time()),
                   "why": "person 11/12 ~16:20: sell to the end - restock stop 36 -> 6 h, end cap at the stop, endgame 48 -> "
                          "12 h; food unchanged"}, f)
    os.replace(path + ".tmp", path)


def apply_food(path=FOOD_NEED, now=None):
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "margin_h": 0.5, "since": round(now or time.time()),
                   "why": "person 11/12 ~16:45: food only with the shelf life the buyer asked for (+0.5 h), checked "
                          "again before the receipt"}, f)
    os.replace(path + ".tmp", path)


def end_values(env=None):
    """The end rules as a fresh process sees them (what restock / the seller / the analyst load)."""
    code = ("import sys, json; sys.path.insert(0, r'%s'); from datetime import datetime as D; import limits, pricing; "
            "print(json.dumps({'restock_stop_h': limits.RESTOCK_STOP_H, 'stop_at': D.fromtimestamp(limits.ROUND_END - "
            "limits.RESTOCK_STOP_H * 3600).strftime('%%m-%%d %%H:%%M'), 'cap_end_at': D.fromtimestamp(limits.CAP_END_AT)"
            ".strftime('%%m-%%d %%H:%%M'), 'endgame_h': pricing.ENDGAME_H}))" % HERE)
    r = subprocess.run([PY if os.path.exists(PY) else sys.executable, "-X", "utf8", "-c", code], capture_output=True,
                       text=True, timeout=120, cwd=HERE, env=env)
    return json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else {"error": r.stderr[-300:]}


def apply(rules_path=None, db=DB):
    path = rules_path or dbutil.RULES
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "since": round(time.time()), "busy_s": dbutil.BUSY_S, "waits": list(dbutil.WAITS),
                   "why": "person 11/12 ~15:20: after the 15:05 'database is locked' (record_cash, strategy tick)"}, f)
    os.replace(path + ".tmp", path)
    return dbutil.wal(db, log=log)


def lock_errors(t0, t1, kit=KIT_LOG, analyst=ANALYST_LOG):
    """{program: count} of "database is locked" lines in [t0, t1) (kit lines carry HH:MM:SS of today)."""
    out = {}
    day = datetime.fromtimestamp(t0).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    try:
        with open(kit, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if "database is locked" not in ln:
                    continue
                m = re.match(r"(\d\d):(\d\d):(\d\d) \[(\w+)\]", ln)
                if m and t0 <= day + int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) < t1:
                    out[m[4]] = out.get(m[4], 0) + 1
    except OSError:
        pass
    try:
        with open(analyst, encoding="utf-8", errors="replace") as f:
            for ln in f:
                if "database is locked" in ln:
                    t = datetime.strptime(f"2026-{ln[:14]}", "%Y-%m-%d %H:%M:%S").timestamp()
                    if t0 <= t < t1:
                        out["analyst"] = out.get("analyst", 0) + 1
    except (OSError, ValueError):
        pass
    return out


def numbers(t0, t1, db=DB):
    d = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    try:
        s = d.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM settles WHERE shop=? AND ts>=? AND ts<?",
                      (ME, t0, t1)).fetchone()
        sup = d.execute("SELECT COUNT(*), COALESCE(SUM(cost), 0) FROM my_stock WHERE supplied_at>=? AND supplied_at<?",
                        (t0, t1)).fetchone()
        b = d.execute("SELECT COUNT(*), COALESCE(SUM(outcome='deal'), 0) FROM buyer_deals WHERE ts>=? AND ts<?",
                      (t0, t1)).fetchone()
        mode = d.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        d.close()
    return {"sales": s[0], "rev": int(s[1]), "supplied": sup[0], "sup_cost": int(sup[1]), "orders": b[0],
            "deals": int(b[1]), "mode": mode}


def report(t, now=None, db=DB, kit=KIT_LOG, analyst=ANALYST_LOG):
    now = now or time.time()
    h = max(now - t, 60)
    a, p = numbers(t, now, db), numbers(t - h, t, db)
    le = lock_errors(t, now, kit, analyst)
    L = [f"DB 잠금 대책 적용 {h / 3600:.1f}시간 점검 ({datetime.fromtimestamp(t):%H:%M} 적용, journal {a['mode']})",
         f"- lock 오류: {sum(le.values())}건" + (f" ({', '.join(f'{k} {v}' for k, v in sorted(le.items()))})" if le else ""),
         f"- 판매 {a['sales']}건 매출 {a['rev']:,} vs 직전 같은 길이 {p['sales']}건 {p['rev']:,}",
         f"- 입고 {a['supplied']}개 원가 {a['sup_cost']:,} vs {p['supplied']}개 {p['sup_cost']:,}",
         f"- buyer 주문 {a['orders']}건 중 거래 {a['deals']}건 vs {p['orders']}건 중 {p['deals']}건",
         "(정산은 5분마다 원장에서 들어와 마지막 몇 분은 덜 잡힐 수 있음)"]
    return "\n".join(L)


def main():
    dry = next((float(x.split("=", 1)[1]) for x in sys.argv if x.startswith("--report-dry=")), None)
    if dry:
        print(report(dry))
        return
    log("waiting for the floor-rule report")
    while "--now" not in sys.argv and not report_out() and time.time() < LATEST:
        time.sleep(30)
    late = "--now" not in sys.argv and not report_out()
    t = time.time()
    mode = apply()
    apply_end(now=t)
    apply_food(now=t)
    from seller_conc_rollout import restart_seller, seller_back
    killed = restart_seller()
    old, pid = restart_analyst()
    log(f"applied: db_safe on, journal {mode}, seller restarted (killed {killed}), analyst {old} -> {pid}"
        + (" (no floor report by 18:30)" if late else ""))
    time.sleep(120)
    back = seller_back(t)
    ev = end_values()
    ok = back and mode == "wal" and ev.get("restock_stop_h") == 6
    alerts.send("info" if ok else "urgent", "db_safe_applied",
                f"DB 잠금 대책 적용 {datetime.fromtimestamp(t):%H:%M}: busy 10초 + 재시도 3번, journal {mode}, seller 재시작 "
                + (f"정상 ({back})" if back else "후 확인 안 됨, 점검 필요") + f", 분석관 재시작 (pid {pid})"
                + (" | 하한 규칙 보고가 18:30까지 안 나가 그대로 적용" if late else "")
                + f" | 끝까지 팔기: 매입 중단 {ev.get('stop_at')} (종료 {ev.get('restock_stop_h')}시간 전), 예약 재고 한도 "
                  f"{ev.get('cap_end_at')}, 막바지 할인 종료 {ev.get('endgame_h')}시간 전부터 | 식품 유통기한 확인 "
                  f"{'켬' if json.load(open(FOOD_NEED, encoding='utf-8')).get('enabled') else '안 켜짐'}" + (f" (확인 실패 {ev.get('error')})"
                                                                                  if ev.get("error") else ""), log)
    while time.time() < t + 3600:
        time.sleep(60)
    try:
        alerts.send("daily", f"db_safe_report:{round(t)}", report(t), log)
        log("report sent")
    except Exception as e:
        alerts.send("urgent", "db_safe_report_fail", f"DB 잠금 대책 1시간 점검 실패: {type(e).__name__}: {e}", log)


if __name__ == "__main__":
    main()
