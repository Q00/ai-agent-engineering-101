"""Execute the preregistered condition x scenario x repeat experiment matrix."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

import httpx2
from mcp import MCPError
from pydantic import ValidationError

from market.api_models import OpenedNegotiation, OpenNegotiation
from market.archive import EpisodeKey, ResultArchive, ResultRow
from market.host import HostRequest, ModelCallError, run_host_turn
from market.models import Condition, HostPolicy, Role, Scenario, Status

if TYPE_CHECKING:
    from openai import AsyncOpenAI

    from market.admin_client import AdminClient
    from market.settings import ExperimentConfig


class ExperimentHaltedError(RuntimeError):
    """A crashed episode was archived and the batch should stop safely."""


@dataclass(frozen=True, slots=True)
class RunnerContext:
    """Long-lived collaborators shared by every episode."""

    config: ExperimentConfig
    scenarios: tuple[Scenario, ...]
    admin: AdminClient
    model: AsyncOpenAI
    archive: ResultArchive
    server_base: str
    host_policy: HostPolicy = HostPolicy.BASELINE


class ExperimentRunner:
    """Resume completed keys and archive one row per attempted episode."""

    def __init__(self, context: RunnerContext) -> None:
        self._context: RunnerContext = context

    async def run(self, conditions: tuple[Condition, ...]) -> None:
        """Run every missing key in stable condition/repeat/scenario order."""
        for condition in conditions:
            for repeat in range(1, self._context.config.repetitions + 1):
                for scenario in self._context.scenarios:
                    await self._episode(condition, repeat, scenario)

    async def _episode(
        self,
        condition: Condition,
        repeat: int,
        scenario: Scenario,
    ) -> None:
        run_name = f"{condition.value}-{repeat:02d}"
        key = EpisodeKey(run=run_name, condition=condition, scenario=str(scenario.id))
        if self._context.archive.contains(key):
            return
        opened = await self._context.admin.open(
            OpenNegotiation(scenario=scenario, condition=condition)
        )
        lines = [
            f"[episode] run={run_name} scenario={scenario.id} item={scenario.item}",
        ]
        model_calls = 0
        try:
            for host_index in range(1, self._context.config.max_turns + 1):
                summary = await self._context.admin.summary(opened.negotiation_id)
                if summary.status is not Status.OPEN:
                    break
                role = summary.turn
                request = HostRequest(
                    server_url=f"{self._context.server_base}/mcp",
                    token=self._token(opened, role),
                    negotiation_id=opened.negotiation_id,
                    role=role,
                    scenario=scenario,
                    policy=self._context.host_policy,
                )
                lines.append(f"[host] index={host_index} role={role.value}")
                turn = await run_host_turn(
                    self._context.model,
                    self._context.config,
                    request,
                )
                model_calls += turn.model_calls
                lines.extend(turn.lines)
                if not turn.committed:
                    await self._context.admin.pass_turn(opened.negotiation_id)
                    lines.append("[runner] no valid move; turn passed")
            summary = await self._context.admin.summary(opened.negotiation_id)
            events = await self._context.admin.events(opened.negotiation_id)
            note = (
                f"host=openai-mcp;model={self._context.config.model};"
                f"policy={self._context.host_policy.value};model_calls={model_calls};"
                f"recovered_refusals={summary.recovered_refusals}"
            )
            row = ResultRow.from_summary(run_name, summary, note)
            lines.append(f"[result] {row.model_dump_json()}")
            self._context.archive.append(row, events, tuple(lines))
        except (ModelCallError, MCPError, ValidationError, httpx2.HTTPError) as exc:
            note = (
                f"host=openai-mcp;model={self._context.config.model};"
                f"policy={self._context.host_policy.value};"
                f"error={exc.__class__.__name__}:{exc}"
            )
            row = ResultRow(
                run=run_name,
                condition=condition,
                scenario=str(scenario.id),
                deal_possible=None,
                outcome=None,
                price=None,
                correct=None,
                violation=None,
                attempted_violations=None,
                refused_calls=None,
                turns=None,
                tool_calls=None,
                note=note,
            )
            lines.append(f"[error] {note}")
            self._context.archive.append(row, (), tuple(lines))
            message = f"archived crashed episode {run_name}/{scenario.id}"
            raise ExperimentHaltedError(message) from exc

    @staticmethod
    def _token(opened: OpenedNegotiation, role: Role) -> str:
        match role:
            case Role.BUYER:
                return opened.buyer_token
            case Role.SELLER:
                return opened.seller_token
            case unreachable:
                assert_never(unreachable)
