"""3 h check of the 11/11 ~10:30 changes (person's decision): absolute saved guard with switching on again (all four
buyer features on), deep stock at most 3, high pins to 11/12 12:00, auto mode ended. One Telegram (daily) H hours
after the start, after vs the same length before:
  buyer  deals per hour, saved, ALL rank and saved rank, each feature on or off
  shop   12 h+ unsold stock (high / normal, cost and count) at the start vs now, deep-stock SKUs' buys / sales /
         turnover (median supply -> sale) / stock-out hours, Book and revenue per hour
    python tools/check_1111.py [--start | --dry]     (--start: record the start now; --dry: print the report now)
"""
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
STATE = os.path.join(HERE, "logs", "check_1111.json")
H = 3


def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def start(now=None):
    import morning_summary as ms
    now = now or time.time()
    s = {"t0": round(now), "stock": ms.stock_line(now)}
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)
    return s


def features():
    from depts import accept_rule, high_accept, second_shop, soft_neg
    names = (("비싼 주문 완화", high_accept), ("D", second_shop), ("soft_neg", soft_neg), ("수락 규칙", accept_rule))
    return ", ".join(f"{n} {'켜짐' if m.on() else '꺼짐'}" for n, m in names)


def fmt(v, d=0):
    return "-" if v is None else f"{v:,.{d}f}"


def report(s, now=None, cur=None, board=None, db_path=None, clock=time.time):
    """(text, the board readings that failed); board readings via tools/board_read.py (cur: the Point for "now")."""
    import board_read as BR
    import morning_summary as ms
    from depts import deep_high
    now = now or time.time()
    t0 = s["t0"]
    h = max((now - t0) / 3600, 0.01)
    if cur is None:
        cur = BR.now(board, clock=clock, db_path=db_path)
    pp, p0 = BR.at(t0 - h * 3600, db_path=db_path), BR.at(t0, db_path=db_path)
    bad = BR.failed(**{"직전 시작": pp, "적용 때": p0, "지금": cur})
    g = lambda q, k: BR.show(q.get("b", k))
    ph = lambda x, y, side, k, spec: BR.show(BR.per_h(x, y, side, k), spec)
    L = [f"11/11 변경 {h:.1f}시간 점검: {datetime.fromtimestamp(t0):%m-%d %H:%M} 적용, 후 vs 직전 같은 길이"]
    L.append(f"- buyer 시간당 deals {ph(p0, cur, 'b', 'deals', ',.1f')} vs {ph(pp, p0, 'b', 'deals', ',.1f')}; saved "
             f"{g(p0, 'saved')} -> {g(cur, 'saved')} (saved {g(p0, 'saved_rank')}위 -> {g(cur, 'saved_rank')}위), "
             f"ALL {g(p0, 'all')} ({g(p0, 'rank')}위) -> {g(cur, 'all')} ({g(cur, 'rank')}위)")
    L.append(f"- 장치: {features()} (끄는/켜는 saved: 비싼 주문 0.395/0.400, D 0.390/0.395, soft_neg·수락 0.383/0.388; 4위 > 0.375면 4위 + 0.018/0.013/0.008)")
    L.append(f"- 적용 때 {s['stock']}")
    L.append(f"- 지금 {ms.stock_line(now)}")
    a = deep_high.window_stats(deep_high.skus_between(t0, now), t0, now)
    p = deep_high.window_stats(deep_high.skus_between(t0 - h * 3600, t0), t0 - h * 3600, t0)
    L.append(f"- 깊게 쌓기 대상(최대 3개) SKU {a['skus']} vs {p['skus']}개: 매입 {a['buy_n']}개 {a['buy_cost']:,} vs {p['buy_n']}개 "
             f"{p['buy_cost']:,}; 판매 {a['sell_n']}개 이익 {a['profit']:,} vs {p['sell_n']}개 {p['profit']:,}; 입고→판매 중앙값 "
             f"{fmt(a['median_h'], 2)} vs {fmt(p['median_h'], 2)}시간; 품절 {a['out_h']:.1f} vs {p['out_h']:.1f}시간")
    L.append(f"- 가게 시간당 Book {ph(p0, cur, 's', 'book', ',.0f')} vs {ph(pp, p0, 's', 'book', ',.0f')}, 시간당 매출 "
             f"{ph(p0, cur, 's', 'revenue', ',.0f')} vs {ph(pp, p0, 's', 'revenue', ',.0f')}, 순위 "
             f"{BR.show(p0.get('s', 'rank'))} -> {BR.show(cur.get('s', 'rank'))}")
    if bad:
        L.append(BR.fail_line(bad))
    return "\n".join(L), bad


def main():
    if "--start" in sys.argv:
        print(start())
        return
    s = load() or start()
    if "--dry" in sys.argv:
        print(report(s)[0])
        return
    while time.time() < s["t0"] + H * 3600:
        time.sleep(min(60, max(1, s["t0"] + H * 3600 - time.time())))
    import alerts
    try:
        import board_read as BR
        BR.send_with_retry(lambda cur: report(s, now=None if cur is None else cur.ts, cur=cur), alerts.send,
                           f"check_1111:{s['t0']}")
    except Exception as e:
        alerts.send("urgent", "check_1111_fail", f"11/11 변경 3시간 점검 실패: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
