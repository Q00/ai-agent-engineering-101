"""Watcher for the wider-buying trial B + C (person 11/12 ~14:20, depts/wide_trial.py). Every CHECK_S (5 min):
the guard (12 h+ unsold high-price stock +10 units or +5,000,000 over the value when applied -> both off, one urgent
Telegram) and, once, the 3 h report. Runs until the report is sent and the trial is off or reported for 24 h.
    python tools/wide_trial_watch.py [--once | --dry]
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
from depts import wide_trial as W  # noqa: E402

CHECK_S = 300
END = __import__("datetime").datetime(2026, 11, 17, 23, 59).timestamp()


def step(now=None, send=None, path=None, units=None, log=print, **kw):
    """One check: (guard result, report sent)."""
    g = W.guard(units=units, now=now, send=send, path=path, log=log)
    sent = W.report_due(now=now, send=send, path=path, log=log, **kw)
    try:                                           # 11/13 ~00:00: TWO's 3 h check
        sent = W.report_two_due(now=now, send=send, path=path, log=log) or sent
    except Exception as e:
        log(f"[wide trial] TWO report failed: {type(e).__name__}: {e}")
    return g, sent


def main():
    if "--dry" in sys.argv:
        print(W.report())
        return
    while True:
        try:
            g, sent = step()
            print(time.strftime("%H:%M:%S"), g, "report sent" if sent else "", flush=True)
        except Exception as e:
            print(time.strftime("%H:%M:%S"), f"failed: {type(e).__name__}: {e}", flush=True)
        r = W.rules()
        # 11/13 ~00:00: with the cost guard (max_cost) it runs until the guard trips or the round ends
        done = (r.get("off_ts") and (r.get("two_reported") or not r.get("two_since"))) or (
            not r.get("max_cost") and r.get("reported") and time.time() > r["since"] + 86400)
        if "--once" in sys.argv or done or time.time() > END:
            return
        time.sleep(CHECK_S)


if __name__ == "__main__":
    main()
