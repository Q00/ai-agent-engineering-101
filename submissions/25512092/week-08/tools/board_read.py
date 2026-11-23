"""Board readings for every report that compares my shop / buyer board rows over time (person 11/11 ~17:00: one common
function, not one per report).

Why: the 11/11 16:35 soft_neg x1.05 report showed deals(board) / saved / ALL as None. Its "now" came from the nearest
board_hist snapshot within 20 min, and snapshots are written every 30-43 min (market_db.board_tick, on kit ticks), so
the nearest one was 21 min old.

The rules:
  - "now": the live board first (GET /api/board). If that fails, the nearest board_hist snapshot. If that fails too,
    the value is shown as FAIL ("읽기 실패").
  - past points: the nearest board_hist snapshot within TOL_S (45 min). Otherwise FAIL.
  - per-hour rates divide by the real time between the two readings, not by the nominal window.
  - send_with_retry: a report with a failed reading is sent with FAIL shown and "다음 스냅샷 값으로 다시 시도". Then
    it is built again with each next snapshot as "now" (up to RETRIES, waiting at most WAIT_S for each).

Rows have the board_hist shape (the board row plus rank by ALL; the buyer also saved_rank by Saved).
"""
import json
import os
import sqlite3
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(HERE, "market.db")
ME = "choo-chu"
FAIL = "읽기 실패"
TOL_S = 2700          # snapshots come every 30-43 min: the nearest is at most ~22 min off, 45 min leaves room
RETRIES = 3
WAIT_S = 5400


class Point:
    """One reading: ts, shop row, buyer row, src ("보드" | "스냅샷" | None when failed), j (the live board json)."""

    def __init__(self, ts=None, shop=None, buyer=None, src=None, j=None):
        self.ts, self.shop, self.buyer, self.src, self.j = ts, shop, buyer, src, j

    @property
    def ok(self):
        return self.shop is not None or self.buyer is not None

    def get(self, side, k):
        """The value, or None when the reading failed / the key is missing."""
        row = self.shop if side == "s" else self.buyer
        return None if row is None else row.get(k)


def board():
    import httpx
    return httpx.get("https://market.ourolabs.ai/api/board", params={"round": 3}, timeout=30).json()


def rows(j, me=ME):
    """(my shop row, my buyer row) from a live board json, with the ranks board_hist keeps."""
    rank = lambda rs, x: 1 + sum(1 for r in rs if (r.get("all") or 0) > (x.get("all") or 0))
    keep = lambda x: {k: v for k, v in x.items() if k not in ("owner", "card")}
    shops, buyers = j.get("shops") or [], j.get("buyers") or []
    s = next((x for x in shops if x.get("name") == me), None)
    b = next((x for x in buyers if x.get("name") == me), None)
    if s is None and b is None:
        raise ValueError("me not on the board")
    if s is not None:
        s = dict(keep(s), rank=rank(shops, s))
    if b is not None:
        b = dict(keep(b), rank=rank(buyers, b),
                 saved_rank=1 + sum(1 for r in buyers if (r.get("saved") or 0) > (b.get("saved") or 0)))
    return s, b


def _q(sql, args, db_path=None):
    d = sqlite3.connect(db_path or DB, timeout=60)
    try:
        return d.execute(sql, args).fetchone()
    finally:
        d.close()


def _point(r, src="스냅샷"):
    if not r:
        return None
    try:
        return Point(r[0], json.loads(r[1]) if r[1] else None, json.loads(r[2]) if r[2] else None, src)
    except (TypeError, ValueError):
        return None


def at(t, tol=TOL_S, db_path=None):
    """The board_hist snapshot nearest t within tol, or a failed Point."""
    try:
        r = _q("SELECT ts, shop, buyer FROM board_hist ORDER BY ABS(ts - ?) LIMIT 1", (t,), db_path)
    except sqlite3.Error:
        return Point()
    if not r or abs(r[0] - t) > tol:
        return Point()
    return _point(r) or Point()


def now(board_fn=None, clock=time.time, db_path=None, me=ME):
    """The live board, else the nearest snapshot, else a failed Point."""
    try:
        j = (board_fn or board)()
        s, b = rows(j, me)
        return Point(clock(), s, b, "보드", j)
    except Exception:
        p = at(clock(), db_path=db_path)
        return p


def next_snapshot(after, wait_s=None, sleep=time.sleep, db_path=None):
    """The first board_hist snapshot after `after`, waiting up to wait_s (WAIT_S); a failed Point if none."""
    end = time.time() + (WAIT_S if wait_s is None else wait_s)
    while True:
        try:
            r = _q("SELECT ts, shop, buyer FROM board_hist WHERE ts > ? ORDER BY ts LIMIT 1", (after,), db_path)
        except sqlite3.Error:
            r = None
        if r:
            p = _point(r)
            if p is not None:
                return p
            after = r[0]
            continue
        if time.time() >= end:
            return Point()
        sleep(60)


def per_h(a, b, side, k):
    """(b - a) per hour over the real time between the two readings, or None."""
    x, y = a.get(side, k), b.get(side, k)
    if x is None or y is None or not a.ts or not b.ts or b.ts <= a.ts:
        return None
    return (y - x) / ((b.ts - a.ts) / 3600)


def show(v, spec=""):
    """FAIL for a failed reading, else the value formatted."""
    return FAIL if v is None else format(v, spec)


def failed(**points):
    """The labels of the failed readings, in order."""
    return [k for k, p in points.items() if not p.ok]


def fail_line(names):
    return f"- 보드 {FAIL}: {', '.join(names)} -> 다음 스냅샷 값으로 다시 시도" if names else None


def send_with_retry(build, send, key, level="daily", sleep=time.sleep, db_path=None, now_ts=None):
    """build(cur) -> (text, failed labels); cur None = read "now" as usual (live board, then snapshot), else the Point
    to use as "now". Sends the report; while a reading failed, builds and sends it again with each next snapshot."""
    text, bad = build(None)
    send(level, key, text)
    out, after = [text], now_ts or time.time()
    for k in range(RETRIES):
        if not bad:
            break
        cur = next_snapshot(after, sleep=sleep, db_path=db_path)
        if not cur.ok:
            send(level, f"{key}:retry{k + 1}", f"{key.split(':')[0]}: 다음 보드 스냅샷이 {WAIT_S // 60}분 안에 없어 다시 읽기 중단")
            break
        text, bad = build(cur)
        from datetime import datetime
        text = f"[다시 읽음 {k + 1}: {datetime.fromtimestamp(cur.ts):%H:%M} 스냅샷]\n" + text
        send(level, f"{key}:retry{k + 1}", text)
        out.append(text)
        after = cur.ts
    return out


def mine(t, db_path=None, near_now_s=120, board_fn=None):
    """(shop row, buyer row) at t for the older reports' helpers: t within near_now_s of now = now() (live board, then
    snapshot), else at(t); (None, None) when the reading failed."""
    p = now(board_fn, db_path=db_path) if abs(time.time() - t) <= near_now_s else at(t, db_path=db_path)
    return p.shop, p.buyer
