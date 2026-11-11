#!/usr/bin/python3
"""Keep a Cloudflare Quick Tunnel on the Mac for the shop port the container publishes (127.0.0.1:8900),
and restart it when inbound traffic stops.

The tunnel lives outside the container, so a container recreate or restart keeps the shop's public URL:
other agents remember this shop by URL. This script runs cloudflared, reads the trycloudflare.com URL it
prints, writes it to <data>/.market/fixed-address.json as an external fixed address (docker/launch.py then
starts market_runtime.py --fixed-config, which registers the kit with that URL), and when cloudflared
reconnects with a new URL it restarts the container so the kit re-registers, then runs docker/hot_update.sh
(a restart copies the image's hook code over /data and kills the strategist loop; the script puts both back).
When cloudflared exits it is started again after 10 s.

Watchdog (added 11-12, after 11-11 when Quick Tunnels of 10-13 of the 33 trycloudflare shops, ours among
them, went unreachable from the market at the same moments while the market's orders and our own buyer ran
normally: 28 silences, 337 min): every CHECK_S the script looks at the data folder. The seller log
(logs/decisions/seller.jsonl) gets a row for every message a buyer sends us, and the buyer hook log
(logs/hooks/market_buyer.log) a line for every order the market sends our buyer. When the seller log has
been silent for SILENT_S while the buyer log moved inside that span, the market is working and only our
inbound path is dead: cloudflared is terminated, comes back with a new URL, and the container re-registers.
A public card probe (the URL from the Mac, python user agent) that fails PROBE_FAILS checks in a row does the
same. A quiet market (both logs still) is not a reason to restart, and after a restart COOLDOWN_S pass before
the next one, since the restart itself silences the seller for a minute. Every verdict is logged.
Run it under launchd (docker/com.psyoung82.market-tunnel.plist) or by hand:

    /usr/bin/python3 docker/host_tunnel.py [--data DIR] [--port 8900] [--once-url]

Logs go to <data>/logs/host-tunnel.log and cloudflared's own lines to <data>/logs/host-cloudflared.log.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CLOUDFLARED = "/opt/homebrew/bin/cloudflared"
CHECK_S, SILENT_S, PROBE_FAILS, COOLDOWN_S = 60, 300, 3, 600


def log(logs, msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} [host-tunnel] {msg}\n"
    with (logs / "host-tunnel.log").open("a", encoding="utf-8") as f:
        f.write(line)
    sys.stdout.write(line)
    sys.stdout.flush()


def current_url(private):
    try:
        return json.loads((private / "fixed-address.json").read_text(encoding="utf-8")).get("public_url")
    except (OSError, ValueError, AttributeError):
        return None


def write_url(private, url):
    tmp = private / "fixed-address.json.tmp"
    tmp.write_text(json.dumps({"public_url": url, "external": True,
                               "note": "written by docker/host_tunnel.py: the Quick Tunnel runs on the Mac"},
                              indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(private / "fixed-address.json")


def container_running():
    out = subprocess.run(["podman", "ps", "--filter", "name=week08-market-market-1", "--format", "{{.Names}}"],
                         capture_output=True, text=True)
    return "week08-market-market-1" in out.stdout


def restart_container(logs):
    compose = HERE / "compose.yaml"
    r = subprocess.run(["podman", "compose", "-f", str(compose), "--project-directory", str(HERE), "restart", "market"],
                       capture_output=True, text=True, timeout=180)
    log(logs, f"container restart exit {r.returncode}: {(r.stderr or r.stdout).strip()[-200:]}")
    threading.Thread(target=hot_update, args=(logs,), daemon=True).start()


def hot_update(logs):
    """After a container restart: the hook code and the strategist loop (hot_update.sh rsyncs and restarts it)."""
    try:
        r = subprocess.run(["sh", str(HERE / "docker" / "hot_update.sh")], capture_output=True, text=True, timeout=300)
        log(logs, f"hot_update exit {r.returncode}: {(r.stdout or r.stderr).strip()[-200:]}")
    except Exception as e:
        log(logs, f"hot_update error {type(e).__name__}: {e}")


# --- the watchdog

def mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def probe(url, timeout=10):
    """True when the public URL serves our agent card (the request a buyer's kit makes first)."""
    try:
        req = urllib.request.Request(url + "/.well-known/agent-card.json", headers={"User-Agent": "python-httpx/0.27"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200 and b'"name"' in r.read(4096)
    except Exception:
        return False


def verdict(now, seller_mtime, buyer_mtime, probe_fails, last_restart):
    """Why to restart cloudflared, or None. now and the mtimes are epoch seconds."""
    if now - last_restart < COOLDOWN_S:
        return None
    silent = now - seller_mtime
    if silent >= SILENT_S and now - buyer_mtime < SILENT_S:
        return f"seller silent {silent:.0f} s while the buyer hook ran {now - buyer_mtime:.0f} s ago"
    if probe_fails >= PROBE_FAILS:
        return f"public card failed {probe_fails} checks"
    return None


class Watchdog(threading.Thread):
    def __init__(self, data, logs, get_proc):
        super().__init__(daemon=True)
        self.data, self.logs, self.get_proc = data, logs, get_proc
        self.last_restart, self.fails = 0.0, 0
        self.seller = data / "logs" / "decisions" / "seller.jsonl"
        self.buyer = data / "logs" / "hooks" / "market_buyer.log"

    def run(self):
        while True:
            time.sleep(CHECK_S)
            try:
                self.check(time.time())
            except Exception as e:
                log(self.logs, f"watchdog error {type(e).__name__}: {e}")

    def check(self, now):
        proc = self.get_proc()
        url = current_url(self.data / ".market")
        if proc is None or proc.poll() is not None or not url or not container_running():
            self.fails = 0
            return
        self.fails = 0 if probe(url) else self.fails + 1
        if self.fails:
            log(self.logs, f"watchdog: public card probe failed ({self.fails})")
        why = verdict(now, mtime(self.seller), mtime(self.buyer), self.fails, self.last_restart)
        if why:
            log(self.logs, f"watchdog: {why}; restarting cloudflared (a new URL follows, then the container)")
            self.last_restart, self.fails = now, 0
            proc.terminate()


def run_tunnel(args, private, logs, state):
    cmd = [CLOUDFLARED, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{args.port}", "--protocol", "http2"]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    state["proc"] = proc
    log(logs, f"cloudflared started, pid {proc.pid}")
    url = None
    with (logs / "host-cloudflared.log").open("a", encoding="utf-8") as raw:
        for line in proc.stdout:
            raw.write(line)
            raw.flush()
            m = URL_RE.search(line)
            if m and m.group(0) != url:
                url = m.group(0)
                before = current_url(private)
                write_url(private, url)
                if before == url:
                    log(logs, f"tunnel URL unchanged: {url}")
                else:
                    log(logs, f"tunnel URL {before or '(none)'} -> {url}; fixed-address.json written")
                    if before and container_running():
                        restart_container(logs)
                if args.once_url:
                    proc.terminate()
                    return 0
    code = proc.wait()
    state["proc"] = None
    log(logs, f"cloudflared exited with {code}")
    return code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=Path(os.environ.get("MARKET_DATA_DIR", "/Users/psyoung82/docker/compose/term/data")))
    ap.add_argument("--port", type=int, default=8900)
    ap.add_argument("--once-url", action="store_true", help="exit after the first URL is written (setup check)")
    args = ap.parse_args()
    private, logs = args.data / ".market", args.data / "logs"
    private.mkdir(exist_ok=True)
    logs.mkdir(exist_ok=True)
    state = {"proc": None}
    if not args.once_url:
        Watchdog(args.data, logs, lambda: state["proc"]).start()
    while True:
        try:
            code = run_tunnel(args, private, logs, state)
        except Exception as e:  # keep the supervisor alive whatever cloudflared or podman do
            log(logs, f"error {type(e).__name__}: {e}")
            code = -1
        if args.once_url and code == 0:
            return
        time.sleep(10)


if __name__ == "__main__":
    main()
