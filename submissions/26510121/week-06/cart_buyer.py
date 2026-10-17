"""Shop from a public A2A directory using one shopping list and total budget."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

from negotiation.config import ROOT, load_settings
from negotiation.harness import HarnessError, ModelHarness
from negotiation.a2a_client import ProtocolError
from negotiation.runlog import RunLog
from negotiation.shopping import Shopper


async def run(args):
    settings = load_settings()
    log_dir = args.log_dir or ROOT / "logs/challenge-2" / datetime.now(timezone.utc).strftime("buyer-%Y%m%dT%H%M%S.%fZ")
    log = RunLog(log_dir / "cart-events.jsonl", settings.api_key)
    shopper = None
    try:
        directory = json.loads(args.directory.read_text(encoding="utf-8"))
        shopping = json.loads(args.shopping_list.read_text(encoding="utf-8"))
        shopper = Shopper(shopping["items"], shopping["total_budget"], ModelHarness(log_dir), log,
                          concurrency=args.concurrency)
        async with asyncio.timeout(280):
            result = await shopper.run(directory)
        output = json.dumps(result, ensure_ascii=False)
        print(output.replace(settings.api_key, "[REDACTED]") if settings.api_key else output)
        return 0 if result["outcome"] == "purchased" else 1
    except (HarnessError, ProtocolError) as error:
        code = str(error)
    except TimeoutError:
        code = "cart_deadline_exceeded"
    except Exception:
        code = "invalid_cart_input_or_unexpected_error"
    log.record("cart_failed", code=code, ledger=shopper.ledger.snapshot() if shopper else None)
    print(json.dumps({"outcome": "incomplete", "code": code}))
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--shopping-list", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--log-dir", type=Path)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
