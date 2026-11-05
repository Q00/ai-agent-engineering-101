#!/usr/bin/env python3
"""Shop re-join guard for the R3 market kit on labpc (2026-11-05, approved by the user).

The market drops a shop whose agent card it cannot read, and the kit joins again only when its own tunnel stops
answering, so a shop can stay unlisted while its tunnel is fine. Every 60 s this guard checks that the market is
otherwise healthy (the last shop lists hold >= 35 shops in 4 of 6, and >= 150 market settles in 10 min), that our
public agent card answers, and that our seller has had no buyer request and no ACP sale for 10 min. After three such
checks in a row it stops only the kit's quick tunnel (explicit PID, SIGTERM); the kit watchdog then opens a new
tunnel and joins the shop again while seller and buyer keep running. Right before every stop it checks again that no
request arrived and that the kit process is the same one. An episode tries again only after a join refused with
'cannot read an agent card' (about every 3.5 min), at most 10 tries; any other outcome (timeout, other refusal) ends
the episode and leaves the kit to its own retries. At most 6 episodes per rolling 24 h, 20 min cooldown after each.
Logs to /tmp/rejoin_guard.log without URLs or names.

usage: rejoin_guard.py [--once]   (--once prints the signals and exits without acting)
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from collections import deque

W = os.path.expanduser("~/ai-agent-engineering-101/submissions/25520013/week-09")
KIT_LOG, LOG = "logs/market-kit.log", "/tmp/rejoin_guard.log"
IDLE, N_OK, SETTLES_OK, MAX_EP, MAX_TRIES, EVERY, COOLDOWN = (
    600,
    35,
    150,
    6,
    10,
    60,
    1200,
)
REQUEST_DECISIONS = {"open", "accept", "cancel", "no_stock", "reject", "declined"}
URLISH = re.compile(r"https?://\S+|\S+\.trycloudflare\.com\S*")


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%m-%d %H:%M:%S") + " " + URLISH.sub("<url>", msg) + "\n")


def tail_lines(path, nbytes):
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        start = max(0, fh.tell() - nbytes)
        fh.seek(start)
        if start:
            fh.readline()  # drop the partial first line of a tail
        return fh.read().decode("utf-8", "replace").splitlines()


def hms_age(hms, now=None):
    """Age in seconds of an HH:MM:SS kit-log stamp (the log has no dates; up to 60 s ahead counts as clock skew)."""
    t = time.localtime(now)
    h, m, s = map(int, hms.split(":"))
    age = (t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec) - (h * 3600 + m * 60 + s)
    return age + 86400 if age < -60 else age


def idle_age():
    """Seconds since the last buyer request to our seller or ACP sale, whichever is newer."""
    t = 0
    for line in tail_lines(".r3/metrics.jsonl", 8_000_000):
        if '"seller"' not in line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("hook") == "seller" and r.get("decision") in REQUEST_DECISIONS:
            t = max(t, r.get("ts") or 0)
    age = time.time() - t if t else 10**9
    for line in tail_lines(KIT_LOG, 600_000):
        m = re.match(r"(\d\d:\d\d:\d\d) \[acp\] settled ", line)
        if m:
            age = min(age, hms_age(m.group(1)))
    return age


def market_health():
    """(shop lists ok, the last 6 shop-list sizes, market settles in the last 10 min)."""
    ns = []
    for line in tail_lines(KIT_LOG, 600_000):
        m = re.match(r"(\d\d:\d\d:\d\d) \[kit\] (\d+) shops in", line)
        if m:
            ns.append((m.group(1), int(m.group(2))))
    recent = ns[-6:]
    fresh = bool(recent) and hms_age(recent[-1][0]) < 240
    n_ok = fresh and len(recent) >= 4 and sum(n >= N_OK for _, n in recent) >= 4
    c, now = 0, time.time()
    for line in tail_lines(".r3/events.jsonl", 12_000_000):
        if '"settle"' not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if (
            e.get("type") == "settle"
            and e.get("src") == "market"
            and (e.get("ts") or 0) >= now - 600
        ):
            c += 1
    return n_ok, [n for _, n in recent], c


def kit_tunnel():
    """(cloudflared pid, kit pid) for the cloudflared whose parent runs market_kit.py join, else (None, None)."""
    out = subprocess.run(
        ["pgrep", "-x", "cloudflared"], capture_output=True, text=True
    ).stdout.split()
    for pid in out:
        try:
            with open(f"/proc/{pid}/stat") as fh:
                ppid = fh.read().rsplit(")", 1)[1].split()[1]
            with open(f"/proc/{ppid}/cmdline", "rb") as fh:
                cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except (OSError, IndexError):
            continue
        if "market_kit.py" in cmd and " join " in cmd:
            return int(pid), int(ppid)
    return None, None


def card_ok(cf_pid):
    """True when our agent card answers through the tunnel this cloudflared serves."""
    out = subprocess.run(["ss", "-ltnp"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if f"pid={cf_pid}," not in line:
            continue
        port = line.split()[3].rsplit(":", 1)[1]
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/quicktunnel", timeout=5
            ) as r:
                host = json.load(r).get("hostname")
            if host:
                with urllib.request.urlopen(
                    f"https://{host}/.well-known/agent-card.json", timeout=15
                ) as r:
                    return r.status == 200
        except Exception:
            continue
    return False


def rejoin(cf_pid, kill=os.kill, poll=10, wait=720):
    """Stop the kit's tunnel and wait for the watchdog's join: 'ok', 'fail <status> <error head>' or 'timeout'.

    A '[kit] watchdog:' line alone is not an end: after a tunnel that does not answer the kit tries again by itself."""
    size = os.path.getsize(KIT_LOG)
    kill(cf_pid, signal.SIGTERM)
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(poll)
        with open(KIT_LOG, "rb") as fh:
            fh.seek(size)
            new = fh.read().decode("utf-8", "replace")
        if "shop registered again" in new:
            return "ok"
        m = re.search(r"join as shop failed: (\d*) ?([^\n]{0,120})", new)
        if m:
            return f"fail {m.group(1)} {m.group(2)}"
    return "timeout"


def signals():
    cf, kit = kit_tunnel()
    age = idle_age()
    n_ok, recent, settles = market_health()
    card = card_ok(cf) if cf else False
    return cf, kit, age, n_ok, recent, settles, card


def episode(kit0):
    """Re-join through the kit watchdog; returns the number of tunnel stops."""
    stops = 0
    for i in range(1, MAX_TRIES + 1):
        cf, kit = kit_tunnel()
        if not cf:
            time.sleep(30)
            continue
        if kit != kit0:
            log("  the kit process changed, ending this episode")
            return stops
        age = idle_age()
        if age < IDLE:
            log(f"  a request arrived {age:.0f}s ago, ending this episode")
            return stops
        res = rejoin(cf)
        stops += 1
        log(f"  try {i}: {res}")
        if res == "ok":
            return stops
        if not (res.startswith("fail") and "cannot read an agent card" in res):
            log("  not the known refusal, leaving the rest to the kit")
            return stops
        time.sleep(5)
    return stops


def main():
    os.environ["TZ"] = "Asia/Seoul"
    time.tzset()
    os.chdir(W)
    if "--once" in sys.argv:
        cf, kit, age, n_ok, recent, settles, card = signals()
        print(
            f"tunnel found {bool(cf)}, idle {age:.0f}s, shop lists {recent} ok={n_ok}, "
            f"settles10m {settles}, card {card}"
        )
        return
    log(
        f"guard start pid {os.getpid()}: idle>=600s, lists>=35 (4 of 6), settles10m>=150, card ok, "
        "3 checks in a row; max 6 episodes/24h"
    )
    episodes, streak, last_beat, cool_until, last_limit_log = deque(), 0, 0, 0, 0
    while True:
        time.sleep(EVERY)
        try:
            cf, kit, age, n_ok, recent, settles, card = signals()
            cond = (
                bool(cf)
                and n_ok
                and settles >= SETTLES_OK
                and card
                and age >= IDLE
                and time.time() >= cool_until
            )
            streak = streak + 1 if cond else 0
            if time.time() - last_beat >= 900:
                last_beat = time.time()
                log(
                    f"beat: idle {min(age, 99999):.0f}s, lists {recent}, settles10m {settles}, card {card}, "
                    f"streak {streak}"
                )
            if streak < 3:
                continue
            streak = 0
            while episodes and episodes[0] < time.time() - 86400:
                episodes.popleft()
            if len(episodes) >= MAX_EP:
                if time.time() - last_limit_log >= 3600:
                    last_limit_log = time.time()
                    log("limit reached: 6 episodes in 24 h, not acting")
                continue
            episodes.append(time.time())
            log(
                f"episode {len(episodes)}: idle {age:.0f}s, lists {recent}, settles10m {settles}; "
                "re-joining via the kit watchdog"
            )
            episode(kit)
            cool_until = time.time() + COOLDOWN
        except Exception as e:
            log(f"guard error {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
