"""Stall watch for the unattended night (person's decision 11/11 ~22:00). Every CHECK_S (5 min):
  shop   no sale of mine in the last 30 min (market.db settles), or cash >= 5M and the last 2 supply windows in a row
         with no supply line in (kit log "[supply]" lines inside each window; windows from .market/inventory.json)
  buyer  no order in the last 10 min (kit log "[order o-" lines)
  board  11/12 ~09:30 (person): our shop "alive": false on the board 2 reads in a row -> one urgent Telegram only (no
         restart; the market's own check flaps), and a line when it is back
Any of the first three: one kit restart (tools/watchdog.restart_kit; keepalive starts the kit again) and an urgent Telegram. Still
stalled 15 min after the restart: one more urgent Telegram, nothing else is touched until it clears. Every step goes to
logs/stall_watch.jsonl (the 08:00 summary counts them) and a stall left unsolved to logs/needs.jsonl (the summary's
decisions needed).
    python tools/stall_watch.py [--once]
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
STATE = os.path.join(HERE, "logs", "stall_watch_state.json")
LOG = os.path.join(HERE, "logs", "stall_watch.jsonl")
NEEDS = os.path.join(HERE, "logs", "needs.jsonl")
KIT_LOG = os.path.join(HERE, "logs", "market-kit.log")
INV = os.path.join(HERE, ".market", "inventory.json")
DB = os.path.join(HERE, "market.db")
CHECK_S, SALE_S, ORDER_S, AFTER_S, CASH_MIN, WINDOWS = 300, 1800, 600, 900, 5_000_000, 2
OFF_N = 2
TS = re.compile(r"^(\d\d):(\d\d):(\d\d) ")
ME = "choo-chu"


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _append(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def kit_tail(now, path=None, size=6_000_000):
    """[(epoch, line)] of the kit log's tail (HH:MM:SS stamps; a stamp after now is yesterday)."""
    try:
        with open(path or KIT_LOG, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - size))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    base = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    out = []
    for x in lines:
        m = TS.match(x)
        if m:
            t = base + int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
            out.append((t - 86400 if t > now + 120 else t, x))
    return out


def sales(t0, db_path=None):
    try:
        d = sqlite3.connect(f"file:{db_path or DB}?mode=ro", uri=True, timeout=60)
        try:
            return d.execute("SELECT COUNT(*) FROM settles WHERE shop=? AND ts>=?", (ME, t0)).fetchone()[0]
        finally:
            d.close()
    except sqlite3.Error:
        return None


def window_lines(lines, w):
    return sum(1 for t, x in lines if w["opens_at"] - 5 <= t <= w["closes_at"] + 40 and "[supply]" in x
               and "refused" not in x)


def board_alive(board=None):
    """Our shop's "alive" on the live board (None: read failed / not found)."""
    try:
        if board is None:
            import board_read
            board = board_read.board()
        stack = [board]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                if o.get("name") == ME and "alive" in o and "deals" in o and "orders" not in o:
                    return bool(o["alive"])
                stack.extend(o.values())
            elif isinstance(o, list):
                stack.extend(o)
    except Exception:
        return None
    return None


def check_board(s, now, alive, send, log_path=None):
    """11/12 ~09:30 (person): offline on OFF_N board reads in a row -> one urgent, no restart; back online -> logged.
    A failed read (None) neither counts nor resets."""
    lp = log_path or LOG
    if alive is None:
        return
    if alive:
        if s.get("off_warned"):
            _append(lp, {"ts": round(now), "event": "board_online", "since": s["off_warned"]})
        s.pop("off_n", None)
        s.pop("off_warned", None)
        s.pop("off_since", None)
        return
    s["off_n"] = s.get("off_n", 0) + 1
    s.setdefault("off_since", round(now))
    if s["off_n"] >= OFF_N and not s.get("off_warned"):
        s["off_warned"] = round(now)
        send("urgent", "board_offline", f"보드에 우리 가게 offline {s['off_n']}번 연속 ({datetime.fromtimestamp(s['off_since']):%H:%M}부터, "
                                        f"{CHECK_S // 60}분 간격). kit 재시작은 하지 않음, 확인 필요")
        _append(lp, {"ts": round(now), "event": "board_offline", "why": [f"보드 offline {s['off_n']}번 연속"]})


def check(s, now, lines, inv, n_sales, restart, send, log_path=None, needs_path=None):
    """One round. Returns the stall reasons found now ([] = fine)."""
    lp = log_path or LOG
    # supply windows seen (from the inventory's restock info), judged once closed
    r = (inv or {}).get("restock") or {}
    ws = s.setdefault("windows", [])
    if r.get("opens_at") and r.get("closes_at") and not any(w["opens_at"] == r["opens_at"] for w in ws):
        ws.append({"opens_at": r["opens_at"], "closes_at": r["closes_at"], "cash": (inv or {}).get("balance", 0)})
    for w in ws:
        if "lines" not in w and now > w["closes_at"] + 60:
            w["lines"] = window_lines(lines, w)
    del ws[:-6]
    why = []
    if n_sales is not None and n_sales == 0:
        why.append(f"shop 판매 {SALE_S // 60}분 동안 0건")
    done = [w for w in ws if "lines" in w][-WINDOWS:]
    if len(done) == WINDOWS and all(w["lines"] == 0 and w["cash"] >= CASH_MIN for w in done):
        why.append(f"현금 {done[-1]['cash']:,} (>= {CASH_MIN:,})인데 공급 창 {WINDOWS}번 연속 매입 0줄")
    orders = sum(1 for t, x in lines if t >= now - ORDER_S and "[order o-" in x)
    if lines and orders == 0:
        why.append(f"buyer 주문 {ORDER_S // 60}분 동안 0건")
    inc = s.get("incident")
    if why and not inc:
        ok = restart()
        s["incident"] = {"since": round(now), "why": why, "restarted_at": round(now), "restarted": bool(ok)}
        send("urgent", "stall_restart", f"멈춤 감지: {'; '.join(why)} -> kit 재시작 {'1번 시도' if ok else '실패(kit 프로세스 없음)'}")
        _append(lp, {"ts": round(now), "event": "restart", "why": why, "ok": bool(ok)})
    elif why and inc and not inc.get("second") and now - inc["restarted_at"] >= AFTER_S:
        inc["second"] = round(now)
        send("urgent", "stall_still", f"멈춤 계속: kit 재시작 {AFTER_S // 60}분 뒤에도 {'; '.join(why)}. 더 건드리지 않음, 확인 필요")
        _append(lp, {"ts": round(now), "event": "still", "why": why})
        _append(needs_path or NEEDS, {"ts": round(now), "item": f"멈춤 계속 ({datetime.fromtimestamp(inc['since']):%H:%M}부터): "
                                                              f"{'; '.join(why)}. kit 재시작 1번 뒤에도 그대로"})
    elif not why and inc:
        _append(lp, {"ts": round(now), "event": "recovered", "since": inc["since"], "why": inc["why"]})
        s.pop("incident", None)
    return why


def summary_line(t0, t1, path=None):
    ev = []
    try:
        with open(path or LOG, encoding="utf-8") as f:
            for ln in f:
                e = json.loads(ln)
                if t0 <= e["ts"] < t1:
                    ev.append(e)
    except (OSError, ValueError):
        pass
    if not ev:
        return "멈춤 감지 0번"
    parts = []
    for e in ev:
        t = f"{datetime.fromtimestamp(e['ts']):%H:%M}"
        if e["event"] == "restart":
            parts.append(f"{t} {'; '.join(e['why'])} -> kit 재시작 {'시도' if e['ok'] else '실패'}")
        elif e["event"] == "still":
            parts.append(f"{t} 재시작 15분 뒤에도 멈춤 -> 긴급 알림만")
        elif e["event"] == "recovered":
            parts.append(f"{t} 회복")
        elif e["event"] == "board_offline":
            parts.append(f"{t} 보드 offline 2번 연속 -> 긴급 알림만")
        elif e["event"] == "board_online":
            parts.append(f"{t} 보드 online 복귀")
        else:
            parts.append(f"{t} 감시 오류 {'; '.join(e.get('why') or [])[:80]}")
    return f"멈춤 감지 {sum(1 for e in ev if e['event'] == 'restart')}번: " + "; ".join(parts)


def main():
    import alerts
    import watchdog

    def restart():
        return watchdog.restart_kit(watchdog.procs())
    while True:
        now = time.time()
        s = _load(STATE, {})
        try:
            check(s, now, kit_tail(now), _load(INV, {}), sales(now - SALE_S), restart, alerts.send)
        except Exception as e:
            _append(LOG, {"ts": round(now), "event": "error", "why": [f"{type(e).__name__}: {e}"[:200]]})
        try:
            check_board(s, now, board_alive(), alerts.send)
        except Exception as e:
            _append(LOG, {"ts": round(now), "event": "error", "why": [f"{type(e).__name__}: {e}"[:200]]})
        _save(STATE, s)
        if "--once" in sys.argv:
            return
        time.sleep(CHECK_S)


if __name__ == "__main__":
    main()
