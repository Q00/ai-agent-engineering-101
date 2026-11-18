"""3 h check of the 11/11 ~14:50 changes (person's decision): supply window fit (early start + lines that reach the
market before the close), deep stocking at most 5, high-price margin filter 8%. One Telegram (daily) H hours after
the start, after vs the same length before:
  supply window refusals ("supply is closed") and the lines dropped to the next window
  high-price candidates (high lines planned per run), units bought, units sold
  deep-stock SKUs' turnover (deep_high.window_stats)
  high-price lines in the 8-10% margin band (logs/high_margin_band.jsonl): units bought, sold, profit
  12 h+ unsold stock (high / normal), Book and revenue per hour
    python tools/check_window.py [--start [--t0=EPOCH] | --dry]
"""
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
STATE = os.path.join(HERE, "logs", "check_window.json")
KIT = [os.path.join(HERE, "logs", "market-kit.log.1"), os.path.join(HERE, "logs", "market-kit.log")]
BAND = os.path.join(HERE, "logs", "high_margin_band.jsonl")
H = 3
TS = re.compile(r"(\d\d):(\d\d):(\d\d) ")


def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def start(now=None, t0=None):
    import morning_summary as ms
    now = now or time.time()
    s = {"t0": round(t0 or now), "stock": ms.stock_line(now)}
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)
    return s


def kit_lines(now=None):
    """(epoch, line) for the kit logs (HH:MM:SS stamps; days rebuilt backwards from today, a midnight wrap = -1 day)."""
    now = now or time.time()
    raw = []
    for p in KIT:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                raw += f.readlines()
        except OSError:
            pass
    base = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    out, day, last = [], 0, None
    for x in reversed(raw):
        m = TS.match(x)
        if not m:
            continue
        s = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        if last is not None and s > last + 3600:
            day -= 1
        last = s
        out.append((base + day * 86400 + s, x))
    out.reverse()
    return out


def kit_stats(lines, t0, t1):
    L = [x for t, x in lines if t0 <= t < t1]
    closed = [x for x in L if "[supply]" in x and "supply is closed" in x]
    return {"closed": len(closed), "closed_units": sum(int(m[1]) for m in (re.search(r"'qty': (\d+)", x) for x in closed) if m),
            "ok_lines": sum(1 for x in L if "[supply]" in x and "refused" not in x),
            "dropped": sum(int(m[1]) for m in (re.search(r"\[window\] \d+ lines, \d+ reach .*?: (\d+) dropped", x) for x in L) if m),
            "runs": sum(1 for x in L if "[restock] budget " in x),
            "high_lines": sum(1 for x in L if re.search(r"\[high\] .*-> buy \d+|\[rank\] \S+ \(high", x))}


def high_db(t0, t1, band_skus=None, db_path=None):
    """High-price units (cost > HIGH_MIN) supplied in [t0, t1) and those of them sold (by now), profit; band_skus: only
    the units of those SKUs supplied after the SKU's band row."""
    from depts.qc import HIGH_MIN
    d = sqlite3.connect(f"file:{db_path or os.path.join(HERE, 'market.db')}?mode=ro", uri=True, timeout=60)
    try:
        rows = d.execute("SELECT m.unit, m.sku, m.cost, m.supplied_at, s.amount FROM my_stock m LEFT JOIN settles s ON "
                         "s.unit=m.unit AND s.shop='choo-chu' WHERE m.cost>? AND m.supplied_at>=? AND m.supplied_at<?",
                         (HIGH_MIN, t0, t1)).fetchall()
    finally:
        d.close()
    if band_skus is not None:
        rows = [r for r in rows if r[1] in band_skus and r[3] >= band_skus[r[1]]]
    sold = [r for r in rows if r[4] is not None]
    return {"n": len(rows), "cost": int(sum(r[2] for r in rows)), "sold": len(sold),
            "profit": int(sum(r[4] - r[2] for r in sold))}


def band(t0, t1):
    out = {}
    try:
        with open(BAND, encoding="utf-8") as f:
            for x in f:
                try:
                    r = json.loads(x)
                except ValueError:
                    continue
                if t0 <= r["ts"] < t1:
                    out[r["sku"]] = min(out.get(r["sku"], r["ts"]), r["ts"])
    except OSError:
        pass
    return out


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
    tp = t0 - h * 3600
    lines = kit_lines(now)
    a, p = kit_stats(lines, t0, now), kit_stats(lines, tp, t0)
    ha, hp = high_db(t0, now), high_db(tp, t0)
    L = [f"11/11 공급 창·고가 마진 8%·깊게 쌓기 5 {h:.1f}시간 점검: {datetime.fromtimestamp(t0):%m-%d %H:%M} 적용, 후 vs 직전 같은 길이"]
    L.append(f"- 공급 창 마감 거절 {a['closed']}줄 {a['closed_units']}개 vs {p['closed']}줄 {p['closed_units']}개; 마감 전에 "
             f"못 들어가 다음 창으로 미룬 {a['dropped']}줄; 들어간 매입 {a['ok_lines']}줄 vs {p['ok_lines']}줄")
    L.append(f"- 고가 후보(계획된 고가 줄) {a['high_lines']}줄/{a['runs']}run vs {p['high_lines']}줄/{p['runs']}run; 고가 매입 "
             f"{ha['n']}개 {ha['cost']:,} vs {hp['n']}개 {hp['cost']:,}; 그중 판매 {ha['sold']}개 이익 {ha['profit']:,} vs "
             f"{hp['sold']}개 {hp['profit']:,}")
    da = deep_high.window_stats(deep_high.skus_between(t0, now), t0, now)
    dp = deep_high.window_stats(deep_high.skus_between(tp, t0), tp, t0)
    L.append(f"- 깊게 쌓기 대상(최대 5) SKU {da['skus']} vs {dp['skus']}개: 매입 {da['buy_n']}개 vs {dp['buy_n']}개; 판매 "
             f"{da['sell_n']}개 이익 {da['profit']:,} vs {dp['sell_n']}개 {dp['profit']:,}; 입고→판매 중앙값 "
             f"{fmt(da['median_h'], 2)} vs {fmt(dp['median_h'], 2)}시간; 품절 {da['out_h']:.1f} vs {dp['out_h']:.1f}시간")
    bs_ = band(t0, now)
    b = high_db(t0, now, bs_)
    L.append(f"- 마진 8~10% 구간 고가: 계획 SKU {len(bs_)}개, 매입 {b['n']}개 원가 {b['cost']:,}, 판매 {b['sold']}개 이익 "
             f"{b['profit']:,}")
    L.append(f"- 적용 때 {s['stock']}")
    L.append(f"- 지금 {ms.stock_line(now)}")
    if cur is None:
        cur = BR.now(board, clock=clock, db_path=db_path)
    pp, p0 = BR.at(tp, db_path=db_path), BR.at(t0, db_path=db_path)
    bad = BR.failed(**{"직전 시작": pp, "적용 때": p0, "지금": cur})
    ph = lambda x, y, k: BR.show(BR.per_h(x, y, "s", k), ",.0f")
    L.append(f"- 가게 시간당 Book {ph(p0, cur, 'book')} vs {ph(pp, p0, 'book')}, 시간당 매출 "
             f"{ph(p0, cur, 'revenue')} vs {ph(pp, p0, 'revenue')}, 순위 {BR.show(p0.get('s', 'rank'))} -> "
             f"{BR.show(cur.get('s', 'rank'))}")
    if bad:
        L.append(BR.fail_line(bad))
    return "\n".join(L), bad


def send_with_retry(s, send, now=None, sleep=time.sleep, db_path=None, board=None, clock=time.time):
    """The report through board_read.send_with_retry (again with each next snapshot while a reading failed)."""
    import board_read as BR
    now = now or time.time()
    return BR.send_with_retry(
        lambda cur: report(s, now=now if cur is None else cur.ts, cur=cur, board=board, db_path=db_path, clock=clock),
        send, f"check_window:{s['t0']}", sleep=sleep, db_path=db_path, now_ts=now)


def main():
    if "--start" in sys.argv:
        t0 = next((float(x.split("=", 1)[1]) for x in sys.argv if x.startswith("--t0=")), None)
        print(start(t0=t0))
        return
    s = load() or start()
    if "--dry" in sys.argv:
        print(report(s)[0])
        return
    while time.time() < s["t0"] + H * 3600:
        time.sleep(min(60, max(1, s["t0"] + H * 3600 - time.time())))
    import alerts
    try:
        send_with_retry(s, alerts.send)
    except Exception as e:
        alerts.send("urgent", "check_window_fail", f"11/11 공급 창 3시간 점검 실패: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
