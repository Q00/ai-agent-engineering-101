"""The slow loop: public-ledger memory, forum posts and model-written policy versions.

A separate process beside the kit. Every 30 s it pulls the public ledger forward and
reads our own payments; every 5 minutes it summarises the market and our results,
asks the model (Haiku 5.5, through the same quota ledger as the trading harness) for
bounded policy changes, validates them and publishes a new policy version that the
seller, buyer and restock hook read on their next decision. Without a model key it
still collects, posts and keeps the current policy. It never touches keys, budgets,
settlement or the market's own files.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final

import anyio
import httpx
import typer
from openai import APIError, AsyncOpenAI
from openai.types.chat import ChatCompletionFunctionToolParam
from openai.types.shared_params import FunctionDefinition
from pydantic import Field, JsonValue, ValidationError

from market_a2a.anthropic_fallback import ANTHROPIC_URL, HAIKU_MODEL
from market_a2a.models import FrozenModel, HarnessError
from market_a2a.paid_fallback import DailyQuota
from r3.events import EventLedger, events_path
from r3.forum import STATE_FILE as FORUM_STATE
from r3.forum import maybe_post
from r3.ledger_db import LedgerDB, ledger_path, pull
from r3.market_files import read_catalog, read_inventory, read_payments
from r3.policy import Policy, apply_changes, load_policy, policy_path, save_policy
from r3.review import DEALS_FILE

if TYPE_CHECKING:
    from collections.abc import Mapping

    from openai.types.chat import ChatCompletionMessageParam

LOGGER: Final = logging.getLogger("r3.manager")
PULL_EVERY: Final = 30.0
REVIEW_EVERY: Final = 300.0
MAX_CHANGES: Final = 6
PROMPT: Final = (
    "You manage the trading policy of one shop and one buyer agent in a simulated "
    "market. Shops rank by profit, revenue and stars; buyers by surplus (list price "
    "minus price paid, zero for broken or expired goods), forum reputation and deal "
    "count. All ranks are percentiles. You see a compact summary of the public "
    "ledger, our own results and the current policy with field bounds. Propose at "
    "most six bounded changes that raise expected profit and surplus without "
    "risking the cash reserve, or propose none. Rules: never collude, never post "
    "rumours, keep grade claims honest, keep certificates attached, never spend "
    "below the reserve. Use the set_policy tool once."
)


def policy_schema() -> dict[str, object]:
    """Tool schema: a flat map of ``section.field`` to value plus a reason."""
    fields: dict[str, object] = {}
    for section_name, model in (
        ("seller", Policy().seller),
        ("buyer", Policy().buyer),
        ("restock", Policy().restock),
    ):
        for name, info in type(model).model_fields.items():
            annotation = info.annotation
            kind = "number"
            if annotation is int:
                kind = "integer"
            elif annotation is bool:
                kind = "boolean"
            elif annotation is not float:
                continue
            fields[f"{section_name}.{name}"] = {"type": kind}
    return {
        "type": "object",
        "properties": {
            "changes": {
                "type": "object",
                "properties": fields,
                "additionalProperties": False,
            },
            "reason": {"type": "string", "maxLength": 300},
        },
        "required": ["changes", "reason"],
        "additionalProperties": False,
    }


class Proposal(FrozenModel):
    """The only shape the policy tool accepts: a flat change map and a reason."""

    changes: dict[str, JsonValue] = Field(default_factory=dict, max_length=MAX_CHANGES)
    reason: str = Field(default="", max_length=300)


class Manager:
    """Owns the ledger mirror, the events ledger and the policy file."""

    def __init__(
        self,
        run_dir: Path,
        market: str,
        *,
        shop_url: str,
        buyer_name: str,
        shop_name: str,
    ) -> None:
        """Bind to the kit's run directory."""
        self.run_dir: Path = run_dir
        self.market_dir: Path = run_dir / ".market"
        self.market: str = market.rstrip("/")
        self.shop_url: str = shop_url.rstrip("/")
        self.buyer_name: str = buyer_name
        self.shop_name: str = shop_name
        self.ledger: LedgerDB = LedgerDB(ledger_path(self.market_dir))
        self.events: EventLedger = EventLedger(events_path(self.market_dir))
        self.policy_file: Path = policy_path(self.market_dir)
        self.http: httpx.Client = httpx.Client(timeout=httpx.Timeout(20, connect=10))
        self.shop_names: dict[str, str] = {}
        self.started: float = time.time()

    def pull_ledger(self) -> int:
        """Advance the public-ledger mirror; first run starts 2,000 rows back."""
        if self.ledger.cursor == 0:
            reply = self.http.get(f"{self.market}/api/ledger", params={"after": -2000})
            if reply.status_code == 200:  # noqa: PLR2004 - HTTP OK
                rows = reply.json()
                if isinstance(rows, list):
                    _ = self.ledger.ingest(r for r in rows if isinstance(r, dict))
        return pull(self.ledger, self.market, self.http)

    def refresh_shop_names(self) -> None:
        """Map shop URLs to names for readable forum posts."""
        try:
            reply = self.http.get(f"{self.market}/api/shops")
            if reply.status_code == 200:  # noqa: PLR2004 - HTTP OK
                shops = reply.json().get("shops", [])
                self.shop_names = {
                    str(s.get("url", "")).rstrip("/"): str(s.get("name", ""))
                    for s in shops
                    if isinstance(s, dict)
                }
        except (httpx.HTTPError, ValueError):
            pass

    def summary(self) -> dict[str, object]:
        """Build the compact state the model sees."""
        catalog = read_catalog(self.market_dir)
        inventory = read_inventory(self.market_dir)
        _ = self.events.sync_payments(read_payments(self.market_dir))
        now = time.time()
        aging = {"food_under_20pct": 0, "goods_over_7d": 0}
        for unit in inventory.units:
            shelf = catalog.get(unit.sku)
            ratio = unit.shelf_left_ratio(
                now, float(shelf.shelf_hours) if shelf and shelf.shelf_hours else None
            )
            if ratio is not None and ratio < 0.2:  # noqa: PLR2004 - policy default
                aging["food_under_20pct"] += 1
            if unit.kind == "goods" and now - unit.supplied_at > 7 * 86400:
                aging["goods_over_7d"] += 1
        return {
            "hours_running": round((now - self.started) / 3600, 2),
            "shop": {
                "balance": inventory.balance,
                "units": len(inventory.units),
                "stock_cost": sum(u.cost for u in inventory.units),
                "categories": len({u.category for u in inventory.units}),
                "aging": aging,
            },
            "own_results_24h": self.events.summary(24),
            "market_6h": self.ledger.summary(
                catalog, hours=6, our_shop=self.shop_url, our_buyer=self.buyer_name
            ),
            "policy": load_policy(self.policy_file).model_dump(),
        }

    async def ask_model(  # noqa: PLR0911 - every refusal path keeps the policy
        self, summary: Mapping[str, object]
    ) -> Policy | None:
        """One bounded tool call to Haiku; any failure leaves the policy unchanged."""
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key or os.environ.get("AX_R3_MANAGER_MODEL", "1") != "1":
            return None
        quota_path = os.environ.get("AX_PAID_QUOTA_PATH")
        if quota_path:
            try:
                _ = DailyQuota(
                    Path(quota_path), int(os.environ.get("AX_PAID_DAILY_LIMIT", "5000"))
                ).reserve()
            except HarnessError as exc:
                LOGGER.warning("manager_quota_refused %s", exc)
                return None
        tool = ChatCompletionFunctionToolParam(
            type="function",
            function=FunctionDefinition(
                name="set_policy",
                description="Propose bounded policy changes (or an empty map).",
                parameters=policy_schema(),
            ),
        )
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": PROMPT},
            {
                "role": "user",
                "content": json.dumps(summary, ensure_ascii=False)[:60_000],
            },
        ]
        async with AsyncOpenAI(
            base_url=ANTHROPIC_URL, api_key=key, max_retries=0
        ) as client:
            try:
                with anyio.fail_after(60):
                    completion = await client.chat.completions.create(
                        model=os.environ.get("ANTHROPIC_MODEL", HAIKU_MODEL),
                        messages=messages,
                        tools=[tool],
                        tool_choice="required",
                        max_completion_tokens=700,
                    )
            except (APIError, TimeoutError, ValueError, OSError) as exc:
                LOGGER.warning("manager_model_failed %s", type(exc).__name__)
                return None
        calls = completion.choices[0].message.tool_calls or []
        for call in calls:
            if call.type != "function" or call.function.name != "set_policy":
                continue
            try:
                proposal = Proposal.model_validate_json(call.function.arguments)
                changes = proposal.changes
                reason = proposal.reason
                candidate = apply_changes(load_policy(self.policy_file), changes)
            except (ValueError, ValidationError, TypeError, AttributeError) as exc:
                LOGGER.warning("manager_invalid_proposal %s", type(exc).__name__)
                return None
            if not changes:
                LOGGER.info("manager_no_change reason=%s", reason)
                return None
            published = save_policy(self.policy_file, candidate, f"model: {reason}")
            LOGGER.info(
                "manager_policy_v%s changes=%s reason=%s",
                published.version,
                json.dumps(changes),
                reason,
            )
            return published
        return None

    def post_forum(self) -> list[str]:
        """Queue buyer/shop posts when their intervals allow."""
        inventory = read_inventory(self.market_dir)
        policy = load_policy(self.policy_file)
        market = self.ledger.summary(read_catalog(self.market_dir), hours=1)
        top = market.get("top_categories")
        line = ""
        if isinstance(top, list) and top:
            first = top[0]
            if isinstance(first, dict):
                line = (
                    f"Market last hour: {market.get('settles')} settles; busiest "
                    f"{str(first.get('category', '')).split('>')[-1]} "
                    f"({first.get('settles')} settles, median "
                    f"{first.get('median_price_to_list')} of list)."
                )
        return maybe_post(
            self.market_dir,
            self.run_dir / DEALS_FILE,
            self.run_dir / FORUM_STATE,
            inventory=inventory,
            shop_name=self.shop_name,
            warranty_a=policy.seller.warranty_a,
            shop_names=self.shop_names,
            market_line=line,
        )

    async def loop(self) -> None:
        """Run until stopped; every step is guarded so one failure skips one tick."""
        last_review = 0.0
        self.refresh_shop_names()
        while True:
            try:
                added = await anyio.to_thread.run_sync(self.pull_ledger)
                if added:
                    LOGGER.info(
                        "ledger_rows_added=%s cursor=%s", added, self.ledger.cursor
                    )
                if time.time() - last_review >= REVIEW_EVERY:
                    last_review = time.time()
                    await anyio.to_thread.run_sync(self.refresh_shop_names)
                    posted = await anyio.to_thread.run_sync(self.post_forum)
                    if posted:
                        LOGGER.info("forum_queued=%s", ",".join(posted))
                    summary = await anyio.to_thread.run_sync(self.summary)
                    LOGGER.info(
                        "summary=%s", json.dumps(summary, ensure_ascii=False)[:2000]
                    )
                    _ = await self.ask_model(summary)
            except Exception as exc:  # noqa: BLE001 - one bad tick must never end the loop
                LOGGER.warning("manager_tick_failed %s: %s", type(exc).__name__, exc)
            await anyio.sleep(PULL_EVERY)


def main(  # noqa: PLR0913 - CLI options
    run_dir: Annotated[Path, typer.Option("--dir")],
    market: Annotated[str, typer.Option()] = "https://market.ourolabs.ai",
    shop_url: Annotated[str, typer.Option()] = "",
    buyer_name: Annotated[str, typer.Option()] = "25620027",
    shop_name: Annotated[str, typer.Option()] = "Sungjin Shop",
    *,
    once: Annotated[bool, typer.Option()] = False,
) -> None:
    """Public GET only against the market; policy and posts go through local files."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    manager = Manager(
        run_dir.resolve(),
        market,
        shop_url=shop_url,
        buyer_name=buyer_name,
        shop_name=shop_name,
    )
    if once:
        added = manager.pull_ledger()
        manager.refresh_shop_names()
        typer.echo(
            json.dumps(
                {
                    "ledger_rows_added": added,
                    "cursor": manager.ledger.cursor,
                    "posted": manager.post_forum(),
                    "summary": manager.summary(),
                },
                ensure_ascii=False,
            )[:4000]
        )
        return
    anyio.run(manager.loop)


if __name__ == "__main__":
    typer.run(main)
