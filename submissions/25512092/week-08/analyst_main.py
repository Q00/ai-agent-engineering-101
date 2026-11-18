"""Strategy analyst process (separate from the kit: no seller or keepalive restart is ever needed).

    python analyst_main.py            (started by start_market.cmd / Task Scheduler "ChooMarket")
    python analyst_main.py --test     send one test proposal (no effect when approved) and keep running

Loop: Telegram getUpdates (25 s long poll) for the approve / reject / later buttons, then housekeeping
(expire unanswered proposals, re-ask "later" ones). Steps 2-4 add data collection, the hourly analysis and the
appliers. Step 2: analyst_brain.tick() collects every 15 min and analyses every
60 min from 11/07 20:00 (record only until 11/08 07:00). Log: logs/analyst.log.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
from depts import analyst, analyst_apply, analyst_brain  # noqa: E402

analyst_apply.register()                              # step 3: approvals apply, 되돌리기 undoes
from depts import callout  # noqa: E402
from depts import cap_brake  # noqa: E402
from depts import receipt_hold  # noqa: E402
callout.register()                                    # 11/07: grade-claim callout drafts, 승인 -> buyer post


def main():
    analyst.log("analyst started")
    import threading
    from depts import buyer_poster                      # 11/07 ~17:10: buyer forum posts, one per 5-min slot
    threading.Thread(target=buyer_poster.loop, kwargs={"log": analyst.log}, daemon=True).start()
    if "--test" in sys.argv:
        analyst.send_proposal("test", "test.no_effect", "ok", "시험 제안입니다. 아무 버튼이나 눌러 보세요 (승인해도 바뀌는 것 없음).")
    while True:
        try:
            analyst.poll(timeout=25)
            analyst.housekeeping()
            analyst_brain.tick()                   # collect 15 min / analyse 60 min, from 11/07 20:00
            analyst_apply.check_approvals()        # step 4: 3 h before/after each approval, CEO revert follow-up
            callout.tick()                         # grade-claim callout drafts (one per shop per 24 h)
            cap_brake.tick(log=analyst.log)        # 11/07 ~20:40: STOCK_CAP above 15M -> brake checks every 5 min
            receipt_hold.refresh(log=analyst.log)  # 11/08: the receipt hold (p99, 3-15 min), once a day
            receipt_hold.conflict_tick(log=analyst.log)   # 11/08: late_pay_conflict, every 5 min
            if time.localtime().tm_hour == 0 and time.localtime().tm_min >= 15:   # 11/07: yesterday's forum flow
                import alerts
                from depts import forum_watch
                forum_watch.flow(log=analyst.log, send=alerts.send)          # once a day (forum_meta flow:<day>)
        except Exception as e:
            analyst.log(f"loop error: {type(e).__name__}")
            time.sleep(30)


if __name__ == "__main__":
    main()
