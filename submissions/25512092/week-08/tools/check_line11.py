"""3 h check of the supply window at 11 s a line (person's decision 11/11 ~18:25; the close - 5 s rule unchanged; the
guard in depts/supply_window.guard goes back to 12 s at > 3 lines refused as closed in 3 h). One Telegram (daily) H hours
after the start, after vs the same length before:
  lines refused as "supply is closed" (lines, units), lines dropped to the next window, lines in per window
  (supplies grouped into windows: a gap of GAP_S or more starts a new one), and whether the guard went back to 12 s
    python tools/check_line11.py [--start [--t0=EPOCH] | --dry]
"""
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
STATE = os.path.join(HERE, "logs", "check_line11.json")
H = 3
GAP_S = 120            # supplies of one window are ~11 s apart; windows are minutes apart


def load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def start(t0=None):
    s = {"t0": round(t0 or time.time())}
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f)
    return s


def windows(lines, t0, t1):
    """Lines in per window: [count, ...] for the accepted supplies in [t0, t1)."""
    ts = [t for t, x in lines if t0 <= t < t1 and "[supply]" in x and "refused" not in x]
    out = []
    for i, t in enumerate(ts):
        if i == 0 or t - ts[i - 1] >= GAP_S:
            out.append(0)
        out[-1] += 1
    return out


def stats(lines, t0, t1):
    import check_window as cw
    k = cw.kit_stats(lines, t0, t1)
    w = windows(lines, t0, t1)
    return dict(k, windows=len(w), per_window=(sum(w) / len(w)) if w else None, max_window=max(w) if w else None)


def report(s, now=None, lines=None):
    import check_window as cw
    from depts import supply_window as W
    now = now or time.time()
    t0 = s["t0"]
    h = max((now - t0) / 3600, 0.01)
    lines = cw.kit_lines(now) if lines is None else lines
    a, p = stats(lines, t0, now), stats(lines, t0 - h * 3600, t0)
    f1 = lambda v: "-" if v is None else f"{v:.1f}"
    r = W.rules()
    g = (f"{datetime.fromtimestamp(r['reverted']):%H:%M} 12초로 자동 복귀 ({r.get('revert_reason')})" if r.get("reverted")
         else f"복귀 없음, 지금 {W.sec_per_line()}초 (최근 3시간 마감 거절 {r.get('closed_now', '-')}줄)")
    L = [f"공급 창 줄당 11초 {h:.1f}시간 점검: {datetime.fromtimestamp(t0):%m-%d %H:%M} 적용, 후 vs 직전 같은 길이 (12초)",
         f"- 마감 거절 {a['closed']}줄 {a['closed_units']}개 vs {p['closed']}줄 {p['closed_units']}개",
         f"- 다음 창으로 미룬 줄 {a['dropped']} vs {p['dropped']}",
         f"- 창당 들어간 줄 평균 {f1(a['per_window'])} vs {f1(p['per_window'])} (최대 {a['max_window'] or '-'} vs "
         f"{p['max_window'] or '-'}, 창 {a['windows']} vs {p['windows']}개), 들어간 매입 {a['ok_lines']} vs {p['ok_lines']}줄",
         f"- 가드: {g}"]
    return "\n".join(L)


def main():
    if "--start" in sys.argv:
        t0 = next((float(x.split("=", 1)[1]) for x in sys.argv if x.startswith("--t0=")), None)
        print(start(t0))
        return
    s = load() or start()
    if "--dry" in sys.argv:
        print(report(s))
        return
    while time.time() < s["t0"] + H * 3600:
        time.sleep(min(60, max(1, s["t0"] + H * 3600 - time.time())))
    import alerts
    try:
        alerts.send("daily", f"check_line11:{s['t0']}", report(s))
    except Exception as e:
        alerts.send("urgent", "check_line11_fail", f"공급 창 11초 3시간 점검 실패: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
