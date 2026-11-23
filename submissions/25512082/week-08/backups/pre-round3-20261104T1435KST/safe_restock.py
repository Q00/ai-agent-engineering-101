"""Round 3 preflight restock hook that never purchases inventory.

The hook accepts the market state path required by the kit contract, but it
deliberately does not inspect that path.  Missing or malformed local state must
therefore fail closed to the same empty purchase plan.
"""
from __future__ import annotations

import argparse
import json


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Return a no-purchase restock plan")
    parser.add_argument("--state", required=True, help="Path to the kit's .market directory")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    parse_args(argv)
    print(json.dumps({"buy": []}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
