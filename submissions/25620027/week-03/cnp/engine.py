"""Execute gold-free tasks sequentially and emit an immutable console trace."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from time import sleep
from typing import TYPE_CHECKING, TextIO
from typing import Protocol as BidProtocol

from cnp.domain import Condition, ContractId, RuntimeTask
from cnp.prompts import system_prompt
from cnp.protocol import AGENTS, Protocol, ProtocolError
from cnp.records import Announcement, Contract, ModelReply, Request

if TYPE_CHECKING:
    from cnp.settings import Settings


class BidClient(BidProtocol):
    """Only the model-call seam varies between network and test fixtures."""

    def bid(self, prompt: str, announcement: Announcement) -> ModelReply:
        """Return the one terminal network response."""
        ...


class BatchBlockedError(Exception):
    """Stop on quota or authorization refusal while retaining the failed run."""

    def __init__(self, detail: str) -> None:
        self.detail: str = detail
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class Trace:
    """Console capture required by the assignment; raw model data is escaped."""

    stream: TextIO

    def emit(self, kind: str, payload: str) -> None:
        """Flush each line so interruption cannot erase preceding responses."""
        line = f"{datetime.now(UTC).isoformat()} {kind} {payload}"
        print(line, file=self.stream, flush=True)
        print(line, flush=True)


@dataclass(frozen=True, slots=True)
class Engine:
    """One run's actual execution context, separate from offline gold data."""

    run: int
    condition: Condition
    settings: Settings
    protocol: Protocol
    client: BidClient
    trace: Trace

    def execute(self, tasks: tuple[RuntimeTask, ...]) -> tuple[Contract, ...]:
        """Finish each allocation before starting the next independent task."""
        results: list[Contract] = []
        for task in tasks:
            cid = ContractId(f"{self.run}:{task.id}")
            manager = Request(contract_id=cid, actor="M", request_id=f"{cid}:M")
            self.protocol.get_task(manager, task)
            self.protocol.announce(manager, self.settings.contract_deadline_s)
            for agent in AGENTS:
                request = Request(contract_id=cid, actor=agent, request_id=f"{cid}:{agent}")
                announcement = self.protocol.read_announcement(request)
                self.trace.emit(f"ANNOUNCE {agent}", announcement.model_dump_json())
                sleep(self.settings.min_call_interval_s)
                reply = self.client.bid(system_prompt(self.condition, agent), announcement)
                self.trace.emit(f"RAW_RESPONSE {agent}", reply.model_dump_json())
                try:
                    recorded = self.protocol.submit_bid(request, reply)
                except ProtocolError as exc:
                    if exc.code != "late_bid":
                        raise
                    self.trace.emit("LATE_BID", f"{cid} {agent}")
                    break
                response = recorded.responses[-1]
                self.trace.emit(f"RESPONSE {agent}", response.model_dump_json())
                if reply.stop_batch:
                    raise BatchBlockedError(reply.error_detail)
            self.protocol.seal(manager)
            awarded = self.protocol.award(manager)
            self.trace.emit("AWARD", json.dumps({"task_id": task.id, "winner": awarded.winner}))
            results.append(self.protocol.close(manager))
        return tuple(results)
