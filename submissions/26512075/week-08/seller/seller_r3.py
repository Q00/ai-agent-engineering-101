"""Round 3 seller using the kit's local inventory snapshot.



This is a separate entrypoint.
The kit must provide MARKET_DIR and its ACP proxy. The in-process holds below
reduce duplicate offers within one running seller; they are not market stock
reservations and do not coordinate multiple seller processes.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import fcntl
import json
import logging
import math
import os
import re
import time
from dataclasses import asdict
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from market_inventory import InventoryError, InventoryUnit, available_units, parse_inventory, read_inventory
from seller_stock import StockChoice, revalidate_stock
from seller import ExistingTaskContextBuilder, _positive_int, decide, followup_decision, sign_receipt
from storage import stores
from strategy import Policy, pricing, eligible, stock_order, comps
import forum_effects
import shop_ads
from offer_validation import OfferParsingError


LOG = logging.getLogger("seller_r3")
SKU_PATTERN = re.compile(r"(?<![A-Za-z0-9])G[0-9a-fA-F]{8}(?![A-Za-z0-9])", re.I)
MAX_SNAPSHOT_AGE = 60  # seconds; fail closed if the kit stops updating
HOLD_SECONDS = 600
AFTER_ACCEPT_HOLD_SECONDS = 600  # bounded local precaution, never a market reservation

def read_fresh_stock(market_dir: Path, *, now: float | None = None) -> list[InventoryUnit]:
    """Require a recent, explicitly Round 3 snapshot; never fall back to old products."""
    now = time.time() if now is None else now
    path = market_dir / "inventory.json"
    try:
        age = now - path.stat().st_mtime
    except OSError as exc:
        raise InventoryError(f"cannot stat {path}: {exc}") from exc
    if age < -5 or age > MAX_SNAPSHOT_AGE:
        raise InventoryError(f"inventory snapshot age {age:.1f}s is outside the accepted range")
    data = read_inventory(market_dir)
    round_number = data.get("round")
    if isinstance(round_number, bool) or not isinstance(round_number, int) or round_number < 3:
        raise InventoryError(f"Round 3 inventory required; got round={round_number!r}")
    units = available_units(parse_inventory(data), now=now)
    food = [u for u in units if u.kind == "food"]
    if food:
        catalog = json.loads((market_dir / "catalog.json").read_text())
        for u in food:
            shelf = catalog.get(u.sku, {}).get("shelf_hours")
            if type(shelf) not in (int, float) or not math.isfinite(shelf) or shelf <= 0:
                raise InventoryError("food needs official catalog shelf_hours to recheck grade")
            u.shelf_hours = shelf
            fraction = (u.expires_at - now) / (shelf * 3600)
            u.grade = "A" if fraction >= .8 else "B" if fraction >= .45 else "C"
    return [u for u in units if u.grade.upper() in {"A", "B", "C"}]


def matching_units(text: str, units: list[InventoryUnit]) -> list[InventoryUnit]:
    """Use one explicit SKU, or an exact name/category request; never guess from fragments.

    Category requests cannot reveal the buyer's acceptable-SKU list. The buyer
    and market still validate the chosen SKU during checkout.
    """
    query = text.strip().casefold().rstrip(".!? ")
    explicit = {m.group(0).casefold() for m in SKU_PATTERN.finditer(text)}
    if len(explicit) > 1:
        return []
    if explicit:
        matches = [u for u in units if u.sku.casefold() in explicit]
    else:
        # Preserve Korean names and punctuation while removing only common whole prefixes.
        query = re.sub(r"^(?:i want to buy|i want|i would like to buy|buy|want)\s+(?:a\s+|an\s+)?", "", query)
        matches = [u for u in units if query in {
            u.name.strip().casefold(),
            (u.category or "").strip().casefold(),
            (u.category or "").rsplit(">", 1)[-1].strip().casefold(),
        } and query]
        # A leaf name may occur under different full categories. Refuse to guess.
        if len({u.category for u in matches}) > 1:
            return []
    return sorted(matches, key=lambda u: (
        u.expires_at if u.expires_at is not None else float("inf"),
        u.supplied_at if u.supplied_at is not None else float("inf"), u.unit,
    ))


class Round3Seller(AgentExecutor):
    def __init__(self, deal_store, name: str, market_dir: Path):
        self.policy = Policy.load()
        self.deals = deal_store
        self.name = name
        self.market_dir = market_dir
        self.lock = asyncio.Lock()
        self.task_ads = {}
        self.holds: dict[str, tuple[str, float]] = {}  # unit -> (task, monotonic expiry)

    def _validate_ad_price(self, ad, reserve):
        # The approved price is fixed; storage accrual may change the live floor.
        if ad["price"] < reserve or ad["warranty_hours"] != 0:
            raise InventoryError(
                "advertised price below current floor or invalid warranty: "
                f"sku={ad['sku']} ad_floor={ad['floor']} "
                f"current_floor={reserve} ad_price={ad['price']} "
                f"warranty_hours={ad['warranty_hours']}"
            )

    def _validate_ad(self, ad, units, now, *, available=False):
        if (ad.get("as") != "shop" or ad.get("id") != shop_ads.digest(ad)
                or type(ad.get("price")) is not int or ad["price"] <= 0 or ad.get("qty") != 1):
            raise InventoryError("invalid advertisement approval")
        shop_ads.validate_reference(self.market_dir, ad, now)
        if ad.get("post") != shop_ads.post_text(ad):
            raise InventoryError("advertisement text changed")
        if not ad["checked_at"] <= now < ad["expires_at"] or ad["expires_at"]-ad["checked_at"] > 3600:
            raise InventoryError("advertisement expired or invalid duration")
        unit = next((u for u in units if u.unit == ad["unit"]), None)
        if unit is None or unit.kind != "goods" or not eligible(unit, self.policy, now):
            raise InventoryError("advertised stock unavailable")
        owner = (unit.cert or {}).get("cert", {}).get("shop")
        terms = pricing(unit, self.policy, now)
        if (unit.sku, unit.grade, unit.cost, owner) != (ad["sku"], ad["grade"], ad["cost"], ad["shop"]):
            raise InventoryError("advertised unit conditions changed")
        self._validate_ad_price(ad, terms["reserve"])
        if available and (unit.unit in self.holds or unit.unit in shop_ads.issued_units(self.market_dir)):
            raise InventoryError("advertised unit reserved or receipt already issued")
        return unit

    async def advertisement(self, request):
        async with self.lock:
            self._prune_holds()
            now = time.time()
            units = read_fresh_stock(self.market_dir)
            action = request["action"]
            if action == "preview":
                price, ttl = request.get("price"), request.get("ttl", 1800)
                if (price is not None and (type(price) is not int or price <= 0)) or type(ttl) is not int or not 1 <= ttl <= 3600:
                    raise ValueError("positive integer price and TTL 1..3600 seconds required")
                unit = next((u for u in units if u.unit == request["unit"]), None)
                if unit is None:
                    raise InventoryError("unit unavailable")
                if price is None:
                    price = pricing(unit, self.policy, now)["target_price"]
                ad = {"as":"shop", "market":request["market"], "shop":request["shop"],
                      "unit":unit.unit, "sku":unit.sku, "name":" ".join(unit.name.split()),
                      "grade":unit.grade, "cost":unit.cost, "floor":pricing(unit,self.policy,now)["reserve"],
                      "price":price, "qty":1, "warranty_hours":0, "checked_at":int(now),
                      "expires_at":int(now)+ttl, "refs":[]}
                reference = shop_ads.ledger_reference(self.market_dir, ad["market"],
                                                       unit.sku, unit.grade, request.get("seq"), now)
                ad.update(format_version=3, reference=reference,
                          refs=[reference["seq"]] if reference else [])
                ad.update(shop_ads.limited_offer_fields(ad))
                ad["post"] = shop_ads.post_text(ad)
                ad["id"] = shop_ads.digest(ad)
            else:
                ad = request["entry"]
            self._validate_ad(ad, units, now, available=True)
            if action == "activate":
                for old in shop_ads.active_ads(self.market_dir):
                    if old["unit"] == ad["unit"] and old["id"] != ad["id"] and old["expires_at"] > now:
                        raise InventoryError("another advertisement is active for this unit")
                with shop_ads.database(self.market_dir) as db:
                    db.execute("INSERT OR IGNORE INTO ads VALUES (?,?,?)", (ad["id"],json.dumps(ad),now))
            elif action not in ("preview", "validate"):
                raise ValueError("unknown advertisement action")
            return ad

    async def _ad_client(self, reader, writer):
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=5)
            if len(raw) > 65536:
                raise ValueError("request too large")
            result = await self.advertisement(json.loads(raw))
            reply = {"result":result}
        except Exception as exc:
            reply = {"error":str(exc)}
        writer.write(json.dumps(reply, ensure_ascii=False).encode()+b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    def _publish_availability(self) -> None:
        # Local measurement only. Market checkout remains authoritative for stock.
        now, monotonic = time.time(), time.monotonic()
        value = {"min_food_hours": self.policy.min_food_hours,
                 "allowed_grades": list(self.policy.allowed_grades),
                 "holds": {unit: now + max(0, expiry-monotonic) for unit, (_, expiry) in self.holds.items()}}
        path = self.market_dir / "seller-availability.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value))
        os.replace(temporary, path)

    def _prune_holds(self) -> None:
        now = time.monotonic()
        for unit_id, (held_task, expires) in list(self.holds.items()):
            if expires <= now:
                self.holds.pop(unit_id, None)
                self.task_ads.pop(held_task, None)

    async def _reserve(self, task_id: str, text: str, constraints=None) -> InventoryUnit | None:
        async with self.lock:
            self._prune_holds()
            units = read_fresh_stock(self.market_dir)
            constraints = constraints or {}
            now = time.time()
            matches = matching_units(text, units)
            issued = shop_ads.issued_units(self.market_dir)
            ads = [a for a in shop_ads.active_ads(self.market_dir) if a["expires_at"] > now
                   and a["sku"] in {u.sku for u in matches}]
            if ads:
                # Do not switch a shopper to a different unit/price when the advertised unit is held.
                for ad in ads:
                    unit = self._validate_ad(ad, units, now)
                    if unit.unit not in self.holds and unit.unit not in issued and eligible(unit,self.policy,now,
                            constraints.get("min_hours_left"),constraints.get("grades")):
                        self.holds[unit.unit] = (task_id,time.monotonic()+HOLD_SECONDS)
                        self.task_ads[task_id] = ad
                        self._publish_availability()
                        return unit
                return None
            for unit in sorted(matches, key=stock_order):
                if unit.unit not in self.holds and unit.unit not in issued and eligible(unit, self.policy, now,
                        constraints.get("min_hours_left"), constraints.get("grades")):
                    self.holds[unit.unit] = (task_id, time.monotonic() + HOLD_SECONDS)
                    self._publish_availability()
                    return unit
        return None

    async def _release(self, task_id: str, unit_id: str, *, after_accept: bool = False) -> None:
        async with self.lock:
            hold = self.holds.get(unit_id)
            if hold and hold[0] == task_id:
                if after_accept:
                    self.holds[unit_id] = (task_id, time.monotonic() + AFTER_ACCEPT_HOLD_SECONDS)
                else:
                    self.holds.pop(unit_id, None)
                    self.task_ads.pop(task_id, None)
                self._publish_availability()

    async def _check_unit(self, task_id: str, deal: dict) -> InventoryUnit:
        async with self.lock:
            self._prune_holds()
            hold = self.holds.get(deal["unit"])
            if not hold or hold[0] != task_id:
                raise InventoryError("local hold expired or seller restarted")
            # Check timestamp as an additional conservative local safeguard, then
            # re-read and validate identity and cost using the shared stock helper.
            fresh = read_fresh_stock(self.market_dir)
            original = InventoryUnit(**deal["stock"])
            revalidate_stock(self.market_dir, StockChoice(original, deal["reserve"]))
            current = next((u for u in fresh if u.unit == original.unit), None)
            constraints = deal.get("constraints", {})
            if current is None or not eligible(current, self.policy, time.time(),
                    constraints.get("min_hours_left"), constraints.get("grades")):
                raise InventoryError("stock no longer meets grade or shelf-life requirements")
            ad = deal.get("advertisement") or self.task_ads.get(task_id)
            if ad:
                self._validate_ad(ad, fresh, time.time())
            return current

    async def execute(self, context, event_queue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        text = get_message_text(context.message) or ""
        deal = await self.deals.get(task.id)
        say = lambda value: updater.new_agent_message(parts=[new_text_part(value)])

        if deal is None:
            unit = None
            try:
                metadata = getattr(context.message, "metadata", None) or {}
                constraints = metadata.get("accept", {})
                if not isinstance(constraints, dict):
                    raise ValueError("invalid acceptance constraints")
                if "grades" in constraints and (not isinstance(constraints["grades"], list) or
                        not constraints["grades"] or any(g not in ("A", "B", "C") for g in constraints["grades"])):
                    raise ValueError("invalid grade constraints")
                for key in ("min_hours_left", "warranty_hours"):
                    value = constraints.get(key, 0)
                    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                        raise ValueError("invalid acceptance constraint")
                if constraints.get("warranty_hours", 0) > 0:
                    await updater.reject(say("보증 없음 / No warranty (0 hours); requested warranty unavailable."))
                    return
                unit = await self._reserve(task.id, text, constraints)
                if unit is None:
                    await updater.reject(say("No matching in-stock item is available."))
                    return
                terms = pricing(unit, self.policy, time.time())
                reserve = terms["reserve"]
                terms["comps"] = comps(self.market_dir, unit)
                ad = self.task_ads.get(task.id)
                if ad:
                    ask = ad["price"]
                elif terms["pricing_mode"] in ("clearance", "aged_floor", "turnover"):
                    ask = reserve
                else:
                    choice = await decide({"side": "seller", "item": unit.name, "itemId": unit.sku,
                                           "limit": reserve, "listPrice": unit.list_price, **terms, "transcript": [text]})
                    if choice.get("action") != "offer":
                        raise ValueError("opening decision must be an offer")
                    ask = max(reserve, _positive_int(choice.get("price")))
                checked = await self._check_unit(task.id, {"unit": unit.unit, "stock": asdict(unit), "reserve": reserve, "constraints": constraints})
                if checked.grade != unit.grade:
                    raise InventoryError("grade changed while preparing quote")
                unit = checked
                terms = pricing(unit, self.policy, time.time())
                terms["comps"] = comps(self.market_dir, unit)
                reserve = terms["reserve"]
                if ad:
                    self._validate_ad_price(ad, reserve)
                ask = ad["price"] if ad else max(ask, reserve)
                deal = {"unit": unit.unit, "sku": unit.sku, "cost": unit.cost, "stock": asdict(unit),
                        "reserve": reserve, "ask": ask, "advertisement": ad, "constraints": constraints, "terms": terms, "turn": 0, "transcript": [f"buyer: {text}"]}
                await self.deals.save(task.id, deal)
                try:
                    forum_effects.quote(self.market_dir, unit.sku, ask)
                except Exception:
                    LOG.exception("quote observation unavailable")
                await updater.requires_input(updater.new_agent_message(
                    parts=[new_text_part(f"{unit.name}: asking {ask}. 등급 {unit.grade} · 재고 1개 · 보증 없음 / No warranty (0 hours).")],
                    metadata={"ask": ask, "itemId": unit.sku, "unit": unit.unit, "cert": unit.cert,
                              "grade": unit.grade, "remaining_shelf_life_hours": terms["remaining_shelf_life_hours"],
                              "offered_warranty_hours": 0,
                              "advertisement_id": ad["id"] if ad else None,
                              "price_valid_until": ad["expires_at"] if ad else None}))
                LOG.info("open task=%s sku=%s unit=%s ask=%s", task.id, unit.sku, unit.unit, ask)
            except Exception:
                LOG.exception("opening failed for task=%s", task.id)
                if unit is not None:
                    await self._release(task.id, unit.unit)
                await self.deals.delete(task.id)
                await updater.failed(say("Seller could not confirm current inventory."))
            return

        unit_id = deal.get("unit")
        try:
            unit = await self._check_unit(task.id, deal)
            terms = pricing(unit, self.policy, time.time())
            terms["comps"] = comps(self.market_dir, unit)
            deal["reserve"] = terms["reserve"]
            ad = deal.get("advertisement")
            if ad:
                self._validate_ad_price(ad, terms["reserve"])
                # Explicit number only; never let a model reinterpret fixed-price acceptance.
                normalized = text.strip()
                offer = int(normalized.replace(",", "")) if re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", normalized) else None
                choice = {"action": "clarify" if offer is None or offer <= 0 else
                          "accept" if offer >= ad["price"] else "offer", "price": ad["price"]}
            else:
                choice, offer = await followup_decision(
                    {"side": "seller", "item": unit.name, "itemId": unit.sku,
                     "taskId": task.id, "seenBuyerOffers": deal.get("seen_buyer_offers", []),
                     "limit": deal["reserve"], "currentAsk": deal["ask"], "incomingText": text,
                     "turn": deal["turn"], **terms, "transcript": deal["transcript"][-6:]},
                    text, deal["reserve"], deal["ask"],
                )
            if type(offer) is int and offer > 0:
                seen = deal.setdefault("seen_buyer_offers", [])
                if offer not in seen:
                    seen.append(offer)
            LOG.info("negotiation task=%s sku=%s unit=%s turn=%s offer=%s previous_ask=%s "
                     "action=%s price=%s reserve=%s reason=%s",
                     task.id, unit.sku, unit.unit, deal["turn"] + 1, offer, deal["ask"],
                     choice.get("action"), choice.get("price"), deal["reserve"],
                     choice.get("reason", "fixed_ad" if ad else "decision"))
            deal["turn"] += 1
            deal["transcript"].append(f"buyer: {text}")
            if choice.get("action") == "clarify":
                deal["ask"] = max(deal["ask"], deal["reserve"])
                clarification = (f"현재 호가 / Current ask: {max(deal['ask'], deal['reserve']):,} credits. "
                                 f"등급 {unit.grade} · 보증 없음(0시간). "
                                 f"구매하시려면 {deal['ask']} 또는 제안 금액을 양의 정수로 보내주세요. "
                                 "Please state your offer as a positive whole-number price.")
                deal["transcript"].append(f"seller: {clarification}")
                await self.deals.save(task.id, deal)
                await updater.requires_input(updater.new_agent_message(
                    parts=[new_text_part(clarification)],
                    metadata={"ask": deal["ask"], "itemId": unit.sku, "reason": "missing_buyer_offer"}))
                LOG.info("clarification requested task=%s reason=missing_buyer_offer", task.id)
            elif choice.get("action") == "accept":
                _positive_int(offer)
                price = _positive_int(choice.get("price"))
                if (price != offer and not (ad and price == ad["price"] and offer >= price)) or price < deal["reserve"]:
                    raise ValueError("acceptance must equal the buyer offer and meet the reserve")
                final_unit = await self._check_unit(task.id, deal)  # model execution can take time
                final_floor = pricing(final_unit, self.policy, time.time())["reserve"]
                if ad:
                    self._validate_ad_price(ad, final_floor)
                if final_unit.grade != unit.grade or price < final_floor:
                    raise InventoryError("grade or price floor changed during negotiation; request a fresh quote")
                unit = final_unit
                receipt = sign_receipt({"shop": self.name, "item": unit.sku,
                                        "price": price, "task": task.id, "unit": unit.unit})
                if ad:
                    shop_ads.issued(self.market_dir, ad, task.id, receipt)
                await updater.add_artifact(parts=[new_data_part(receipt)], name="receipt")
                await updater.complete(say(f"Deal: {unit.name} at {price}."))
                await self.deals.delete(task.id)
                await self._release(task.id, unit.unit, after_accept=True)
                LOG.info("negotiated task=%s sku=%s unit=%s price=%s", task.id, unit.sku, unit.unit, price)
            elif choice.get("action") == "offer":
                ask = ad["price"] if ad else max(deal["reserve"], _positive_int(choice.get("price")))
                deal["ask"] = ask
                deal["transcript"].append(f"seller: {ask}")
                await self.deals.save(task.id, deal)
                try:
                    forum_effects.quote(self.market_dir, unit.sku, ask)
                except Exception:
                    LOG.exception("quote observation unavailable")
                await updater.requires_input(updater.new_agent_message(
                    parts=[new_text_part(f"Counteroffer: {ask}. 보증 없음 / No warranty (0 hours).")], metadata={"ask": ask, "itemId": unit.sku, "unit": unit.unit, "cert": unit.cert,
                              "grade": unit.grade, "remaining_shelf_life_hours": terms["remaining_shelf_life_hours"],
                              "offered_warranty_hours": 0,
                              "advertisement_id": ad["id"] if ad else None,
                              "price_valid_until": ad["expires_at"] if ad else None}))
            elif choice.get("action") in ("reject", "walk_away"):
                await updater.reject(say("No deal."))
                await self.deals.delete(task.id)
                await self._release(task.id, unit.unit)
            else:
                raise ValueError("unknown seller action")
        except Exception as exc:
            LOG.exception("follow-up failed for task=%s category=%s", task.id,
                          "offer_parsing" if isinstance(exc, OfferParsingError) else
                          "inventory" if isinstance(exc, InventoryError) else "negotiation")
            if unit_id:
                await self._release(task.id, unit_id)
            await self.deals.delete(task.id)
            message = ("Seller could not parse or validate the buyer offer. Please start a new negotiation with an explicit price."
                       if isinstance(exc, OfferParsingError) else
                       "Seller could not confirm current inventory." if isinstance(exc, InventoryError) else
                       "Seller could not complete the negotiation.")
            await updater.failed(say(message))

    async def cancel(self, context, event_queue) -> None:
        task = context.current_task
        if task is None:
            return
        deal = await self.deals.get(task.id)
        if deal and deal.get("unit"):
            await self._release(task.id, deal["unit"])
        await self.deals.delete(task.id)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build_app(port: int, public_url: str | None = None) -> Starlette:
    market_dir = Path(os.environ.get("MARKET_DIR", ".market"))
    url = public_url or f"http://127.0.0.1:{port}/"  # the kit rewrites this through its public proxy
    card = AgentCard(name="Catalog Shop", description="Round 3 B-first seller. 보증 없음 / No warranty (0 hours).",
                     version="1.0.0", default_input_modes=["text/plain"],
                     default_output_modes=["application/json", "text/plain"],
                     capabilities=AgentCapabilities(streaming=False),
                     supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
                     skills=[AgentSkill(id="negotiate", name="Negotiate available stock",
                                        description="Negotiates available market stock",
                                        tags=["negotiation"], examples=["I want to buy G000f6cda"])])
    task_store, deal_store = stores()
    executor = Round3Seller(deal_store, card.name, market_dir)
    @asynccontextmanager
    async def lifespan(app):
        market_dir.mkdir(parents=True, exist_ok=True)
        # A second process cannot steal the live seller's socket and memory-held reservations.
        with (market_dir / "seller-ads.lock").open("a") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            path = market_dir / shop_ads.SOCKET
            path.unlink(missing_ok=True)
            server = await asyncio.start_unix_server(executor._ad_client, path=str(path))
            os.chmod(path, 0o600)
            try:
                yield
            finally:
                server.close()
                await server.wait_closed()
                path.unlink(missing_ok=True)
    handler = DefaultRequestHandler(agent_executor=executor,
                                    task_store=task_store, agent_card=card,
                                    request_context_builder=ExistingTaskContextBuilder(task_store))
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"), lifespan=lifespan)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9999)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(build_app(args.port), host="127.0.0.1", port=args.port, log_level="warning")
