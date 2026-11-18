"""LLM calls a day, seller and buyer, 24 h after vs 24 h before a change (person's request 11/08 ~11:10: buyer fixed to
plain | min offers 1, seller code haggling 100% below the high-price line). One Telegram at AT + 24 h. Counting as in
tools/morning_summary.llm_calls (kit console for the seller, market-kit.log(.1) for the buyer); negotiations from
market.db for calls per negotiation.
    python tools/llm_day_compare.py <AT epoch> [--dry]
"""
import os
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)


def counts(t0, t1):
    from morning_summary import llm_calls
    c = llm_calls(t0, t1)
    db = sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    try:
        sn = db.execute("SELECT COUNT(*) FROM shop_deals WHERE ts>=? AND ts<?", (t0, t1)).fetchone()[0]
        bn = db.execute("SELECT COUNT(*) FROM buyer_deals WHERE ts>=? AND ts<? AND outcome!='skip'", (t0, t1)).fetchone()[0]
    finally:
        db.close()
    return c["seller"], c["buyer"], sn, bn


def text(at, t1):
    h = (t1 - at) / 3600
    a, b = counts(at, t1), counts(at - (t1 - at), at)
    per = lambda calls, n: f"{calls / n:.2f}" if n else "-"
    scale = 24 / h if h else 0
    return "\n".join([
        f"LLM 호출 변화 (변경 {datetime.fromtimestamp(at):%m-%d %H:%M}, 후 {h:.1f}시간 vs 전 같은 길이, 하루 환산)",
        f"- seller {round(b[0] * scale):,} -> {round(a[0] * scale):,}/일 (협상당 {per(b[0], b[2])} -> {per(a[0], a[2])}, 협상 {b[2]} -> {a[2]})",
        f"- buyer {round(b[1] * scale):,} -> {round(a[1] * scale):,}/일 (협상당 {per(b[1], b[3])} -> {per(a[1], a[3])}, 협상 {b[3]} -> {a[3]})",
        "변경: buyer plain|mo1 고정, seller 고가 외 100% 코드 흥정 (고가는 30% 실험 계속)"])


def main():
    at = float(sys.argv[1])
    if "--dry" in sys.argv:
        print(text(at, time.time()))
        return
    while time.time() < at + 86400:
        time.sleep(60)
    import alerts
    alerts.send("daily", f"llm_day_compare:{int(at)}", text(at, at + 86400))


if __name__ == "__main__":
    main()
