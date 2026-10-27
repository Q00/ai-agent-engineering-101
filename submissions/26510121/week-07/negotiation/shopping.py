"""Agent-card directory -> model plan -> concurrent bounded negotiations."""
import asyncio
import json

import httpx

from negotiation.a2a_client import A2AClient, ProtocolError, _http_url
from negotiation.buying import negotiate
from negotiation.cart_contracts import CartContext
from negotiation.cart_state import CartLedger
from negotiation.harness import HarnessError


def public_digest(shop_id, card):
    return {"shop_id": shop_id, "name": str(card.get("name", ""))[:80],
            "skills": [{"id": str(skill.get("id", ""))[:80],
                        "description": str(skill.get("description", ""))[:1200],
                        "tags": [str(tag)[:40] for tag in skill.get("tags", [])[:10]]}
                       for skill in card.get("skills", [])[:8]]}


class Shopper:
    def __init__(self, lines, total_budget, harness, log, concurrency=4, client_factory=None):
        if type(concurrency) is not int or not 1 <= concurrency <= 8:
            raise ValueError("Concurrency must be 1..8")
        self.ledger = CartLedger(lines, total_budget, log)
        self.harness, self.log = harness, log
        self.semaphore = asyncio.Semaphore(concurrency)
        self.client_factory = client_factory or (lambda: A2AClient(log, timeout=125))
        self.shops = {}
        self.plan_summary = ""

    async def discover(self, directory):
        entries = directory.get("shops", [])
        if not 1 <= len(entries) <= 128:
            raise ValueError("Directory must contain 1..128 shops")
        ids, urls = set(), set()
        for entry in entries:
            shop_id = entry.get("shop_id")
            url = _http_url(entry.get("url", "")).rstrip("/")
            if not isinstance(shop_id, str) or not shop_id.strip() or shop_id in ids or url in urls:
                raise ValueError("Duplicate or missing directory identity")
            ids.add(shop_id)
            urls.add(url)
        gate = asyncio.Semaphore(8)

        async def fetch(entry):
            async with gate:
                client = self.client_factory()
                try:
                    card = await client.discover(entry["url"])
                    digest = public_digest(entry["shop_id"], card)
                    return entry["shop_id"], {"url": entry["url"], "digest": digest}
                except (httpx.HTTPError, ProtocolError):
                    self.log.record("directory_shop_unavailable", shop_id=entry["shop_id"])
                    return entry["shop_id"], None
                finally:
                    await client.close()
        results = await asyncio.gather(*(fetch(entry) for entry in entries))
        self.shops = {key: value for key, value in results if value is not None}
        self.log.record("directory_resolved", requested=len(entries), available=len(self.shops),
                        shops=[shop["digest"] for shop in self.shops.values()])

    def planning_input(self):
        lines, relevant = [], set()
        for line in self.ledger.lines.values():
            if line.state not in {"pending", "stopped"}:
                continue
            candidates = []
            for shop_id, shop in self.shops.items():
                if shop_id in line.attempted_shops:
                    continue
                if any(line.category in skill["tags"] for skill in shop["digest"]["skills"]):
                    candidates.append({"shop_id": shop_id})
            candidates = candidates[:4]
            relevant.update(row["shop_id"] for row in candidates)
            lines.append({"line_id": line.line_id, "item_id": line.item_id, "description": line.description,
                          "shops": candidates})
        context = CartContext(self.ledger.available, lines)
        observation = {"cards_untrusted": [shop["digest"] for key, shop in self.shops.items() if key in relevant],
                       "plan_summary_untrusted": self.plan_summary,
                       "previous_attempts_untrusted": [{"line_id": line.line_id, "state": line.state,
                                                        "summary": line.summary}
                                                       for line in self.ledger.lines.values()]}
        self.log.record("cart_planning_context", line_count=len(lines), card_count=len(relevant),
                        observation_chars=len(json.dumps(observation, ensure_ascii=False)))
        return context, observation

    async def trade_line(self, line_id):
        async with self.semaphore:
            await self.ledger.begin(line_id)
            line = self.ledger.lines[line_id]
            client = self.client_factory()
            async def progress(memory):
                await self.ledger.remember(line_id, memory)
            try:
                async with asyncio.timeout(180):
                    result = await negotiate(client, self.harness, self.shops[line.shop_id]["url"],
                                             line.item_id, line.cap, self.log,
                                             request_text=line.description, progress=progress)
                await self.ledger.finish(line_id, result)
            except asyncio.CancelledError:
                await self.ledger.uncertain(line_id, "negotiation_interrupted")
                raise
            except (ProtocolError, HarnessError) as error:
                await self.ledger.uncertain(line_id, str(error))
            except (httpx.HTTPError, TimeoutError):
                await self.ledger.uncertain(line_id, "network_or_deadline_unknown_outcome")
            except Exception:
                await self.ledger.uncertain(line_id, "unexpected_negotiation_error")
            finally:
                await client.close()

    async def run(self, directory):
        self.ledger.record("cart_start")
        await self.discover(directory)
        for wave in range(1, 4):
            if any(line.state == "uncertain" for line in self.ledger.lines.values()):
                break
            context, observation = self.planning_input()
            if not context.lines:
                break
            decision = await self.harness.cart(context, observation)
            self.plan_summary = decision.summary
            self.log.record("cart_plan", wave=wave, decision=decision.model_dump())
            if decision.action == "stop":
                break
            await self.ledger.reserve(decision)
            await asyncio.gather(*(self.trade_line(row.line_id) for row in decision.assignments))
        result = self.ledger.result()
        self.log.record("cart_result", **result)
        return result
