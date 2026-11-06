"""Enter the market claim code privately on the deployment server.

This is a market account-link code, not an LLM API key. The value is never
printed and is stored outside the repository with owner-only permissions.
"""
import getpass
import json
import os
from pathlib import Path


def main():
    value = getpass.getpass("Market GitHub claim code (input hidden): ").strip()
    if not value or not value.isascii() or any(ord(c) < 32 for c in value):
        raise SystemExit("Expected a nonempty, single-line ASCII claim code.")
    directory = Path.home() / "agent-market-week07" / "secrets"
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / "market.env"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write("MARKET_OWNER=" + json.dumps(value) + "\n")
    print("Market account-link code saved privately. No secret was printed.")


if __name__ == "__main__":
    main()
