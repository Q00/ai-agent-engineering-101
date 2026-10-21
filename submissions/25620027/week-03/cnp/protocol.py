"""Role, phase, deadline and duplicate guards for the contract lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, assert_never

from pydantic import TypeAdapter, ValidationError

from cnp.domain import Agent, Candidate, RuntimeTask, parse_bid, select_winner
from cnp.records import Announcement, Contract, Event, ModelReply, Request, Response
from cnp.store import Store, StoreError

if TYPE_CHECKING:
    from collections.abc import Callable

AGENTS: Final[tuple[Agent, ...]] = ("A", "B", "C")
AGENT: Final[TypeAdapter[Agent]] = TypeAdapter(Agent)


class ProtocolError(Exception):
    """A rejected operation leaves the contract unchanged."""

    def __init__(self, code: str) -> None:
        self.code: str = code
        super().__init__(code)


class Protocol:
    """Advance a single-process state machine through atomic store writes."""

    def __init__(self, store: Store, clock: Callable[[], float]) -> None:
        self.store: Store = store
        self.clock: Callable[[], float] = clock

    def reject(self, request: Request, code: str) -> None:
        """Record a rejection without making it a protocol message."""
        self.store.record(
            Event(
                stream="system_events",
                contract_id=request.contract_id,
                kind="REJECT",
                actor=request.actor,
                detail=code,
            )
        )
        raise ProtocolError(code)

    def load(self, request: Request) -> Contract:
        """Require an existing contract in this run's database."""
        try:
            return self.store.load(request.contract_id)
        except StoreError as exc:
            raise ProtocolError(exc.code) from exc

    def get_task(self, request: Request, task: RuntimeTask) -> Contract:
        """Create a Manager-owned contract with no evaluator data."""
        if request.actor != "M":
            self.reject(request, "role_error")
        return self.store.save(None, Contract(id=request.contract_id, task=task), ())

    def announce(self, request: Request, window_s: float = 240) -> Contract:
        """Publish one identical announcement to all three recipients."""
        current = self.load(request)
        if request.actor != "M":
            self.reject(request, "role_error")
        if current.phase != "QUEUED":
            self.reject(request, "phase_error")
        after = current.model_copy(
            update={
                "phase": "BIDDING",
                "deadline": self.clock() + window_s,
                "expires_at": (datetime.now(UTC) + timedelta(seconds=window_s)).isoformat(),
            }
        )
        return self.store.save(
            current,
            after,
            tuple(
                Event(
                    stream="protocol_messages",
                    contract_id=current.id,
                    kind="ANNOUNCE",
                    actor=a,
                    detail=current.task.model_dump_json(),
                )
                for a in AGENTS
            ),
        )

    def read_announcement(self, request: Request) -> Announcement:
        """Expose current public task, never gold or other contractors' bids."""
        current = self.load(request)
        if request.actor not in AGENTS:
            self.reject(request, "role_error")
        if current.phase != "BIDDING":
            self.reject(request, "phase_error")
        return Announcement(
            contract_id=current.id,
            task_id=current.task.id,
            text=current.task.desc,
            deadline=current.expires_at,
        )

    def submit_bid(self, request: Request, reply: ModelReply) -> Contract:
        """Accept at most one terminal response per authenticated contractor."""
        current = self.load(request)
        if request.actor not in AGENTS:
            self.reject(request, "role_error")
        if current.phase != "BIDDING":
            self.reject(request, "phase_error")
        if self.clock() >= current.deadline:
            self.store.record(
                Event(
                    stream="system_events",
                    contract_id=current.id,
                    kind="LATE_REPLY",
                    actor=request.actor,
                    detail=reply.model_dump_json(),
                )
            )
            self.reject(request, "late_bid")
        if any(r.agent == request.actor for r in current.responses):
            self.reject(request, "duplicate_bid")
        response = self.classify(request, reply)
        events = [
            Event(
                stream="system_events",
                contract_id=current.id,
                kind="RESPONSE",
                actor=request.actor,
                detail=response.model_dump_json(),
            )
        ]
        if response.proposal is not None and response.proposal.bid:
            events.append(
                Event(
                    stream="protocol_messages",
                    contract_id=current.id,
                    kind="BID",
                    actor=request.actor,
                    detail=response.proposal.model_dump_json(),
                )
            )
        return self.store.save(
            current,
            current.model_copy(
                update={
                    "responses": (*current.responses, response),
                }
            ),
            tuple(events),
        )

    @staticmethod
    def classify(request: Request, reply: ModelReply) -> Response:
        """Distinguish normal abstention, malformed JSON and transport errors."""
        agent = AGENT.validate_python(request.actor)
        match reply.error:
            case "api_error" | "timeout" as status:
                return Response(
                    agent=agent, request_id=request.request_id, status=status, reply=reply
                )
            case "":
                try:
                    proposal = parse_bid(reply.text)
                except ValidationError:
                    return Response(
                        agent=agent, request_id=request.request_id, status="parse_fail", reply=reply
                    )
                return Response(
                    agent=agent,
                    request_id=request.request_id,
                    status="bid" if proposal.bid else "decline",
                    proposal=proposal,
                    reply=reply,
                )
            case unreachable:
                assert_never(unreachable)

    def seal(self, request: Request) -> Contract:
        """Freeze candidates after all responses or the common deadline."""
        current = self.load(request)
        if request.actor != "M":
            self.reject(request, "role_error")
        if current.phase != "BIDDING":
            self.reject(request, "phase_error")
        if len(current.responses) < len(AGENTS) and self.clock() < current.deadline:
            self.reject(request, "responses_pending")
        return self.store.save(current, current.model_copy(update={"phase": "SEALED"}), ())

    def award(self, request: Request) -> Contract:
        """Select by confidence; repeating the exact award command is idempotent."""
        current = self.load(request)
        if request.actor != "M":
            self.reject(request, "role_error")
        if current.award_request_id == request.request_id:
            return current
        if current.phase != "SEALED":
            self.reject(request, "phase_error")
        winner = select_winner(
            tuple(
                Candidate(r.agent, r.proposal) for r in current.responses if r.proposal is not None
            )
        )
        events = (
            ()
            if winner is None
            else (
                Event(
                    stream="protocol_messages", contract_id=current.id, kind="AWARD", actor=winner
                ),
            )
        )
        return self.store.save(
            current,
            current.model_copy(
                update={
                    "phase": "UNASSIGNED" if winner is None else "AWARDED",
                    "winner": winner,
                    "award_request_id": request.request_id,
                }
            ),
            events,
        )

    def close(self, request: Request) -> Contract:
        """Finish allocation; this does not execute the task."""
        current = self.load(request)
        if request.actor != "M":
            self.reject(request, "role_error")
        if current.phase not in ("AWARDED", "UNASSIGNED"):
            self.reject(request, "phase_error")
        return self.store.save(current, current.model_copy(update={"phase": "CLOSED"}), ())
