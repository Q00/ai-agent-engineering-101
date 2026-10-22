"""Run the official kit with recovery for order polling only.

The kit returns the buyer's outstanding order on its next poll. Results and
seller requests are never retried here. The shared scripts remain unchanged.
"""
import argparse
from pathlib import Path
import os
import shlex
import signal
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[2] / "scripts"))
import market_kit

OFFICIAL_MARKET = market_kit.Market
RETRYABLE_ERRORS = (httpx.TimeoutException, httpx.NetworkError,
                    httpx.RemoteProtocolError, httpx.ProxyError)


class ReliableMarket(OFFICIAL_MARKET):
    def call(self, method, path, role="buyer", **kwargs):
        delay = 1
        while True:
            try:
                return super().call(method, path, role=role, **kwargs)
            except RETRYABLE_ERRORS as exc:
                if (method.upper(), path, role) != ("POST", "/api/orders", "buyer"):
                    raise
                # Log the exception type, never its request or credentials.
                market_kit.log(f"[runtime] order poll {type(exc).__name__}; retry in {delay}s")
                time.sleep(delay)
                delay = min(delay * 2, 30)


def read_owner(path):
    """Read one dotenv assignment without evaluating shell expressions."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    values = []
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        name, separator, value = line.partition("=")
        if separator and name.strip() == "MARKET_OWNER":
            parts = shlex.split(value, comments=True)
            if len(parts) > 1:
                raise ValueError("MARKET_OWNER must contain one value")
            values.append(parts[0] if parts else "")
    if len(values) > 1:
        raise ValueError("MARKET_OWNER must have one assignment")
    return values[0] if values and values[0] else None


def wait_for_owner(path, environment_owner=None):
    if environment_owner:
        return environment_owner
    waiting_logged = False
    while True:
        try:
            owner = read_owner(path)
        except (OSError, ValueError):
            owner = None
        if owner:
            return owner
        if not waiting_logged:
            market_kit.log(f"[runtime] waiting_for_owner: set MARKET_OWNER in {path}")
            waiting_logged = True
        time.sleep(15)


def main():
    if sys.argv[1:2] != ["join"] or "--help" in sys.argv:
        return market_kit.main()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    owner_parser = argparse.ArgumentParser(add_help=False)
    owner_parser.add_argument("--owner")
    options, _ = owner_parser.parse_known_args()
    # Keep the claim inside this process; buyer and model subprocesses do not
    # need the market ownership credential in their inherited environment.
    environment_owner = os.environ.pop("MARKET_OWNER", None)
    owner = wait_for_owner(ROOT / ".market" / "owner.env",
                           options.owner or environment_owner)

    class AuthenticatedMarket(ReliableMarket):
        def __init__(self, base, owner_override=None):
            super().__init__(base, owner_override or owner)

    market_kit.Market = AuthenticatedMarket
    market_kit.log("[runtime] claim loaded; starting the official market kit")
    return market_kit.main()


if __name__ == "__main__":
    main()
