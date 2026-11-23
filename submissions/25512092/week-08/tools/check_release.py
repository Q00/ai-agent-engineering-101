"""3 h check after releasing the one-case shops from the permanent block (person's decision 11/11 ~21:20; only the
24 h rules of depts/shop_avoid apply to them; bright-living stays blocked; the buyer checks the offered SKU before any
accept). One Telegram (daily) H hours after the release, after vs the same length before:
  the released shops: tries and deals (market.db buyer_deals), item / amount mismatches (memory.jsonl), wrong-item
      refusals before an accept (buyer_deals reason 'offered ...'), per shop for the ones tried
  all orders: deals per hour (board) and saved (tools/board_read.py: live board / snapshot, "읽기 실패" and again with
      the next snapshot)
    python tools/check_release.py [--dry]
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
H = 3


def released(path=None):
    r = json.load(open(path or os.path.join(HERE, "shop_avoid.json"), encoding="utf-8"))
    rel = r.get("released") or {}
    return rel, min(rel.values()) if rel else None


def tries(names, t0, t1, db_path=None):
    d = sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=60)
    try:
        rows = d.execute(f"SELECT shop, outcome, reason FROM buyer_deals WHERE ts>=? AND ts<? AND shop IN "
                         f"({','.join('?' * len(names))})", (t0, t1, *names)).fetchall()
    finally:
        d.close()
    per = {}
    for shop, outcome, reason in rows:
        p = per.setdefault(shop, {"tries": 0, "deals": 0, "wrong_item": 0})
        p["tries"] += 1
        p["deals"] += outcome == "deal" and reason == "ok"
        p["wrong_item"] += (reason or "").startswith("offered ")
    return per


def mismatches(names, t0, t1, path=None):
    out = {"item_mismatch": 0, "receipt_mismatch": 0}
    try:
        with open(path or os.path.join(HERE, "memory.jsonl"), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 8_000_000))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for ln in lines:
        if "mismatch" not in ln:
            continue
        try:
            m = json.loads(ln)
            t = datetime.strptime(m["ts"], "%Y-%m-%d %H:%M:%S").timestamp()
        except (ValueError, KeyError):
            continue
        if t0 <= t < t1 and m.get("shop") in names and m.get("flag") in out:
            out[m["flag"]] += 1
    return out


def report(now=None, cur=None, board=None, db_path=None, clock=time.time, rules_path=None, mem_path=None):
    import board_read as BR
    now = now or time.time()
    rel, t0 = released(rules_path)
    names = sorted(rel)
    h = max((now - t0) / 3600, 0.01)
    a, p = tries(names, t0, now, db_path), tries(names, t0 - h * 3600, t0, db_path)
    ma, mp = mismatches(names, t0, now, mem_path), mismatches(names, t0 - h * 3600, t0, mem_path)
    tot = lambda x, k: sum(v[k] for v in x.values())
    L = [f"영구 차단 해제 {h:.1f}시간 점검: {datetime.fromtimestamp(t0):%m-%d %H:%M} 해제 ({len(names)}곳), 후 vs 직전 같은 길이"]
    L.append(f"- 해제한 가게 시도 {tot(a, 'tries')}건 성사 {tot(a, 'deals')}건 vs {tot(p, 'tries')}건 {tot(p, 'deals')}건; "
             f"품목 불일치 {ma['item_mismatch']} vs {mp['item_mismatch']}, 금액 불일치 {ma['receipt_mismatch']} vs "
             f"{mp['receipt_mismatch']}, 수락 전 품목 확인으로 거절 {tot(a, 'wrong_item')} vs {tot(p, 'wrong_item')}")
    if a:
        L.append("  가게별 (시도/성사/품목 확인 거절): " + ", ".join(
            f"{s} {v['tries']}/{v['deals']}/{v['wrong_item']}" for s, v in sorted(a.items(), key=lambda x: -x[1]["tries"])))
    if cur is None:
        cur = BR.now(board, clock=clock, db_path=db_path)
    pp, q0 = BR.at(t0 - h * 3600, db_path=db_path), BR.at(t0, db_path=db_path)
    bad = BR.failed(**{"직전 시작": pp, "해제 때": q0, "지금": cur})
    L.append(f"- 전체 시간당 deals {BR.show(BR.per_h(q0, cur, 'b', 'deals'), '.1f')} vs "
             f"{BR.show(BR.per_h(pp, q0, 'b', 'deals'), '.1f')}; saved {BR.show(q0.get('b', 'saved'))} -> "
             f"{BR.show(cur.get('b', 'saved'))} ({BR.show(cur.get('b', 'saved_rank'))}위), ALL {BR.show(q0.get('b', 'all'))} "
             f"-> {BR.show(cur.get('b', 'all'))} ({BR.show(cur.get('b', 'rank'))}위)")
    try:
        from depts import shop_avoid
        L.append("- " + shop_avoid.summary_line(now, t0))
    except Exception:
        pass
    if bad:
        L.append(BR.fail_line(bad))
    return "\n".join(L), bad


def main():
    if "--dry" in sys.argv:
        print(report()[0])
        return
    _, t0 = released()
    while time.time() < t0 + H * 3600:
        time.sleep(min(60, max(1, t0 + H * 3600 - time.time())))
    import alerts
    import board_read as BR
    try:
        BR.send_with_retry(lambda cur: report(now=None if cur is None else cur.ts, cur=cur), alerts.send,
                           f"check_release:{round(t0)}")
    except Exception as e:
        alerts.send("urgent", "check_release_fail", f"영구 차단 해제 3시간 점검 실패: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
