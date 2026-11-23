"""Supply window fit (person's decision 11/11 ~14:50): every planned line reaches the market before the window closes.

Why lines were refused: a restock window is open 180 s (closes_at = opens_at + 180). The kit sends one supply per
plan line, ~11 s apart (measured median), at most 10 lines per run, and runs restock.py again only after the last
line. The first plan of a window (10 lines) took ~110 s, so the second plan started ~140 s in and only 3-4 of its
lines got in; the rest got 429 "supply is closed" after the kit's 3 tries (30 s each), 102 lines / 236 units in the
6 h to 11/11 14:45.

The fix, in restock.py (the kit is not ours to change):
  1. early start: a run up to EARLY_S before the window opens plans at once and waits until the window opens, then
     hands over the plan, so the first line goes in at the opening second (it went in 0-60 s late)
  2. fit: a plan gets only as many lines as can reach the market before closes_at (SEC_PER_LINE each, MARGIN_S
     before the close); the rest are dropped from this run (the lowest ranked: the plan is in rank order) and are
     planned again in the next window, not sent to be refused
  3. (11/11 ~15:20, person) the second plan is made with the first (one plan of up to 2 x MAX_LINES lines, so no SKU
     twice and one budget / high-price room) and saved; the kit's next restock.py run (right after the first plan's
     last line) hands it over at once, before the refresh and the ticks (~20 s), fitted to the close as above
"""
import json
import math
import os
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECOND = os.path.join(HERE, "logs", "window_plan2.json")

SEC_PER_LINE = 12          # measured 11 s median between two supplies of one run, plus a little (the fallback)
RULES = os.path.join(HERE, "supply_window.json")   # 11/11 ~18:25 (person): sec_per_line 11 with a guard
KIT_LOGS = [os.path.join(HERE, "logs", "market-kit.log.1"), os.path.join(HERE, "logs", "market-kit.log")]
GUARD_EVERY_S = 60
FIRST_LINE_S = 3           # from printing the plan to the kit's first supply call
MARGIN_S = 5               # the last line arrives at least this long before the close
EARLY_S = 150              # the kit's buyer timeout is 300 s; planning takes ~20-30 s


def soon(r, now=None):
    """True when this run should plan: the window is open, or opens within EARLY_S."""
    now = now or time.time()
    return bool(r.get("open")) or (r.get("opens_at") or now) - now <= EARLY_S


def wait_open(r, log=print, now=None, sleep=time.sleep):
    """Before the window opens: wait until it does (plus 1 s). Returns the seconds waited."""
    now = now or time.time()
    if r.get("open") or not r.get("opens_at") or r["opens_at"] <= now:
        return 0
    w = min(r["opens_at"] + 1 - now, EARLY_S + 30)
    log(f"[window] plan ready {w:.0f} s before the window opens: waiting so the first line goes in at the opening")
    sleep(w)
    return w


def n_fit(r, now=None):
    """Plan lines that can reach the market before closes_at (None: no close time known)."""
    now = now or time.time()
    close = r.get("closes_at")
    if not close:
        return None
    start = max(now, r.get("opens_at") or now) + FIRST_LINE_S
    left = close - MARGIN_S - start
    return 0 if left < 0 else int(math.floor(left / sec_per_line())) + 1


def rules(path=None):
    try:
        with open(path or RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def sec_per_line(path=None):
    """Seconds a plan line is expected to take: supply_window.json sec_per_line (11/11 ~18:25: 11), else 12."""
    v = rules(path).get("sec_per_line")
    return v if isinstance(v, (int, float)) and 5 <= v <= 30 else SEC_PER_LINE


def closed_since(t0, now=None, paths=None):
    """Kit supply lines refused as "supply is closed" in [t0, now] (the kit logs, HH:MM:SS stamps, days rebuilt
    backwards from today)."""
    import re
    from datetime import datetime
    now = now or time.time()
    ts = re.compile(r"(\d\d):(\d\d):(\d\d) ")
    raw = []
    for p in paths or KIT_LOGS:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                raw += f.readlines()
        except OSError:
            pass
    base = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    n, day, last = 0, 0, None
    for x in reversed(raw):
        m = ts.match(x)
        if not m:
            continue
        sec = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        if last is not None and sec > last + 3600:
            day -= 1
        last = sec
        t = base + day * 86400 + sec
        if t < t0:
            break
        if t <= now and "[supply]" in x and "supply is closed" in x:
            n += 1
    return n


def guard(now=None, log=print, path=None, paths=None, send=None):
    """11/11 ~18:25 (person): while sec_per_line is below the fallback, more than max_closed lines refused as "supply is
    closed" in the last guard_h hours (counted from the change) -> back to the fallback (12) and a Telegram.
    Checked at most every GUARD_EVERY_S. Returns True when it reverted."""
    path = path or RULES
    now = now or time.time()
    r = rules(path)
    fb = r.get("fallback", SEC_PER_LINE)
    if not r.get("since") or r.get("reverted") or not r.get("sec_per_line") or r["sec_per_line"] >= fb:
        return False
    if now - r.get("checked", 0) < GUARD_EVERY_S:
        return False
    t0 = max(r["since"], now - r.get("guard_h", 3) * 3600)
    n = closed_since(t0, now, paths)
    r["checked"], r["closed_now"] = round(now), n
    hit = n > r.get("max_closed", 3)
    if hit:
        r.update(reverted=round(now), reverted_from=r["sec_per_line"], sec_per_line=fb,
                 revert_reason=f"{n} lines refused as supply is closed since {time.strftime('%H:%M', time.localtime(t0))}")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    if hit:
        msg = (f"공급 창 줄당 {r['reverted_from']}초 -> {fb}초 자동 복귀: {time.strftime('%H:%M', time.localtime(t0))} 이후 "
               f"마감 거절 {n}줄 (기준 {r.get('max_closed', 3)}줄 초과)")
        log(f"[window] {msg}")
        try:
            if send is None:
                import alerts
                send = alerts.send
            send("urgent", "window_line_revert", msg)
        except Exception:
            pass
        try:
            import decisions
            decisions.record("purchasing", "window_line_revert", "supply window", fb, msg)
        except Exception:
            pass
    return hit


def save_second(plan2, r, log=print, path=None):
    """11/11 ~15:20 (person): the window's 2nd plan, made together with the 1st (one plan of 2 x MAX_LINES split in
    two: no SKU twice, one budget, one high-price room), kept until the kit runs restock.py again."""
    path = path or SECOND
    if not plan2:
        if os.path.exists(path):
            os.remove(path)
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"opens_at": r.get("opens_at"), "closes_at": r.get("closes_at"), "ts": round(time.time()),
                   "plan": plan2}, f)
    log(f"[window] second plan ready ({len(plan2)} lines): it goes out right after the first plan's last line")


def take_second(state, log=print, now=None, path=None):
    """The saved 2nd plan for the current window, fitted to the close, or None (none saved / another window: a normal
    run). Used once: the file is removed."""
    path = path or SECOND
    if not os.path.exists(path):
        return None
    now = now or time.time()
    with open(path, encoding="utf-8") as f:
        s = json.load(f)
    os.remove(path)
    with open(os.path.join(state, "inventory.json"), encoding="utf-8") as f:
        r = json.load(f).get("restock") or {}
    close = s.get("closes_at")
    if not close or now >= close or r.get("closes_at") not in (None, close):
        log("[window] saved second plan is for a window already closed: normal run")
        return None
    import limits
    if limits.buy_paused(now=now):
        log("[window] second plan dropped: cash brake pause")
        return []
    plan, dropped = fit(s["plan"], {"open": True, "opens_at": s.get("opens_at"), "closes_at": close}, now, log=log)
    log(f"[window] second plan out at once: {len(plan)} of {len(s['plan'])} lines "
        f"({close - now:.0f} s to the close)")
    try:
        import decisions
        decisions.record("purchasing", "window_second", "supply window", len(plan),
                         f"2nd plan: {len(plan)} lines sent, {len(dropped)} would arrive after the close "
                         + ", ".join(f"{p['sku']} x{p['qty']}" for p in plan))
    except Exception:
        pass
    return plan


def fit(plan, r, now=None, log=print):
    """(the lines that fit, the dropped lines)."""
    n = n_fit(r, now)
    if n is None or len(plan) <= n:
        return plan, []
    kept, dropped = plan[:n], plan[n:]
    log(f"[window] {len(plan)} lines, {n} reach the market before the close ({sec_per_line()} s a line): "
        f"{len(dropped)} dropped to the next window ({', '.join(str(p['sku']) + ' x' + str(p['qty']) for p in dropped)})")
    return kept, dropped
