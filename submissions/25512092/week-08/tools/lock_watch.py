"""Hourly watch of negotiation locks (person's decision 11/09 ~11:30, with depts/open_expiry.py): every hour, the
negotiations closed as expired in the hour (logs/expired.jsonl) and the share of held units (.market/inventory.json,
expired grade out) that sit in an open negotiation (logs/seller_open.json, written by the seller). One line in
logs/lock_watch.log and a decision lock_watch; share over ALERT_SHARE (20%) -> urgent Telegram. Starts measuring once
the seller writes its state (the 14:00 restart), runs to the round end.
    python tools/lock_watch.py [--once]
"""
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
from depts import open_expiry  # noqa: E402
from limits import ROUND_END  # noqa: E402

LOG = os.path.join(HERE, "logs", "lock_watch.log")
ALERT_SHARE = 0.20
EVERY_S = 3600


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def expired_count(t0, t1):
    n = 0
    try:
        with open(open_expiry.EXPIRED, encoding="utf-8") as f:
            for ln in f:
                if ln.strip() and t0 <= json.loads(ln)["ts"] < t1:
                    n += 1
    except OSError:
        pass
    return n


def measure(now=None):
    now = now or time.time()
    with open(open_expiry.STATE, encoding="utf-8") as f:
        st = json.load(f)
    with open(os.path.join(HERE, ".market", "inventory.json"), encoding="utf-8") as f:
        held = {u["unit"]: u.get("cost") or 0 for u in json.load(f).get("units", []) if u.get("grade") != "expired"}
    locked = [u for u in set(st.get("units") or []) if u in held]
    return {"ts": round(now), "state_age_s": round(now - st["ts"]), "open": st.get("open"), "held": len(held),
            "locked": len(locked), "locked_cost": sum(held[u] for u in locked),
            "share": round(len(locked) / len(held), 4) if held else 0.0,
            "expired_1h": expired_count(now - 3600, now)}


def tick():
    import alerts
    import decisions
    m = measure()
    log(json.dumps(m))
    decisions.record("cs", "lock_watch", "open negotiations", m,
                     f"locked {m['locked']}/{m['held']} ({m['share']:.1%}), expired in 1 h {m['expired_1h']}")
    if m["share"] > ALERT_SHARE:
        alerts.send("urgent", f"lock_watch:{m['ts'] // 3600}",
                    f"협상에 잡힌 재고 {m['share']:.1%} > {ALERT_SHARE:.0%}: {m['locked']}/{m['held']}개, 원가 "
                    f"{m['locked_cost']:,}; 열린 협상 {m['open']}, 최근 1시간 expired {m['expired_1h']}건"
                    f" (seller 상태 {m['state_age_s']}초 전)", log=log)
    return m


def main():
    if "--once" in sys.argv:
        print(measure())
        return
    log("waiting for the seller state (14:00 restart)")
    t_start = time.time()
    while not (os.path.exists(open_expiry.STATE) and os.path.getmtime(open_expiry.STATE) > t_start):
        time.sleep(30)
    log("seller state seen")
    nxt = time.time() + EVERY_S
    while time.time() < ROUND_END:
        time.sleep(max(5, nxt - time.time()))
        nxt += EVERY_S
        try:
            tick()
        except Exception as e:
            log(f"tick failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
