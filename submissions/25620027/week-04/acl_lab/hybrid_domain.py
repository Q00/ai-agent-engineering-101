"""Typed values for the state-routed hybrid extension."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acl_lab.domain import ModelFailure, Outcome, Scenario, Settings


@unique
class PublicPhase(StrEnum):
    """Public negotiation states with different wire formats."""

    DISCOVERY = "discovery"
    BARGAINING = "bargaining"


@dataclass(frozen=True, slots=True)
class HybridContext:
    """Fixed inputs for one hybrid episode."""

    scenario: Scenario
    max_turns: int


@dataclass(frozen=True, slots=True)
class HybridEpisodeResult:
    """Core assignment metrics plus settlement-specific observations."""

    outcome: Outcome | None
    price: int | None
    correct: int | None
    violation: int | None
    turns: int | None
    format_errors: int | None
    reader_calls: int | None
    settlement_calls: int
    settlement_errors: int
    settlement_vetoes: int
    guard_vetoes: int
    note: str


@dataclass(frozen=True, slots=True)
class HybridExecution:
    """A hybrid result and the evidence used to interpret it."""

    result: HybridEpisodeResult
    transcript: tuple[str, ...]
    stop_batch: bool = False


@dataclass(frozen=True, slots=True)
class HybridResultRecord:
    """One append-only extension CSV row."""

    run: int
    scenario: str
    deal_possible: int
    result: HybridEpisodeResult


@dataclass(frozen=True, slots=True)
class HybridLogRecord:
    """One extension transcript with its frozen settings."""

    run: int
    scenario: str
    execution: HybridExecution
    settings: Settings


@dataclass(frozen=True, slots=True)
class HybridCrash:
    """A model failure plus metrics accumulated before it stopped the episode."""

    failure: ModelFailure
    reader_calls: int
    settlement_calls: int
    settlement_errors: int
    settlement_vetoes: int
    guard_vetoes: int
