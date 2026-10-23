"""Keep the tunnel and market kit together when the public URL changes.

MARKET_OWNER is inherited from the environment, never written to disk.
This manages processes only; shop choice and trading stay in the agents.
"""
import argparse
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from configure_market_owner import kit_environment


def public_url_from_line(line):
    """Provider API errors are not announcements of a usable shop address."""
    for match in re.finditer(
        r"https://(?:[a-z0-9]+\.lhr\.life|[a-z0-9-]+\.trycloudflare\.com)(?![a-z0-9.-])", line
    ):
        if match.group() != "https://api.trycloudflare.com":
            return match.group()
    return None


class TunnelWatchdog:
    """A running process can still hold a revoked or unreachable tunnel."""

    def __init__(self, now, probe):
        self.started = now
        self.probe = probe
        self.url = None
        self.next_probe = now
        self.failures = 0

    def restart_reason(self, line, now, url):
        if "unauthorized: tunnel not found" in line.lower():
            return "provider revoked the tunnel"
        if not url:
            return "no public URL within 120 seconds" if now - self.started >= 120 else None
        if url != self.url:
            self.url = url
            self.next_probe = now + 45  # Allow the kit and public DNS to start.
            self.failures = 0
        if now >= self.next_probe:
            self.next_probe = now + 30
            self.failures = 0 if self.probe(url) else self.failures + 1
            if self.failures >= 3:
                return "public card failed three consecutive checks"
        return None


def stop(proc):
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except ProcessLookupError:
        pass
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def forward_lines(proc, messages):
    try:
        for line in proc.stdout:
            messages.put(line)
    finally:
        messages.put(None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--market", default="https://market.ourolabs.ai")
    parser.add_argument("--port", type=int, default=8900)
    parser.add_argument("--cloudflared", type=Path,
                        help="use this cloudflared executable instead of the SSH tunnel")
    parser.add_argument('--require-owner', action='store_true',
                        help='wait for MARKET_OWNER or ignored .market/market-owner before starting')
    parser.add_argument("--dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--kit", type=Path, default=Path(__file__).resolve().parents[3] / "scripts/market_kit.py")
    parser.add_argument("--fixed-config", type=Path,
                        help="ignored local JSON: reserved public_url and tunnel_command argument list")
    args = parser.parse_args()
    if args.fixed_config:
        from fixed_address_runtime import load_fixed_config
        load_fixed_config(args.fixed_config)  # Reject bad config before touching the runtime PID.
    # Keep recovery local to this supervisor and its Python children.
    from tunnel_dns import install
    install()
    support = str(Path(__file__).resolve().parent / 'dns_support')
    previous_path = os.environ.get('PYTHONPATH')
    os.environ['PYTHONPATH'] = support + (os.pathsep + previous_path if previous_path else '')
    folder = args.dir.resolve()
    logs = folder / "logs"
    logs.mkdir(exist_ok=True)
    private = folder / ".market"
    private.mkdir(exist_ok=True)
    (private / "runtime.pid").write_text(str(os.getpid()) + "\n")
    running = True

    def shutdown(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    def log(message):
        print(f"{datetime.now().astimezone().isoformat(timespec='seconds')} [runtime] {message}", flush=True)

    if args.require_owner:
        announced = False
        while running and not kit_environment(private).get('MARKET_OWNER'):
            if not announced:
                log('market requires GitHub claim code; waiting for local configure_market_owner.py setup')
                announced = True
            time.sleep(2)
        if not running:
            log('stopped while waiting for market authentication')
            return

    if args.fixed_config:
        from fixed_address_runtime import run_fixed
        run_fixed(args, folder, private, logs, lambda: running, log, stop)
        return

    tunnel = kit = None
    try:
        with (logs / "market-runtime-kit.log").open("a", encoding="utf-8") as kit_output:
            while running:
                messages = queue.Queue()
                command = ["ssh", "-4", "-F", "/dev/null", "-T", "-o", "BatchMode=yes",
                           "-o", "IdentityAgent=none", "-o", "ConnectTimeout=10",
                           "-o", "ConnectionAttempts=1", "-o", "StrictHostKeyChecking=accept-new",
                           "-o", f"UserKnownHostsFile={private / 'known_hosts'}",
                           "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=3",
                           "-o", "ExitOnForwardFailure=yes", "-R",
                           f"80:localhost:{args.port}", "nokey@localhost.run"]
                if args.cloudflared:
                    command = [str(args.cloudflared.resolve()), "tunnel", "--no-autoupdate",
                               "--url", f"http://127.0.0.1:{args.port}", "--protocol", "http2"]
                tunnel = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
                threading.Thread(target=forward_lines, args=(tunnel, messages), daemon=True).start()
                current_url = None
                retry_kit_at = 0.0
                from fixed_address_runtime import public_card_healthy
                watchdog = TunnelWatchdog(time.monotonic(),
                                          lambda url: public_card_healthy(url, args.name))
                # Preserve raw tunnel output, including failures, without editing it.
                tunnel_log = "cloudflared-runtime.log" if args.cloudflared else "ssh-runtime.log"
                with (logs / tunnel_log).open("a", encoding="utf-8") as ssh_log:
                    while running:
                        try:
                            line = messages.get(timeout=1)
                        except queue.Empty:
                            line = ""
                        if line is None:
                            break
                        if line:
                            ssh_log.write(line)
                            ssh_log.flush()
                        found = public_url_from_line(line)
                        if found and found != current_url:
                            current_url = found
                            log(f"public URL changed to {current_url}; restarting kit with the same keys")
                            stop(kit)
                            kit = None
                            retry_kit_at = 0.0
                        if current_url and (kit is None or kit.poll() is not None) and time.monotonic() >= retry_kit_at:
                            command = [sys.executable, "-u", str(args.kit.resolve()), "join",
                                       "--dir", str(folder), "--market", args.market,
                                       "--name", args.name, "--port", str(args.port), "--public-url", current_url]
                            kit = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=kit_output,
                                                   stderr=subprocess.STDOUT, start_new_session=True,
                                                   env=kit_environment(private))
                            (private / "kit.pid").write_text(str(kit.pid) + "\n")
                            retry_kit_at = time.monotonic() + 30
                            log(f"kit started, pid {kit.pid}")
                        reason = watchdog.restart_reason(line, time.monotonic(), current_url)
                        if reason:
                            log(f"{reason}; creating a fresh tunnel and re-registering with the same keys")
                            break
                stop(kit)
                kit = None
                stop(tunnel)
                tunnel = None
                if running:
                    log("tunnel connection ended; retrying in 10 seconds")
                    for _ in range(10):
                        if not running:
                            break
                        time.sleep(1)
    finally:
        stop(kit)
        stop(tunnel)
        log("stopped")


if __name__ == "__main__":
    main()
