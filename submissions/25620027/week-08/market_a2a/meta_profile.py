"""Version bounded strategy priors from operator feedback on own tool outcomes."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Final, Literal, assert_never

from pydantic import AwareDatetime, Field

from market_a2a.models import FrozenModel, HarnessError, json_value

if TYPE_CHECKING:
    from pydantic import JsonValue

    from market_a2a.memory_journal import MemoryJournal

MAX_FEEDBACK_BYTES: Final = 65536
MAX_PROFILE_BYTES: Final = 16384


class Focus(StrEnum):
    """Attention priorities cannot change price, payment or authorization guards."""

    RETRIEVE = "retrieve_own_evidence"
    PRICE = "compare_observed_prices"
    TURNS = "avoid_unproductive_turns"


class OperatorFeedback(FrozenModel):
    """Only a private operator file supplies feedback; market reviews are excluded."""

    source: Literal["operator"]
    event_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verdict: Literal["good", "bad"]
    focus: Focus


class FeedbackBatch(FrozenModel):
    """One replaceable human review batch, not an ever-growing transcript."""

    entries: tuple[OperatorFeedback, ...] = Field(min_length=1, max_length=64)


class StrategyProfile(FrozenModel):
    """Describe next-step emphasis without rewriting the fixed system instructions."""

    version: int = Field(ge=1)
    priorities: tuple[Focus, ...] = Field(min_length=3, max_length=3)
    feedback_digest: str
    reviewed_observations: int
    expires_at: AwareDatetime


class ProfileHistory(FrozenModel):
    """Retain the current profile and at most two prior versions."""

    current: StrategyProfile
    previous: tuple[StrategyProfile, ...] = Field(default=(), max_length=2)


@dataclass(frozen=True, slots=True)
class ProfilePaths:
    """Separate read-only operator input from the worker's derived output."""

    feedback: Path
    profile: Path


def refresh_profile(journal: MemoryJournal, paths: ProfilePaths) -> bool:
    """Check evidence outside the trading context, then publish a typed profile."""
    if not paths.feedback.exists():
        return False
    if paths.feedback.stat().st_size > MAX_FEEDBACK_BYTES:
        message = "Operator feedback exceeds its input bound"
        raise HarnessError(message)
    raw = paths.feedback.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    previous = (
        ProfileHistory.model_validate_json(paths.profile.read_bytes())
        if paths.profile.exists()
        else None
    )
    if previous and previous.current.feedback_digest == digest:
        return False
    feedback = FeedbackBatch.model_validate_json(raw)
    _, memory = journal.snapshot()
    events = {
        trade.event_id: trade for trades in memory.shops.values() for trade in trades
    }
    weights = dict.fromkeys(Focus, 0)
    for entry in feedback.entries:
        trade = events.get(entry.event_id)
        if (
            trade is None
            or hashlib.sha256(trade.model_dump_json().encode()).hexdigest()
            != entry.evidence_sha256
        ):
            message = "Feedback must reference an unchanged own observation"
            raise HarnessError(message)
        # Bad outcomes request extra attention to that capability, not retaliation.
        match entry.verdict:
            case "bad":
                weights[entry.focus] += 2
            case "good":
                weights[entry.focus] += 1
            case _:
                assert_never(entry.verdict)
    profile = ProfileHistory(
        current=StrategyProfile(
            version=previous.current.version + 1 if previous else 1,
            priorities=tuple(sorted(Focus, key=lambda focus: -weights[focus])),
            feedback_digest=digest,
            reviewed_observations=len(feedback.entries),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        ),
        previous=(previous.current, *previous.previous[:1]) if previous else (),
    )
    paths.profile.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=paths.profile.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            _ = stream.write(profile.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())
            _ = temporary.replace(paths.profile)
        finally:
            temporary.unlink(missing_ok=True)
    return True


def profile_context() -> dict[str, JsonValue]:
    """Read a small versioned prior; invalid/expired data cannot become instructions."""
    location = os.environ.get("AX_META_PROFILE")
    if not location:
        return {}
    path = Path(location)
    if not path.exists():
        return {}
    if path.stat().st_size > MAX_PROFILE_BYTES:
        message = "Strategy profile exceeds its context bound"
        raise HarnessError(message)
    profile = ProfileHistory.model_validate_json(path.read_bytes()).current
    if profile.expires_at <= datetime.now(UTC):
        return {}
    return {"strategy_prior": json_value(profile)}
