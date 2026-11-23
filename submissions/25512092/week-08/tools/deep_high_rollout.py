"""Deep stock for fast-selling high-price SKUs (person's decision 11/10 ~12:30, depts/deep_high.py): one 3 h report,
the 3 h after deep_high.json "since" vs the 3 h before, for the SKUs on the list at the switch-on (logs/deep_high_start.json)
plus every SKU listed since: target SKU buys / sales, stock-out hours, high-price revenue and profit, cash.
    python tools/deep_high_rollout.py [--report-dry]
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
import alerts  # noqa: E402
from depts import deep_high  # noqa: E402

START = os.path.join(HERE, "logs", "deep_high_start.json")
LOG = os.path.join(HERE, "logs", "deep_high_rollout.log")
H = 3


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def report(t, now=None):
    import cap30_rollout as c30
    from aggressive_rollout import shop_window
    from depts.substitute import catalog
    t1 = min(t + H * 3600, now or time.time())
    t0 = t - (t1 - t)
    st = json.load(open(START, encoding="utf-8"))
    skus = set(st["skus"]) | deep_high.skus_between(t, t1)
    a, b = deep_high.window_stats(skus, t, t1), deep_high.window_stats(skus, t0, t)
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=60)
    try:
        cat = catalog()
        ha, hb = shop_window(db, t, t1, cat)["high"], shop_window(db, t0, t, cat)["high"]
    finally:
        db.close()
    held = c30.held()
    import sales_trend
    rl = sales_trend.request_lines_text()           # 11/10 ~14:15: request lines back to 4 inside this window
    pf = lambda x: "-" if x is None else f"{x:.1%}"
    md = lambda x: "-" if x is None else f"{x}시간"
    return "\n".join([
        f"고가 깊게 쌓기 {(t1 - t) / 3600:.1f}시간 점검: {datetime.fromtimestamp(t):%m-%d %H:%M} 적용, 후 vs 직전 같은 길이 "
        f"(대상 {len(skus)}개 SKU, 시작 때 {len(st['skus'])}개)",
        f"- 대상 SKU 매입 {a['buy_n']}개 {a['buy_cost']:,} vs {b['buy_n']}개 {b['buy_cost']:,}; 판매 {a['sell_n']}개 매출 "
        f"{a['rev']:,} 이익 {a['profit']:,} vs {b['sell_n']}개 {b['rev']:,} 이익 {b['profit']:,}; 입고→판매 중앙값 "
        f"{md(a['median_h'])} vs {md(b['median_h'])}",
        f"- 대상 SKU 품절 시간 합계 {a['out_h']}시간 vs {b['out_h']}시간 (SKU {len(skus)}개 x {(t1 - t) / 3600:.1f}시간 중)",
        f"- 고가 전체: 매출 {ha['rev']:,} vs {hb['rev']:,}, 이익률 {pf(ha['margin_rate'])} vs {pf(hb['margin_rate'])}, 매입 "
        f"{ha['buy_n']}개 {ha['buy_cost']:,} vs {hb['buy_n']}개 {hb['buy_cost']:,}",
        f"- 현금 {st['cash']:,} -> {held[4]:,}, 재고 원가 {st['stock']:,} -> {held[0] + held[2]:,}"]
        + ([rl + " (이 점검 구간 안의 변경)"] if rl else []))


def main():
    if "--report-dry" in sys.argv:
        print(report(time.time() - 3600))
        return
    import cap30_rollout as c30
    r = deep_high.rules()
    t = r["since"]
    if not os.path.exists(START):
        held = c30.held()
        lst = deep_high.compute(t, r)
        json.dump({"t": t, "skus": {k: v["limit"] for k, v in lst.items()}, "cash": held[4], "stock": held[0] + held[2]},
                  open(START, "w", encoding="utf-8"), ensure_ascii=False)
        log(f"start: {len(lst)} SKUs")
    while time.time() < t + H * 3600 + 60:
        time.sleep(60)
    alerts.send("daily", f"deep_high_report:{int(t)}", report(t), log=log)
    log("report sent")


if __name__ == "__main__":
    main()
