"""Restart the market kit safely (used by the /kit-restart skill).

    .venv/bin/python ops/kit_restart.py            # wait for a supply window to close, restart, verify
    .venv/bin/python ops/kit_restart.py --now      # restart at once (the shop is already unreachable)
    .venv/bin/python ops/kit_restart.py --dry      # only say when it would restart

A restart cuts in-flight deals and the shop is dark ~1.5-3 min, so it waits until right after a supply window
(shop_guard.after_supply_window) unless --now. run_market.sh (the supervisor) starts the kit again; the kit can
exit once or twice when the market refuses the first join, which the supervisor retries every 30 s. Success =
'shop registered as' in logs/market-kit.log after the restart, the card answering at the public URL, and seller
tasks arriving again. Exit code 0 on success, 1 otherwise. No model call.
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import shop_guard  # noqa: E402

LOG = HERE / "logs" / "market-kit.log"
SUP = HERE / "logs" / "market-supervisor.log"


def say(msg):
    print(f"{time.strftime('%H:%M:%S', time.gmtime())} UTC {msg}", flush=True)


def log_tail(n=600):
    try:
        return LOG.read_text(encoding="utf-8", errors="ignore").splitlines()[-n:]
    except OSError:
        return []


def card_ok(url):
    try:
        req = urllib.request.Request(url.rstrip("/") + "/.well-known/agent-card.json", headers={"User-Agent": "curl/8.5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:   # Cloudflare answers 403 to Python's default UA
            return r.status == 200
    except Exception:
        return False


def public_url():
    for l in reversed(log_tail(3000)):
        if "through the kit at " in l:
            return l.split("through the kit at ")[-1].strip()
    return None


FREEZE_END, FREEZE_S = 1794927540, 3 * 3600   # 11-16 (user: '12 h is too long, make it 3'): no routine restart
                                               # in the last 3 h of Round 3; --now (shop already down) still runs


def main():
    now_mode, dry = "--now" in sys.argv, "--dry" in sys.argv
    if not now_mode and time.time() >= FREEZE_END - FREEZE_S:
        say("last 3 h of the round: no routine restart (use --now only if the shop is down)")
        return 2
    if not now_mode:
        t0 = time.time()
        while not shop_guard.after_supply_window():
            if dry:
                say("not right after a supply window; would wait")
                return 0
            if time.time() - t0 > 40 * 60:
                say("no supply window closed within 40 min; giving up (use --now)")
                return 1
            time.sleep(15)
    if dry:
        say("would restart now")
        return 0
    before = time.strftime("%H:%M", time.gmtime())
    pid = shop_guard.restart_kit()
    say(f"killed kit pid {pid}; waiting for the supervisor to start it again")
    if not pid:
        say("no kit process found")
        return 1
    t0 = time.time()
    registered = None
    while time.time() - t0 < 15 * 60:
        time.sleep(10)
        for l in log_tail(400):
            if "shop registered as" in l and l[:5] >= before:
                registered = l
        if registered:
            break
    if not registered:
        say("no 'shop registered' within 15 min; read logs/market-supervisor.log")
        print("\n".join(SUP.read_text(encoding="utf-8", errors="ignore").splitlines()[-5:]))
        return 1
    say(registered.strip())
    exits = [l for l in SUP.read_text(encoding="utf-8", errors="ignore").splitlines()[-10:] if "kit exited" in l]
    if exits:
        say(f"supervisor saw {len(exits)} short exit(s) before it registered (normal when the market refuses the first join)")
    url = public_url()
    say(f"public URL {url}: card {'answers' if url and card_ok(url) else 'does NOT answer'}")
    t1 = time.time()
    while time.time() - t1 < 180:
        tasks = [l for l in log_tail(300) if "[seller]" in l and " task " in l and l[:8] >= registered[:8]]
        if tasks:
            say(f"seller tasks arriving again ({len(tasks)} so far)")
            return 0
        time.sleep(10)
    say("registered, but no seller task within 3 min (the market may not list us yet)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
