"""Serializable contract snapshots and separate event streams."""

from __future__ import annotations

from typing import Literal

from cnp.domain import Agent, Bid, ContractId, FrozenModel, RuntimeTask, Text

Actor = Literal["M", "A", "B", "C"]
Phase = Literal["QUEUED", "BIDDING", "SEALED", "AWARDED", "UNASSIGNED", "CLOSED"]
ResponseStatus = Literal["bid", "decline", "parse_fail", "api_error", "timeout"]


class Request(FrozenModel):
    """Identity comes from the harness, never the model."""

    contract_id: ContractId
    actor: Actor
    request_id: Text


class ModelReply(FrozenModel):
    """One network outcome, preserved even when no valid JSON arrives."""

    text: str = ""
    raw: str = ""
    error: Literal["", "api_error", "timeout"] = ""
    error_detail: str = ""
    stop_batch: bool = False
    tokens: int = 0
    elapsed_s: float = 0


class Response(FrozenModel):
    """A terminal contractor outcome, including normal abstention."""

    agent: Agent
    request_id: Text
    status: ResponseStatus
    proposal: Bid | None = None
    reply: ModelReply


class Contract(FrozenModel):
    """Persisted state. Mutable workflow produces a new snapshot each time."""

    id: ContractId
    task: RuntimeTask
    phase: Phase = "QUEUED"
    version: int = 0
    deadline: float = 0
    expires_at: str = ""
    responses: tuple[Response, ...] = ()
    winner: Agent | None = None
    award_request_id: Text | None = None


class Event(FrozenModel):
    """A protocol message or diagnostic event with contract identity."""

    stream: Literal["protocol_messages", "system_events"]
    contract_id: ContractId
    kind: str
    actor: Actor = "M"
    detail: str = ""


class Announcement(FrozenModel):
    """Identical public payload delivered to each independently prompted agent."""

    contract_id: ContractId
    task_id: str
    text: str
    recipients: tuple[Agent, ...] = ("A", "B", "C")
    eligibility_spec: str = "any contractor whose skill covers this task"
    bid_schema: str = "JSON: bid boolean, confidence number 0..100, reason non-empty string"
    deadline: str
