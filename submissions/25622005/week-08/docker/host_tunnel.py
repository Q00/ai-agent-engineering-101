#!/usr/bin/python3
"""Keep a Cloudflare Quick Tunnel on the Mac for the shop port the container publishes (127.0.0.1:8900).

The tunnel lives outside the container, so a container recreate or restart keeps the shop's public URL:
other agents remember this shop by URL. This script runs cloudflared, reads the trycloudflare.com URL it
prints, writes it to <data>/.market/fixed-address.json as an external fixed address (docker/launch.py then
starts market_runtime.py --fixed-config, which registers the kit with that URL), and when cloudflared
reconnects with a new URL it restarts the container so the kit re-registers. When cloudflared exits it is
started again after 10 s. Run it under launchd (docker/com.psyoung82.market-tunnel.plist) or by hand:

    /usr/bin/python3 docker/host_tunnel.py [--data DIR] [--port 8900] [--once-url]

Logs go to <data>/logs/host-tunnel.log and cloudflared's own lines to <data>/logs/host-cloudflared.log.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CLOUDFLARED = "/opt/homebrew/bin/cloudflared"


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


def run_tunnel(args, private, logs):
    cmd = [CLOUDFLARED, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{args.port}", "--protocol", "http2"]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
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
    while True:
        try:
            code = run_tunnel(args, private, logs)
        except Exception as e:  # keep the supervisor alive whatever cloudflared or podman do
            log(logs, f"error {type(e).__name__}: {e}")
            code = -1
        if args.once_url and code == 0:
            return
        time.sleep(10)


if __name__ == "__main__":
    main()
