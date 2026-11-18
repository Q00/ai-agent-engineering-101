"""Buying expansion 2 (person's decision 11/07 ~16:05), applied after the 3 h check of STOCK_CAP 12M (analyst #2,
due 17:47; the analyst checks every 5 min):
  #2 checked and still approved -> 1. STOCK_CAP 12M -> 15M through the analyst path (a proposal row, approved; b0
                                     before/after check, STOCK_EXIT 88%, commit + push; its own 3 h check, no 24 h
                                     guard wait)
                                  2. QC policy table: high_share 0.4 -> 0.6, high_max 18 -> 28
                                  3. buy_expand.json since2 (the 20:45 report adds the high-price turnover since now)
  #2 reverted (auto) or failed  -> nothing applied, urgent Telegram
    python tools/expand2_rollout.py          (waits; log in logs/expand2_rollout.log)
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
import alerts  # noqa: E402
import decisions  # noqa: E402

LOG = os.path.join(HERE, "logs", "expand2_rollout.log")
PID_CAP12 = 2
WHY = "person's decision 11/07 ~16:05: buying expansion 2 after the STOCK_CAP 12M check"
GIVE_UP = datetime(2026, 11, 7, 19, 0).timestamp()


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def cap12():
    from depts import analyst
    d = analyst.db()
    try:
        return d.execute("SELECT status, checked FROM proposals WHERE id=?", (PID_CAP12,)).fetchone()
    finally:
        d.close()


def main():
    log("waiting for the 3 h check of analyst #2 (STOCK_CAP 12M, due 17:47)")
    while True:
        st = cap12()
        if st and (st[0] != "approved" or st[1]):
            break
        if time.time() > GIVE_UP:
            alerts.send("urgent", "expand2_wait", "매입 확대 2: 19:00까지 STOCK_CAP 12M 점검 결과가 없어 적용 안 함", log=log)
            log("gave up: no check by 19:00")
            return
        time.sleep(60)
    log(f"#2 now {st}")
    if st[0] != "approved":
        alerts.send("urgent", "expand2_skip", f"매입 확대 2 적용 안 함: STOCK_CAP 12M 점검 결과 {st[0]} (되돌려짐). "
                    "15M / 고가 28개 / 비중 60%는 다시 결정 필요", log=log)
        decisions.record("purchasing", "expand2_skipped", "STOCK_CAP/high_max/high_share", st[0], WHY)
        return

    from depts import analyst, analyst_apply, qc
    from market_db import connect
    now = time.time()
    d = analyst.db()
    try:
        analyst_apply._cols(d)
        cur = d.execute("INSERT INTO proposals (ts,kind,key,value,reason,evidence,status,expires,decided_ts,result,asked,"
                        "checked) VALUES (?,?,?,?,?,?,?,?,?,?,?,0)", (now, "limit", "limits.STOCK_CAP",
                                                                      json.dumps(15_000_000), WHY, "person", "approved",
                                                                      now, now, "applying", 1))
        pid = cur.lastrowid
        d.commit()
    finally:
        d.close()
    try:
        res = analyst_apply.apply_limit("limits.STOCK_CAP", 15_000_000, pid, why="person's decision 11/07 ~16:05")
    except Exception as e:
        d = analyst.db()
        d.execute("UPDATE proposals SET status='failed', result=? WHERE id=?", (str(e)[:300], pid))
        d.commit()
        d.close()
        alerts.send("urgent", "expand2_cap_fail", f"매입 확대 2: STOCK_CAP 15M 적용 실패 ({type(e).__name__}: "
                    f"{str(e)[:120]}). 고가 28개 / 비중 60%도 적용 안 함", log=log)
        log(f"STOCK_CAP 15M failed: {e}")
        return
    analyst_apply._store(pid, res)
    d = analyst.db()
    d.execute("UPDATE proposals SET result=? WHERE id=?", (res["text"], pid))
    d.commit()
    d.close()
    decisions.record("analyst", "approved_by_person", "limits.STOCK_CAP", 15_000_000, f"#{pid}: {WHY} | {res['text']}")
    log(f"STOCK_CAP 15M: {res['text']}")

    db = connect()
    try:
        old = qc.policy(db)
        db.execute("INSERT INTO policy VALUES (?,?,?,?)", (time.time(), 0.6, 28,
                                                           "11/07 person's decision ~16:05: high-price share 0.4 -> 0.6, "
                                                           "units held 18 -> 28 (buying expansion 2)"))
        db.commit()
    finally:
        db.close()
    j = json.load(open(os.path.join(HERE, "buy_expand.json"), encoding="utf-8"))
    j.update(since2=round(time.time()), high_max2=[18, 28], high_share2=[0.4, 0.6], stock_cap2=[12_000_000, 15_000_000])
    json.dump(j, open(os.path.join(HERE, "buy_expand.json"), "w"))
    decisions.record("purchasing", "buy_expand2", "high_share/high_max", {"high_share": [old["high_share"], 0.6],
                                                                          "high_max": [old["high_max"], 28]},
                     WHY + "; 16:00 copy: planned per run 785,541 -> 1,952,969 (first of window 2,769,753)")
    log(f"QC table: high_share {old['high_share']} -> 0.6, high_max {old['high_max']} -> 28; since2 written")
    r = subprocess.run(["git", "commit", "-q", "-m", "week-08 buying expansion 2 applied after the 12M check: high_share 0.6, "
                        "high_max 28 (QC table), since2 for the 20:45 report\n\nCo-Authored-By: Claude Opus 5.5 "
                        "<noreply@anthropic.com>", "--", "buy_expand.json", "logs/decisions.jsonl"],
                       cwd=HERE, capture_output=True, text=True, timeout=120)
    p = subprocess.run(["git", "push", "-q", "origin", "week-08"], cwd=HERE, capture_output=True, text=True, timeout=120)
    log(f"git commit {r.returncode}, push {p.returncode}")
    alerts.send("info", "expand2_on", f"매입 확대 2 적용 {datetime.now():%H:%M}: STOCK_CAP 12M -> 15M ({res['text'][:120]}), "
                "고가 28개, 고가 비중 60%. STOCK_CAP 15M 3시간 점검, 20:45 고가 회전 보고에 새 설정 이후 수치 포함", log=log)
    log("done")


if __name__ == "__main__":
    main()
