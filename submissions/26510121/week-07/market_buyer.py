"""Market buyer: personal evidence, cautious reviews and bounded exploration."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

import httpx

from negotiation.a2a_client import A2AClient, ProtocolError
from negotiation.buying import negotiate
from negotiation.config import ROOT, load_settings
from negotiation.harness import HarnessError, ModelHarness
from negotiation.market import choose_shop, read_memory, read_shops, remember, review_trade
from negotiation.runlog import RunLog


class BudgetClient(A2AClient):
    def __init__(self, log, budget, **kwargs):
        super().__init__(log, **kwargs)
        self.budget, self.turns = budget, 0

    async def send(self, text, task_id=None):
        # Final boundary before the HTTP request, in addition to schema validation.
        if task_id is not None and re.fullmatch(r"[+-]?\d+", text):
            price = int(text)
            if not 0 < price <= self.budget:
                raise ProtocolError("outgoing_price_outside_budget")
            self.turns += 1
        return await super().send(text, task_id)


class InformedHarness:
    def __init__(self, harness, evidence):
        self.harness, self.evidence = harness, evidence

    async def buyer(self, context, observation):
        # Public text remains data; it cannot replace trusted item/budget fields.
        return await self.harness.buyer(context, {**observation, "market_context_untrusted": self.evidence})


async def run(args):
    settings = load_settings()
    folder = ROOT / "logs" / "market-buyer" / datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S.%fZ")
    log = RunLog(folder / "buyer-events.jsonl", settings.api_key)
    shops = read_shops(args.shops)
    memory = read_memory(args.memory)
    shop, reason, evidence = choose_shop(shops, memory, args.item, args.budget)
    safe = lambda value: value.replace(settings.api_key, "[REDACTED]") if settings.api_key else value
    print(safe(f"[choose] {shop['name']} {shop['url']}: {reason}"), flush=True)
    log.record("shop_selected", name=shop["name"], url=shop["url"], item=args.item,
               budget=args.budget, reason=reason, evidence=evidence)
    client = BudgetClient(log, args.budget)
    started, result, error = time.monotonic(), {}, None
    try:
        async with asyncio.timeout(240):
            result = await negotiate(client, InformedHarness(ModelHarness(folder), evidence),
                                     shop["url"], args.item, args.budget, log)
    except (ProtocolError, HarnessError) as failure:
        error = str(failure)
    except TimeoutError:
        error = "buyer_deadline_exceeded"
    except httpx.HTTPError:
        error = "a2a_network_error"
    except Exception:
        error = "unexpected_market_buyer_error"
    finally:
        await client.close()
    stars, review = review_trade(result, error, args.budget, client.turns)
    receipt = result.get("receipt") if not error and result.get("outcome") == "purchased" else None
    outcome = ("integrity_error" if error and stars == 1 else "unavailable" if error
               else "purchased" if receipt is not None else "no_deal")
    record = remember(args.memory, shop=shop, item=args.item, budget=args.budget, outcome=outcome,
                      price=receipt["price"] if receipt is not None else None,
                      seconds=time.monotonic() - started, turns=client.turns, task_id=result.get("task_id"))
    log.record("market_result", memory=record, error=error, stars=stars, review=review)
    print(safe(json.dumps({"shop": shop["url"], "receipt": receipt,
                          "stars": stars, "review": review}, ensure_ascii=False)), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shops", required=True, type=Path)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", required=True, type=int)
    parser.add_argument("--memory", type=Path, default=ROOT / "memory" / "market-trades.jsonl")
    args = parser.parse_args()
    if not args.item.strip() or args.budget < 0:
        parser.error("Nonempty item and nonnegative budget required")
    if not args.memory.resolve().is_relative_to(ROOT):
        parser.error("Memory must stay inside this submission")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
