"""Week 08 round-start notice: one Telegram message once Round 3 has started and the code has run for a while.

    python round_start_notice.py --dir .            # waits for round 3, then reports after WAIT seconds, then exits

It checks .market/inventory.json every 30 s; when the round is 3, it waits WAIT seconds and sends what the code did
in that time: restock runs and units bought, negotiations and deals, buyer orders and deals, Round 3 settles on the
ledger (ours and the market's), and which processes are up. Status only, no internal numbers.
"""
import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import model
import notify
import policy
from manager import jsonl

os.environ["TZ"] = "Asia/Seoul"             # the server runs on UTC
time.tzset()

WAIT = 600
PROCS = ("market_kit.py", "seller.py", "collector.py", "manager.py", "forum.py", "buyer_manager.py", "buyer_forum.py")
# matched after a space or a slash: "forum.py" must not match buyer_forum.py, nor "manager.py" buyer_manager.py


def round_now(md):
    try:
        return json.loads((md / "inventory.json").read_text(encoding="utf-8")).get("round", 0)
    except (OSError, ValueError):
        return 0


def report(md, t0):
    restocks = jsonl(policy.DATA / "restock.jsonl", t0)
    negs = jsonl(model.NEGOTIATIONS, t0)
    orders = jsonl(policy.DATA / "buyer.jsonl", t0)
    try:
        db = model.connect()
        settles = [json.loads(r[0]) for r in db.execute("SELECT data FROM ledger WHERE round >= 3 AND kind='settle'")]
        mine = model.our_urls(db)
    except Exception:
        settles, mine = [], set()
    up = [p for p in PROCS if subprocess.run(["pgrep", "-u", str(os.getuid()), "-f", f"[ /]{p}"],
                                             capture_output=True).returncode == 0]
    down = [p for p in PROCS if p not in up]
    minutes = round((time.time() - t0) / 60)
    lines = [f"[Round 3 시작] {time.strftime('%m-%d %H:%M', time.localtime(t0))}에 시작을 감지했고, {minutes}분 동안 돌린 상태입니다.",
             f"- 가게 입고: {len(restocks)}회, {sum(b.get('qty', 0) for r in restocks for b in r.get('buy', []))}개 구매",
             f"- 가게 협상: {sum(r.get('outcome') == 'open' for r in negs)}건 시작, "
             f"{sum(r.get('outcome') == 'deal' for r in negs)}건 체결",
             f"- buyer 주문: {len(orders)}건, 체결 {sum(r.get('end') == 'deal' for r in orders)}건",
             f"- 장부 Round 3 정산: 시장 {len(settles)}건, 우리 가게 {sum(1 for d in settles if d.get('shop') in mine)}건, "
             f"우리 buyer {sum(1 for d in settles if d.get('buyer') == model.SHOP_NAME)}건",
             "- 프로세스: " + ("모두 정상" if not down else f"멈춤 {', '.join(down)}")]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".")
    a = ap.parse_args()
    md = Path(a.dir) / ".market"
    while round_now(md) < 3:
        time.sleep(30)
    t0 = time.time()
    print(f"round 3 seen at {time.strftime('%H:%M:%S')}; reporting in {WAIT} s", flush=True)
    time.sleep(WAIT)
    text = report(md, t0)
    print(text, flush=True)
    notify.send(text)


if __name__ == "__main__":
    main()
