"""22:00 safety net for the high-price settings (person's decision 11/07 ~18:20):

  at DEADLINE, no high-price buy guard on (guard_rules.json "enabled") -> QC policy table high_share 0.4 / high_max 18,
      buy_expand.json hold_paused (qc.split_floor: no 0.6/28 floor while paused), decision + Telegram
  later, the guard on                                                 -> 0.6 / 28 again, hold resumed, Telegram
  the guard already on at DEADLINE                                    -> nothing
    python tools/guard_watch.py          (waits; log in logs/guard_watch.log)
"""
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
import alerts  # noqa: E402
import decisions  # noqa: E402

DEADLINE = datetime(2026, 11, 7, 22, 0).timestamp()
LOG = os.path.join(HERE, "logs", "guard_watch.log")
EXPAND = os.path.join(HERE, "buy_expand.json")


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def guard_on():
    try:
        return bool(json.load(open(os.path.join(HERE, "guard_rules.json"), encoding="utf-8")).get("enabled"))
    except (OSError, ValueError):
        return False


def set_split(share, cap, paused, why):
    from market_db import connect
    db = connect()
    try:
        db.execute("INSERT INTO policy VALUES (?,?,?,?)", (time.time(), share, cap, why))
        db.commit()
    finally:
        db.close()
    j = json.load(open(EXPAND, encoding="utf-8"))
    j["hold_paused"] = paused
    json.dump(j, open(EXPAND, "w"))
    decisions.record("purchasing", "guard_watch", "high_share/high_max", {"high_share": share, "high_max": cap,
                                                                          "hold_paused": paused}, why)


def main():
    log("waiting for 22:00")
    while time.time() < DEADLINE:
        time.sleep(30)
    if guard_on():
        log("22:00: the buy guard is on, 0.6 / 28 stays")
        return
    set_split(0.4, 18, True, "person's decision 11/07 ~18:20: no high-price buy guard by 22:00 -> 0.4 / 18 until it is on")
    alerts.send("urgent", "guard_watch_down", "22:00 고가 매입 가드 미적용: 고가 비중 0.6 -> 0.4, 고가 보유 28 -> 18로 임시 하향 "
                "(가드 적용되면 자동 복귀)", log=log)
    log("22:00: lowered to 0.4 / 18")
    while not guard_on():
        time.sleep(60)
    set_split(0.6, 28, False, "the high-price buy guard is on -> 0.6 / 28 again, 24 h hold resumed")
    alerts.send("info", "guard_watch_up", "고가 매입 가드 적용됨: 고가 비중 0.6, 고가 보유 28로 복귀", log=log)
    log("guard on: back to 0.6 / 28")


if __name__ == "__main__":
    main()
