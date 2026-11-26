"""State-routed negotiation with a private two-party settlement gateway."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never, final

from acl_lab.domain import ChatMessage, ChatRole, Negotiator, Outcome, Performative
from acl_lab.engine import ReadRequest, read_message, speak
from acl_lab.hybrid_domain import (
    HybridContext,
    HybridEpisodeResult,
    HybridExecution,
    PublicPhase,
)
from acl_lab.hybrid_prompts import hybrid_public_prompt, public_condition
from acl_lab.hybrid_settlement import settle
from acl_lab.protocol import ParsedMessage, ParseFailure, score_outcome

if TYPE_CHECKING:
    from acl_lab.domain import ModelClient, ModelFailure


@final
class EpisodeCounters:
    """Mutable counters intentionally accumulate observations across the turn loop."""

    __slots__ = (
        "attempts",
        "format_errors",
        "guard_vetoes",
        "reader_calls",
        "settlement_calls",
        "settlement_errors",
        "settlement_vetoes",
        "tokens",
        "turns",
    )

    def __init__(self) -> None:
        self.turns = 0
        self.format_errors = 0
        self.reader_calls = 0
        self.settlement_calls = 0
        self.settlement_errors = 0
        self.settlement_vetoes = 0
        self.guard_vetoes = 0
        self.tokens = 0
        self.attempts = 0


@dataclass(frozen=True, slots=True)
class PublicResolution:
    """Deterministic effect of one parsed public act before settlement."""

    phase: PublicPhase
    proposed_price: int | None = None
    settlement_price: int | None = None
    outcome: Outcome | None = None
    format_errors: int = 0
    phase_changed: bool = False


def _other(role: Negotiator) -> Negotiator:
    match role:
        case Negotiator.BUYER:
            return Negotiator.SELLER
        case Negotiator.SELLER:
            return Negotiator.BUYER
        case unreachable:
            assert_never(unreachable)


def _resolve_public(
    parsed: ParsedMessage,
    phase: PublicPhase,
    other_price: int | None,
) -> PublicResolution:
    match parsed.performative:
        case Performative.PROPOSE:
            if parsed.price is None:
                return PublicResolution(phase, format_errors=1)
            match phase:
                case PublicPhase.DISCOVERY:
                    next_phase = PublicPhase.BARGAINING
                    phase_changed = True
                case PublicPhase.BARGAINING:
                    next_phase = phase
                    phase_changed = False
                case unreachable:
                    assert_never(unreachable)
            return PublicResolution(
                next_phase,
                proposed_price=parsed.price,
                phase_changed=phase_changed,
            )
        case Performative.ACCEPT:
            if other_price is None:
                return PublicResolution(phase, format_errors=1)
            return PublicResolution(phase, settlement_price=other_price)
        case Performative.REJECT:
            return PublicResolution(phase)
        case Performative.REFUSE:
            return PublicResolution(phase, outcome=Outcome.NO_DEAL)
        case unreachable:
            assert_never(unreachable)


def _crashed(
    failure: ModelFailure,
    transcript: list[str],
    counters: EpisodeCounters,
) -> HybridExecution:
    note = f"model_error={failure.code.value};detail={failure.detail};attempts={failure.attempts}"
    result = HybridEpisodeResult(
        None,
        None,
        None,
        None,
        None,
        None,
        counters.reader_calls,
        counters.settlement_calls,
        counters.settlement_errors,
        counters.settlement_vetoes,
        counters.guard_vetoes,
        note,
    )
    return HybridExecution(result, (*transcript, f"[error] {note}"), failure.stop_batch)


def _finish(
    context: HybridContext,
    outcome: Outcome,
    deal_price: int | None,
    counters: EpisodeCounters,
    transcript: list[str],
) -> HybridExecution:
    score = score_outcome(context.scenario, outcome, deal_price)
    note = f"tokens={counters.tokens};api_attempts={counters.attempts}"
    result = HybridEpisodeResult(
        outcome,
        deal_price,
        score.correct,
        score.violation,
        counters.turns,
        counters.format_errors,
        counters.reader_calls,
        counters.settlement_calls,
        counters.settlement_errors,
        counters.settlement_vetoes,
        counters.guard_vetoes,
        note,
    )
    return HybridExecution(result, (*transcript, f"[result] {result!r}"))


def run_hybrid_episode(  # noqa: C901, PLR0915
    context: HybridContext,
    model: ModelClient,
) -> HybridExecution:
    """Run free discovery, tagged bargaining, then guarded structured settlement."""
    histories: dict[Negotiator, list[ChatMessage]] = {
        Negotiator.BUYER: [],
        Negotiator.SELLER: [],
    }
    last_price: dict[Negotiator, int] = {}
    transcript: list[str] = []
    visible_transcript: list[str] = []
    counters = EpisodeCounters()
    role = Negotiator.BUYER
    phase = PublicPhase.DISCOVERY
    outcome = Outcome.OPEN
    deal_price: int | None = None

    for turn_number in range(1, context.max_turns + 1):
        counters.turns = turn_number
        other = _other(role)
        speaker = speak(
            model,
            hybrid_public_prompt(role, context.scenario, phase),
            tuple(histories[role]),
        )
        if speaker.failure is not None:
            return _crashed(speaker.failure, transcript, counters)
        counters.tokens += speaker.tokens
        counters.attempts += speaker.attempts
        text = speaker.text or ""
        histories[role].append(ChatMessage(ChatRole.ASSISTANT, text))
        histories[other].append(ChatMessage(ChatRole.USER, text))
        visible_line = f"[{role.value}] {text}"
        visible_transcript.append(visible_line)
        transcript.append(visible_line)

        read = read_message(
            ReadRequest(public_condition(phase), text, tuple(visible_transcript), model)
        )
        counters.reader_calls += read.reader_calls
        counters.tokens += read.tokens
        counters.attempts += read.attempts
        transcript.append(f"  {read.evidence}")
        if read.failure is not None:
            return _crashed(read.failure, transcript, counters)

        match read.parsed:
            case None | ParseFailure():
                counters.format_errors += 1
                role = other
                continue
            case ParsedMessage() as parsed:
                resolution = _resolve_public(parsed, phase, last_price.get(other))
            case unreachable:
                assert_never(unreachable)

        counters.format_errors += resolution.format_errors
        phase = resolution.phase
        if resolution.proposed_price is not None:
            last_price[role] = resolution.proposed_price
        if resolution.phase_changed:
            transcript.append("[router] discovery -> bargaining")
        if resolution.settlement_price is not None:
            settlement = settle(context, resolution.settlement_price, model)
            counters.settlement_calls += settlement.calls
            counters.settlement_errors += settlement.errors
            counters.settlement_vetoes += settlement.vetoes
            counters.guard_vetoes += settlement.guard_vetoes
            counters.tokens += settlement.tokens
            counters.attempts += settlement.attempts
            transcript.append(settlement.evidence)
            if settlement.failure is not None:
                return _crashed(settlement.failure, transcript, counters)
            if settlement.approved:
                outcome = Outcome.DEAL
                deal_price = resolution.settlement_price
                break
        if resolution.outcome is not None:
            outcome = resolution.outcome
            break
        role = other

    return _finish(context, outcome, deal_price, counters, transcript)
