"""Supervise a configured fixed HTTPS tunnel without rotating the kit URL.

Run market_runtime.py --fixed-config .market/fixed-address.json after configuring
an authenticated provider locally. The JSON contains public_url and a list of
tunnel_command arguments. Keep credentials in the provider's ignored config,
never in command arguments. This module does not reserve a hostname or log in.
"""
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit

import httpx


def load_fixed_config(path):
    data = json.loads(Path(path).read_text())
    url = data.get("public_url", "")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise ValueError("public_url must be a fixed HTTPS origin without credentials")
    command = data.get("tunnel_command")
    if (not isinstance(command, list) or not command
            or any(not isinstance(arg, str) or not arg for arg in command)):
        raise ValueError("tunnel_command must be a nonempty JSON array of arguments")
    return url.rstrip("/"), command


def public_card_healthy(url, name):
    try:
        with httpx.Client(timeout=5) as client:
            response = client.get(url + "/.well-known/agent-card.json")
            response.raise_for_status()
            card = response.json()
            return (isinstance(card, dict) and name in card.get("name", "")
                    and bool(card.get("supportedInterfaces")))
    except (httpx.HTTPError, ValueError, TypeError):
        return False


class FixedAddressRuntime:
    def __init__(self, url, command, kit_command, private, tunnel_output,
                 kit_output, log, stop, probe):
        self.url, self.command, self.kit_command = url, command, kit_command
        self.private = private
        self.tunnel_output, self.kit_output = tunnel_output, kit_output
        self.log, self.stop, self.probe = log, stop, probe
        self.tunnel = self.kit = None
        self.next_tunnel = self.next_kit = self.next_probe = 0
        self.failed_probes = 0

    def tick(self, now):
        if (self.tunnel is None or self.tunnel.poll() is not None) and now >= self.next_tunnel:
            self.tunnel = subprocess.Popen(
                self.command, stdin=subprocess.DEVNULL, stdout=self.tunnel_output,
                stderr=subprocess.STDOUT, start_new_session=True)
            (self.private / "tunnel.pid").write_text(str(self.tunnel.pid) + "\n")
            self.next_tunnel = now + 10
            self.log(f"fixed-address tunnel started, pid {self.tunnel.pid}")
        if self.tunnel is None or self.tunnel.poll() is not None:
            return  # Keep the kit and local A2A tasks alive during reconnects.
        if (self.kit is None or self.kit.poll() is not None) and now >= self.next_kit:
            self.kit = subprocess.Popen(
                self.kit_command, stdin=subprocess.DEVNULL, stdout=self.kit_output,
                stderr=subprocess.STDOUT, start_new_session=True)
            (self.private / "kit.pid").write_text(str(self.kit.pid) + "\n")
            self.next_kit = now + 30
            self.next_probe = now + 20  # Allow tunnel startup and registration.
            self.log(f"kit started with the configured fixed URL, pid {self.kit.pid}")
        if self.kit is not None and self.kit.poll() is None and now >= self.next_probe:
            self.next_probe = now + 15
            self.failed_probes = 0 if self.probe(self.url) else self.failed_probes + 1
            if self.failed_probes >= 3:
                self.log("public card failed three checks; reconnecting tunnel with the same URL")
                self.stop(self.tunnel)
                self.tunnel = None
                self.next_tunnel = now + 5
                self.failed_probes = 0

    def close(self):
        self.stop(self.kit)
        self.stop(self.tunnel)


def run_fixed(args, folder, private, logs, running, log, stop):
    url, command = load_fixed_config(args.fixed_config)
    kit_command = [sys.executable, "-u", str(args.kit.resolve()), "join",
                   "--dir", str(folder), "--market", args.market, "--name", args.name,
                   "--port", str(args.port), "--public-url", url]
    with (logs / "fixed-tunnel.log").open("a") as tunnel_output, (logs / "market-runtime-kit.log").open("a") as kit_output:
        runtime = FixedAddressRuntime(
            url, command, kit_command, private, tunnel_output, kit_output, log, stop,
            lambda address: public_card_healthy(address, args.name))
        try:
            while running():
                runtime.tick(time.monotonic())
                time.sleep(1)
        finally:
            runtime.close()
