"""One real A2A incident probe, separate from counted market orders.

No result is submitted to the market and no receipt is fabricated. A confirmed
receipt violation is persisted for the next ordinary market-buyer selection.
"""
import argparse
import asyncio
import contextlib
import json
from pathlib import Path

import market_buyer


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    args = parser.parse_args()
    shops = json.loads(Path(".market/shops.json").read_text(encoding="utf-8"))
    selected = next(s for s in shops if s["name"] == args.name)
    output = Path("logs/challenge-2")
    output.mkdir(parents=True, exist_ok=True)
    if (output / "probe.log").exists():
        raise SystemExit("Probe already recorded; preserve it rather than overwrite evidence.")
    (output / "suspect-reviews.json").write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    market_buyer.MEMORY = Path(".market/probe-memory.json")
    with (output / "probe.log").open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            result = asyncio.run(market_buyer.purchase([selected], "lamp", 150))
            print(json.dumps(result, ensure_ascii=True))
    memory = market_buyer.load_memory()
    if memory["shops"].get(args.name, {}).get("blocked"):
        (output / "confirmed-memory.json").write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
        # Demonstrate the next selection step excludes this identity even if its
        # URL changes, without another network trade or another model call.
        with (output / "avoidance.log").open("w", encoding="utf-8") as log:
            with contextlib.redirect_stdout(log):
                eligible = market_buyer.candidates(shops, memory)
                assert args.name not in {s["name"] for s in eligible}
                print(json.dumps({"avoided": args.name, "eligible": [s["name"] for s in eligible]}))
        print("Confirmed receipt misconduct and subsequent name-based avoidance recorded.")
    else:
        print("No receipt misconduct confirmed; the original observation remains in probe.log.")
