"""Keep the classroom shop/buyer running across short-lived Serveo tunnels.

MARKET_OWNER is inherited from the environment, never written to disk.
Run from week-07: .venv/bin/python run_public_market.py --name dyishappy
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import threading
import time
import uuid

import httpx

from public_market import PublicProxy, kit


class Tunnel:
    def __init__(self, directory, port, name):
        self.created = time.monotonic()
        self.expired = threading.Event()
        self.url = None
        urls = queue.Queue()
        command = [
            "ssh", "-F", "/dev/null", "-T", "-p", "443",
            "-i", ".market/serveo_key.pem",
            "-o", "IdentityAgent=none", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "UserKnownHostsFile=.market/known_hosts",
            "-o", "NumberOfPasswordPrompts=1", "-o", "ConnectTimeout=15",
            "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3",
            "-R", f"80:127.0.0.1:{port}", f"{name}@serveo.net",
        ]
        self.process = subprocess.Popen(
            command, cwd=directory, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, start_new_session=True,
        )
        logfile = directory / "logs" / f"managed-tunnel-{uuid.uuid4().hex[:10]}.log"

        def pump():
            with logfile.open("x") as out:
                for line in self.process.stdout:
                    out.write(line)
                    out.flush()
                    if "expired" in line.lower():
                        self.expired.set()
                    match = re.search(r"https://[\w.-]+\.serveousercontent\.com", line)
                    if match:
                        urls.put(match.group())
            self.expired.set()

        threading.Thread(target=pump, daemon=True).start()
        try:
            self.url = urls.get(timeout=40)
        except queue.Empty:
            kit.stop(self.process)
            raise RuntimeError(f"SSH tunnel failed; see {logfile.name}")

    def close(self):
        kit.stop(self.process)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True)
    ap.add_argument("--market", default="https://market.ourolabs.ai")
    ap.add_argument("--port", type=int, default=8900)
    ap.add_argument("--round2", action="store_true", help="Use the separately verified signed-receipt configuration")
    # Rotate between orders, leaving enough time for the kit's 300-second buyer
    # deadline before the observed ten-minute anonymous tunnel expiry.
    ap.add_argument("--renew-after", type=int, default=180, choices=range(30, 181), metavar="30..180")
    args = ap.parse_args()
    d = Path(__file__).resolve().parent
    for filename in ("shop_key.pem", "buyer_key.pem", ".market/serveo_key.pem"):
        if not (d / filename).exists():
            ap.error(f"existing identity required: {filename}")
    if not os.environ.get("MARKET_OWNER"):
        ap.error("set MARKET_OWNER in the environment")
    lock = (d / ".market/live.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    kit.LOG_FILE = d / "logs/market-kit.log"
    config_path = d / ("agent-round2.json" if args.round2 else "agent.json")
    agent = json.loads(config_path.read_text())
    kit.log(f"[managed] configuration: {config_path.name}")
    keys = {"shop": kit.load_key(d / "shop_key.pem"), "buyer": kit.load_key(d / "buyer_key.pem")}
    market = kit.Market(args.market, os.environ["MARKET_OWNER"])
    processes, tunnels = [], []
    original_start = kit.start

    def tracked_start(*a, **kw):
        proc = original_start(*a, **kw)
        processes.append(proc)
        return proc

    kit.start = tracked_start
    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    runtime = d / "agent-market"
    runtime.mkdir(exist_ok=True)
    (runtime / "live.pid").write_text(f"{os.getpid()}\n")
    try:
        port = kit.free_port()
        seller = kit.start(kit.fill(agent["seller"]["cmd"], port=port), d)
        kit.pump(seller, "[seller]")
        if not kit.wait_card(f"http://127.0.0.1:{port}"):
            raise RuntimeError("seller failed to start")
        proxy = PublicProxy(args.port)
        proxy.target(f"http://127.0.0.1:{port}")
        buyer_proxy = kit.Proxy(kit.free_port())
        active = None
        buyer_joined = False
        while True:
            if seller.poll() is not None:
                raise RuntimeError("seller exited")
            # Finished subprocesses do not need to accumulate forever.
            processes[:] = [p for p in processes if p.poll() is None]
            if active is None or active.expired.is_set() or time.monotonic() - active.created >= args.renew_after:
                fresh = None
                try:
                    fresh = Tunnel(d, args.port, args.name)
                    tunnels.append(fresh)
                    proxy.public = fresh.url
                    if not kit.wait_card(fresh.url, 20):
                        raise RuntimeError("new public card did not respond")
                    joined = market.join("shop", args.name, keys["shop"], fresh.url)
                    active = fresh
                    (runtime / "shop-url").write_text(fresh.url + "\n")
                    (runtime / "tunnel.pid").write_text(f"{fresh.process.pid}\n")
                    kit.log(f"[managed] shop registered as {args.name}, round {joined['round']}: {fresh.url}")
                    # Keep the preceding tunnel through this next order, allowing
                    # requests already using its URL to finish on the same seller.
                    for old in tunnels[:-2]:
                        old.close()
                    tunnels[:] = tunnels[-2:]
                except (RuntimeError, httpx.HTTPError) as exc:
                    if fresh is not None:
                        fresh.close()
                        tunnels.remove(fresh)
                    if active is not None:
                        proxy.public = active.url
                    kit.log(f"[managed] {exc}; retry in 5 seconds")
                    time.sleep(5)
                    continue
            if not buyer_joined:
                joined = market.join("buyer", args.name, keys["buyer"])
                kit.log(f"[managed] buyer registered as {args.name}, round {joined['round']}")
                buyer_joined = True
            try:
                response = market.call("POST", "/api/orders")
                if response.status_code == 429:
                    time.sleep(min(response.json().get("wait", 5), 15))
                    continue
                response.raise_for_status()
                kit.handle_order(response.json(), agent, d, market, buyer_proxy, active.url, keys)
            except Exception as exc:
                kit.log(f"[managed] order failed: {type(exc).__name__}: {exc}")
                time.sleep(5)
    except KeyboardInterrupt:
        kit.log("[managed] stopping")
    finally:
        for proc in reversed(processes):
            kit.stop(proc)
        for tunnel in tunnels:
            tunnel.close()
        market.c.close()
        lock.close()


if __name__ == "__main__":
    main()
