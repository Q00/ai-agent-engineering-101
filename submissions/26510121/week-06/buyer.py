"""A2A buyer entry point. Every offer and voluntary walk-away is model-driven."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path

import httpx

from negotiation.a2a_client import A2AClient, ProtocolError
from negotiation.buying import negotiate
from negotiation.config import ROOT, load_settings
from negotiation.harness import HarnessError, ModelHarness
from negotiation.runlog import RunLog


async def run(args):
    settings = load_settings()
    log_dir = args.log_dir or ROOT / "logs" / "buyer" / datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S.%fZ")
    log = RunLog(log_dir / "buyer-events.jsonl", settings.api_key)
    log.record("startup", item=args.item, budget=args.budget, model=settings.model)
    client = A2AClient(log)
    try:
        # Smaller than the course test's 300-second process deadline.
        async with asyncio.timeout(240):
            result = await negotiate(client, ModelHarness(log_dir), args.url, args.item, args.budget, log)
        output = json.dumps(result, ensure_ascii=False)
        print(output.replace(settings.api_key, "[REDACTED]") if settings.api_key else output)
        return 0
    except (ProtocolError, HarnessError) as error:
        log.record("buyer_failed", code=str(error))
        print(json.dumps({"outcome": "failed", "code": str(error)}))
    except TimeoutError:
        log.record("buyer_failed", code="buyer_deadline_exceeded")
        print(json.dumps({"outcome": "failed", "code": "buyer_deadline_exceeded"}))
    except httpx.HTTPError:
        log.record("buyer_failed", code="a2a_network_error")
        print(json.dumps({"outcome": "failed", "code": "a2a_network_error"}))
    except Exception:
        log.record("buyer_failed", code="unexpected_buyer_error")
        print(json.dumps({"outcome": "failed", "code": "unexpected_buyer_error"}))
    finally:
        await client.close()
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", type=int, required=True)
    parser.add_argument("--log-dir", type=Path)
    args = parser.parse_args()
    if not args.item.strip() or args.budget < 0:
        parser.error("A nonempty item and nonnegative integer budget are required")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
