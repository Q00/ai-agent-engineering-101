"""The whole public board, kept over time (user 11/5 01:3x: the final score is our place on 11/17 23:59, so we need
how fast the players near us grow, not one snapshot).

    py -m outer.board_history          # every 10 min until stopped
    py -m outer.board_history --once

Its own process, outside outer.py: it only reads GET /api/board and appends one line per snapshot to
memory/market/board-full-<date>.jsonl.gz (gzip members appended; gzip.open reads them as one file), then
writes our ranks (outer/position.py -> logs/strategy/position.md) and after 08:00 the night report (outer/morning.py). Reviews and
avatars are dropped; shops, buyers, players and totals are kept as the board shows them. Before 11/5 01:4x only
our own rows were kept (memory/market/board-ours.jsonl) and scoreboard.json is overwritten each time.
"""
import gzip
import json
import sys
import time
import urllib.request

from core import paths

URL = "https://market.ourolabs.ai/api/board"
EVERY = 600


def slim(rows):
    out = []
    for r in rows or []:
        r = dict(r)
        owner = r.pop("owner", None)
        if isinstance(owner, dict):
            r["login"] = owner.get("login")
        r.pop("card", None)
        out.append(r)
    return out


def snapshot(now=None):
    now = now or time.time()
    b = json.loads(urllib.request.urlopen(URL, timeout=60).read())
    line = {"ts": round(now, 1), "round": b.get("round"), "totals": b.get("totals"),
            "shops": slim(b.get("shops")), "buyers": slim(b.get("buyers")), "players": slim(b.get("players"))}
    f = paths.memory() / "market" / f"board-full-{time.strftime('%Y%m%d', time.localtime(now))}.jsonl.gz"
    f.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(f, "at", encoding="utf-8") as g:
        g.write(json.dumps(line, ensure_ascii=False) + "\n")
    return f, len(line["shops"]), len(line["buyers"]), len(line["players"])


def after(now=None):
    """After each snapshot: our ranks (outer/position.py), and once after 08:00 the night report (outer/morning.py)."""
    from outer import morning, position
    position.build(now=now)
    tm = time.localtime(now or time.time())
    day = time.strftime("%Y-%m-%d", tm)
    if tm.tm_hour >= morning.END_H and not (paths.root() / "logs" / "strategy" /
                                            f"morning-{day.replace('-', '')}.md").exists():
        print(time.strftime("%H:%M:%S"), "morning", morning.build(day), flush=True)


def main():
    while True:
        try:
            print(time.strftime("%H:%M:%S"), *snapshot(), flush=True)
            after()
        except Exception as e:                       # a 502 or the network: next round
            print(time.strftime("%H:%M:%S"), "failed", type(e).__name__, e, flush=True)
        if "--once" in sys.argv:
            return
        time.sleep(EVERY)


if __name__ == "__main__":
    main()
