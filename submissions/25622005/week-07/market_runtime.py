"""Keep the SSH tunnel and market kit together when the public URL changes.

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
    parser.add_argument("--dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--kit", type=Path, default=Path(__file__).resolve().parents[3] / "scripts/market_kit.py")
    args = parser.parse_args()
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
                tunnel = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
                threading.Thread(target=forward_lines, args=(tunnel, messages), daemon=True).start()
                current_url = None
                retry_kit_at = 0.0
                # Preserve raw SSH output, including failures, without editing it.
                with (logs / "ssh-runtime.log").open("a", encoding="utf-8") as ssh_log:
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
                        found = re.search(r"https://[a-z0-9]+\.lhr\.life", line)
                        if found and found.group() != current_url:
                            current_url = found.group()
                            log(f"public URL changed to {current_url}; restarting kit with the same keys")
                            stop(kit)
                            kit = None
                            retry_kit_at = 0.0
                        if current_url and (kit is None or kit.poll() is not None) and time.monotonic() >= retry_kit_at:
                            command = [sys.executable, "-u", str(args.kit.resolve()), "join",
                                       "--dir", str(folder), "--market", args.market,
                                       "--name", args.name, "--port", str(args.port), "--public-url", current_url]
                            kit = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=kit_output,
                                                   stderr=subprocess.STDOUT, start_new_session=True)
                            (private / "kit.pid").write_text(str(kit.pid) + "\n")
                            retry_kit_at = time.monotonic() + 30
                            log(f"kit started, pid {kit.pid}")
                stop(kit)
                kit = None
                stop(tunnel)
                tunnel = None
                if running:
                    log("SSH connection ended; retrying in 10 seconds")
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
