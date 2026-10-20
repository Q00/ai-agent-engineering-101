"""Market buyer: personal evidence, cautious reviews and bounded exploration."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time

import httpx

from negotiation.a2a_client import A2AClient, ProtocolError
from negotiation.buying import negotiate
from negotiation.config import ROOT, load_settings
from negotiation.harness import HarnessError, ModelHarness
from negotiation.market import choose_shop, observation_count, read_memory, read_shops, remember, review_trade
from negotiation.receipts import verify_receipt
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


def validate_market_receipt(receipt, shop, memory, registry_path, *, required):
    if any(r.get("task_id") == receipt.get("task") and r.get("outcome") == "purchased" for r in memory):
        raise ProtocolError("receipt_task_reused")
    registry = json.loads(Path(registry_path).read_text(encoding="utf-8")) if registry_path else {}
    registered = registry.get(shop["url"].rstrip("/"))
    if not registered or registered.get("name") != shop["name"]:
        if required:
            raise ProtocolError("receipt_registered_key_unavailable")
        return None
    if not receipt.get("sig_seller") and not required:
        return None
    if not verify_receipt(receipt, registered["pubkey"]):
        raise ProtocolError("receipt_signature_invalid")
    return True


async def run(args):
    settings = load_settings()
    folder = ROOT / "logs" / "market-buyer" / datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S.%fZ")
    log = RunLog(folder / "buyer-events.jsonl", settings.api_key)
    shops = read_shops(args.shops, exclude_name=args.self_name)
    memory = read_memory(args.memory, ROOT / "memory" / "market-verification.jsonl")
    require_verified = int(os.environ.get("MARKET_ROUND", "0")) >= 2
    shop, reason, evidence = choose_shop(shops, memory, args.item, args.budget, require_verified=require_verified)
    safe = lambda value: value.replace(settings.api_key, "[REDACTED]") if settings.api_key else value
    print(safe(f"[choose] {shop['name']} {shop['url']}: {reason}"), flush=True)
    log.record("shop_selected", name=shop["name"], url=shop["url"], item=args.item,
               budget=args.budget, reason=reason, evidence=evidence)
    client = BudgetClient(log, args.budget)
    started, result, error, signature_verified = time.monotonic(), {}, None, None
    try:
        async with asyncio.timeout(240):
            result = await negotiate(client, InformedHarness(ModelHarness(folder), evidence),
                                     shop["url"], args.item, args.budget, log)
            if result.get("outcome") == "purchased":
                signature_verified = validate_market_receipt(result["receipt"], shop, memory,
                    os.environ.get("MARKET_REGISTRY_KEYS"), required=require_verified)
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
                      seconds=time.monotonic() - started, turns=client.turns, task_id=result.get("task_id"),
                      sequence=observation_count(memory) + 1, signature_verified=signature_verified)
    log.record("market_result", memory=record, error=error, stars=stars, review=review)
    print(safe(json.dumps({"shop": shop["url"], "receipt": receipt,
                          "stars": stars, "review": review}, ensure_ascii=False)), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shops", required=True, type=Path)
    parser.add_argument("--item", required=True)
    parser.add_argument("--budget", required=True, type=int)
    parser.add_argument("--self-name", default="seochanit")
    parser.add_argument("--memory", type=Path,
                        default=Path(os.environ.get("MARKET_BUYER_MEMORY", ROOT / "memory" / "market-trades.jsonl")))
    args = parser.parse_args()
    if not args.item.strip() or args.budget < 0:
        parser.error("Nonempty item and nonnegative budget required")
    if not args.memory.resolve().is_relative_to(ROOT):
        parser.error("Memory must stay inside this submission")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
