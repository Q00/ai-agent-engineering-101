"""Bundle 2, part C (person's decision 11/07 ~02:00). Runs once, detached; at 11/07 10:00:

  the volume-pricing guard (alerts.volume_guard, 6 h after the 01:47 switch) passed and the rules are still on
    -> restock_c.json on (no restart; restock reads it every run):
         13 breadth: SKUs I do not hold first, 1-2 units, in the low-share busy categories
         15 surge >= 1.05 last; the first plan of each restock window spends all cash above the reserve
       14 CASH_RESERVE 1.5M -> 1.0M through depts/analyst_apply.apply_limit (b0 before/after check, commit, push)
       b0 run with the flag on, decisions.jsonl lines, Telegram
  otherwise (reverted, failed, or not checked) -> C held, urgent Telegram, nothing changed
Log: logs/bundle2.log
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
import alerts  # noqa: E402
import decisions  # noqa: E402

LOG = os.path.join(HERE, "logs", "bundle2.log")
AT = datetime(2026, 11, 7, 10, 0).timestamp()
NAMED = ["침대/매트리스", "침구", "김치", "소파/의자/스툴"]
R = "person's decision 11/07 ~02:00 (bundle 2 C)"


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def breadth_cats(max_n=8):
    """The named categories plus busy leaves (>= 200 market sales in 24 h) where my share is under half my share of
    the whole market."""
    cat = json.load(open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8"))
    leaf = lambda k: ((cat.get(k) or {}).get("category") or "").split(">")[-1]
    db = sqlite3.connect(f"file:{os.path.join(HERE, 'market.db')}?mode=ro", uri=True)
    rows = db.execute("SELECT sku, shop FROM settles WHERE ts>=?", (time.time() - 86400,)).fetchall()
    db.close()
    mk, me = {}, {}
    for sku, shop in rows:
        mk[leaf(sku)] = mk.get(leaf(sku), 0) + 1
        if shop == "choo-chu":
            me[leaf(sku)] = me.get(leaf(sku), 0) + 1
    overall = sum(me.values()) / max(1, len(rows))
    low = sorted((c for c, n in mk.items() if c and n >= 200 and me.get(c, 0) / n < overall / 2),
                 key=lambda c: -mk[c])
    out = list(NAMED) + [c for c in low if c not in NAMED]
    return out[:max_n], {c: (mk.get(c, 0), me.get(c, 0)) for c in out[:max_n]}, overall


def main():
    log("bundle2 waiting for 10:00")
    while time.time() < AT:
        time.sleep(min(60, AT - time.time()))
    from depts import volume
    r = volume.rules()
    ok = r.get("enabled") and r.get("checked_at") and r.get("check_bad") is False
    if not ok:
        why = ("price rules reverted by the 6 h guard" if r.get("reverted_at") else
               "6 h guard has not run" if not r.get("checked_at") else "volume rules off")
        decisions.record("purchasing", "bundle2_held", "restock_c", None, f"{R}: held, {why}")
        alerts.send("urgent", "bundle2_held", f"C(restock 13~15) 보류: {why}. 아무것도 바꾸지 않음", log)
        log(f"held: {why}")
        return
    cats, counts, overall = breadth_cats()
    rules = {"enabled": True, "since": round(time.time()), "breadth_cats": cats, "surge_min": 1.05,
             "max_first_run": True,
             "first_run_share": 0.7, "breadth_max_lines": 5}
    with open(os.path.join(HERE, "restock_c.json"), "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False)
    decisions.record("purchasing", "breadth_on", "restock_c.breadth_cats", cats,
                     f"{R}: SKUs I do not hold first, 1-2 units, at most 2 held per SKU; market/mine 24 h {counts}, "
                     f"my overall share {overall:.1%}")
    decisions.record("purchasing", "surge_last_on", "restock_c.surge_min", 1.05,
                     f"{R}: surge >= 1.05 last; the first plan of each window spends all cash above the reserve")
    log(f"restock_c on: {cats}")
    try:
        from depts import analyst_apply
        res = analyst_apply.apply_limit("limits.CASH_RESERVE", 1_000_000, "b2", why="person's decision bundle 2 (14)")
        decisions.record("purchasing", "cash_reserve", "limits.CASH_RESERVE", 1_000_000, f"{R}: {res['text']}")
        cash_text = res["text"]
    except Exception as e:
        cash_text = f"CASH_RESERVE 변경 실패: {type(e).__name__}: {str(e)[:150]}"
        decisions.record("purchasing", "cash_reserve_failed", "limits.CASH_RESERVE", None, cash_text)
    try:
        head = analyst_apply.git_commit([os.path.join(HERE, "restock_c.json")],
                                        "week-08 bundle 2 C on (person's decision 11/07): breadth buying, surge last, "
                                        "full first plan per window (restock_c.json)")
    except Exception as e:
        head = f"commit failed: {type(e).__name__}"
    alerts.send("info", "bundle2_on", "C(restock) 적용 10:00: 박리다매 6시간 점검 통과\n"
                f"13 품목 폭: {', '.join(cats)} (안 가진 SKU 우선, SKU당 최대 2개)\n"
                f"14 {cash_text}\n15 surge 1.05 이상 후순위, 창 첫 계획은 예비금 위 현금 전부\n커밋 {head}", log)
    log("done")


if __name__ == "__main__":
    main()
