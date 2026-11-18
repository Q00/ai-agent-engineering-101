"""Substitute option A on every substitute offer until the round ends (person's decision 11/09 ~11:00; the 6 h test of
11/08 15:00-21:00 had 20 receipts, 11 paid, no mismatch review): subst_exp.json mode A_exp, share 1.0, until
limits.ROUND_END (the seller reads it per negotiation, no restart). The stop rules of tools/subst_watch.py stay, checked
every CHECK_S on the last WINDOW_H of A deals (stop -> mode B, "not in stock" at once, urgent Telegram):
  paid share of A receipts < 30%; mismatch reviews >= 5 and >= 10% of A results; A stars <= normal stars - 0.3
  (each needs >= 20 A receipts in the window).
    python tools/subst_always.py            (log in logs/subst_always.log)
"""
import json
import os
import sys
import time
from datetime import datetime

import httpx

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
import subst_watch as sw  # noqa: E402
from limits import ROUND_END  # noqa: E402

LOG = os.path.join(HERE, "logs", "subst_always.log")
CHECK_S = 1800
WINDOW_H = 24


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


_warned = {}


def main():
    import alerts
    import decisions
    t0 = time.time()
    top = httpx.get(f"{sw.MARKET}/api/ledger", params={"after": -1}, timeout=60).json()[-1]["seq"]
    L = sw.Ledger(top)
    sw.set_mode(mode="A_exp", share=1.0, since=round(t0), until=round(ROUND_END), always=True)
    decisions.record("sales", "subst_always_on", "subst_exp", "A_exp 1.0",
                     "person 11/09 ~11:00: option A on 100% of substitute offers until the round ends; stop rules kept")
    alerts.send("info", "subst_always_on", f"대체 상품 A안 상시 적용 {datetime.now():%H:%M} (대체 제안 100%, 라운드 끝까지). "
                f"중단 기준 그대로(최근 {WINDOW_H}시간 A 영수증 20건 이상일 때: 결제율 30% 미만, 품목 불일치 리뷰 5건·10% 이상, "
                "별점 일반 대비 -0.3). 30분마다 확인, 걸리면 B(바로 재고 없음)로 전환", log=log)
    log(f"on, until {datetime.fromtimestamp(ROUND_END):%m-%d %H:%M}")
    while time.time() < ROUND_END:
        time.sleep(CHECK_S)
        from depts import subst_exp
        if subst_exp.rules().get("mode") != "A_exp":   # switched by hand: nothing left to watch
            log(f"mode is {subst_exp.rules().get('mode')}: watch ends")
            return
        try:
            L.pull()
        except Exception as e:
            log(f"ledger failed: {type(e).__name__}")
            continue
        m = sw.metrics(L, max(t0, time.time() - WINDOW_H * 3600))
        log(json.dumps(m, ensure_ascii=False))
        why = sw.stop_reason(m)
        if why and alerts.brakes_warn_only():          # 11/10 ~15:00 (person, auto mode): a warning only, A stays
            if time.time() - _warned.get("t", 0) >= 3600:
                _warned["t"] = time.time()
                decisions.record("sales", "subst_exp_stop_warn", "subst_exp", "A", why + " - warning only (auto mode)")
                alerts.send("info", f"subst_always_warn:{int(time.time())}", "대체 상품 A안 중단 조건 경고 (자동 모드: A 유지): "
                            + why, log=log)
            continue
        if why:
            sw.set_mode(mode="B", stopped=round(time.time()), stop_reason=why)
            decisions.record("sales", "subst_exp_stop", "subst_exp", "B", why)
            alerts.send("urgent", "subst_always_stop", "대체 상품 A안(상시) 중단 -> B(바로 재고 없음): " + why + "\n"
                        + sw.text(m, max(t0, time.time() - WINDOW_H * 3600), time.time(), why), log=log)
            log(f"stopped: {why}")
            return
    log("round end")


if __name__ == "__main__":
    main()
