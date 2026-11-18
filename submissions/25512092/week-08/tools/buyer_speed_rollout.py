"""Buyer speed on (person's decision 11/09 ~11:55, depts/buyer_speed.py): after the 11/09 accept-rule report and its
addendum ("[extra] report sent" in logs/accept_watch.log; at the latest 14:45), buyer_speed.json enabled (the buyer
runs per order: the next order uses it). 3 h later one Telegram, 3 h after vs 3 h before: time per order (mean,
median, from the kit log), orders and deals per hour and the close rate (board_hist, my buyer row), saved, and the
slow_shop_skip decisions with whether those shops were among my usual deal shops (buyer_deals, 24 h before).
    python tools/buyer_speed_rollout.py [--report-dry]          (log in logs/buyer_speed_rollout.log)
"""
import json
import os
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

LOG = os.path.join(HERE, "logs", "buyer_speed_rollout.log")
AFTER = os.path.join(HERE, "logs", "accept_watch.log")
LATEST = datetime(2026, 11, 9, 14, 45).timestamp()
H = 3


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def ready():
    try:
        with open(AFTER, encoding="utf-8") as f:
            return any("[extra] report sent" in ln for ln in f)
    except OSError:
        return False


def cycle(t0, t1):
    """Seconds between consecutive kit order lines in [t0, t1) (one order = from its line to the next)."""
    from strict_mcp_rollout import timed_lines
    ts = []
    for name in ("market-kit.log.1", "market-kit.log"):
        p = os.path.join(HERE, "logs", name)
        if os.path.exists(p):
            ts += [t for t, _ in timed_lines(p, "[order o-", t0, t1, tail_bytes=80_000_000)]
    ts = sorted(set(ts))
    d = [b - a for a, b in zip(ts, ts[1:]) if b - a < 600]
    return (round(statistics.mean(d), 1), statistics.median(d), len(ts)) if d else (None, None, len(ts))


def board_at(db, t):
    """My buyer row at t via tools/board_read.py (the common board reading); None = 읽기 실패."""
    import board_read
    return board_read.mine(t)[1]


def window(db, t0, t1):
    a, b = board_at(db, t0), board_at(db, t1)
    h = (t1 - t0) / 3600
    mean, med, n = cycle(t0, t1)
    do, dd = b["orders"] - a["orders"], b["deals"] - a["deals"]
    return {"mean": mean, "median": med, "orders_h": round(do / h, 1), "deals_h": round(dd / h, 1),
            "rate": dd / do if do else None, "saved": (a["saved"], b["saved"]), "kit_orders": n}


def slow(t0, t1):
    out = []
    with open(os.path.join(HERE, "logs", "decisions.jsonl"), encoding="utf-8") as f:
        for ln in f:
            if "slow_shop_skip" in ln:
                d = json.loads(ln)
                if t0 <= d["ts"] < t1 and d.get("action") == "slow_shop_skip":
                    out.append(d)
    return out


def report(t, now=None):
    t1 = min(t + H * 3600, now or time.time())
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        a, b = window(db, t, t1), window(db, t - (t1 - t), t)
        deals = Counter(s for s, in db.execute("SELECT shop FROM buyer_deals WHERE outcome='deal' AND ts>=? AND ts<?",
                                                (t - 86400, t)))
    finally:
        db.close()
    sk = slow(t, t1)
    top = [s for s, _ in deals.most_common(10)]
    by = Counter(d["target"] for d in sk)
    shops = "; ".join(f"{s} {n}건 (전 24시간 우리 성사 {deals.get(s, 0)}건{', 상위 10' if s in top else ''})"
                      for s, n in by.most_common(8)) or "-"
    stage = Counter((d.get("value") or {}).get("stage") for d in sk)
    f = lambda x: f"{x:.1%}" if x is not None else "-"
    return "\n".join([
        f"buyer 속도 개선 {(t1 - t) / 3600:.1f}시간 점검 ({datetime.fromtimestamp(t):%H:%M} ~ {datetime.fromtimestamp(t1):%H:%M}, "
        "후 vs 전 같은 길이): 첫 LLM 호출 생략, 가게 카드 10분 캐시, 첫 응답 10초 제한",
        f"- 주문 1건 처리 시간 평균 {a['mean']} vs {b['mean']}초, 중앙값 {a['median']} vs {b['median']}초",
        f"- 시간당 주문 {a['orders_h']} vs {b['orders_h']}, 시간당 deals {a['deals_h']} vs {b['deals_h']}, "
        f"성사율 {f(a['rate'])} vs {f(b['rate'])}",
        f"- saved {a['saved'][0]} -> {a['saved'][1]} (전 구간 {b['saved'][0]} -> {b['saved'][1]})",
        f"- slow_shop_skip {len(sk)}건 ({dict(stage)}): {shops}"])


def main():
    if "--report-dry" in sys.argv:
        print(report(time.time() - 3600))
        return
    import alerts
    import decisions
    from depts import buyer_speed
    log("waiting for the accept-rule report and its addendum")
    while not ready() and time.time() < LATEST:
        time.sleep(30)
    r = buyer_speed.rules()
    r.update(enabled=True, since=round(time.time()))
    with open(buyer_speed.RULES, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False)
    t = time.time()
    decisions.record("buyer", "buyer_speed_on", "buyer_speed", r, "person 11/09 ~11:55: no thrown-away first LLM call, "
                     "card cache 10 min, 10 s first-reply limit (slow_shop_skip); prices unchanged")
    log(f"on {r}")
    alerts.send("info", "buyer_speed_on", f"buyer 속도 개선 적용 {datetime.now():%H:%M}: 버려지던 첫 LLM 호출 생략(첫 제안가 동일), "
                "가게 카드 주소별 10분 캐시, 첫 응답 10초 넘으면 다음 후보 가게(slow_shop_skip). 가격 판단 그대로. 3시간 점검 예정",
                log=log)
    while time.time() < t + H * 3600 + 60:
        time.sleep(60)
    alerts.send("daily", f"buyer_speed_report:{int(t)}", report(t), log=log)
    log("report sent")


if __name__ == "__main__":
    main()
