"""The two-agent turn loop and condition-specific protocol reader."""

from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from acl_lab.domain import (
    ChatMessage,
    ChatRole,
    Condition,
    EpisodeContext,
    EpisodeExecution,
    EpisodeResult,
    ModelClient,
    ModelFailure,
    ModelSuccess,
    Negotiator,
    Outcome,
    Performative,
)
from acl_lab.prompts import READER_SYSTEM, reader_input, system_prompt
from acl_lab.protocol import (
    ParsedMessage,
    ParseFailure,
    ParseResult,
    parse_reader,
    parse_structured,
    parse_tagged,
    score_outcome,
)


@dataclass(frozen=True, slots=True)
class ReadExecution:
    """One protocol interpretation plus its measurable reader cost."""

    parsed: ParseResult | None
    failure: ModelFailure | None
    reader_calls: int
    tokens: int
    attempts: int
    evidence: str


@dataclass(frozen=True, slots=True)
class ReadRequest:
    """Inputs needed to interpret one visible negotiation message."""

    condition: Condition
    text: str
    visible_transcript: tuple[str, ...]
    model: ModelClient


@dataclass(frozen=True, slots=True)
class SpeakerExecution:
    """Normalized result of one buyer or seller model call."""

    text: str | None
    failure: ModelFailure | None
    tokens: int
    attempts: int


@dataclass(frozen=True, slots=True)
class ActResolution:
    """State-machine effect of one successfully parsed act."""

    outcome: Outcome | None = None
    deal_price: int | None = None
    proposed_price: int | None = None
    format_errors: int = 0


def _call_reader(request: ReadRequest, tagged_performative: Performative | None) -> ReadExecution:
    reply = request.model.complete(
        READER_SYSTEM,
        (ChatMessage(ChatRole.USER, reader_input(request.visible_transcript)),),
    )
    match reply:
        case ModelFailure():
            return ReadExecution(None, reply, 1, 0, reply.attempts, "[reader] model failure")
        case ModelSuccess():
            parsed = parse_reader(reply.text)
            if tagged_performative is not None:
                if isinstance(parsed, ParseFailure) or parsed.price is None:
                    parsed = ParseFailure(reason="tagged_price_unreadable")
                else:
                    parsed = ParsedMessage(tagged_performative, parsed.price)
            return ReadExecution(
                parsed,
                None,
                1,
                reply.tokens,
                reply.attempts,
                f"[reader] {reply.text}",
            )
        case unreachable:
            assert_never(unreachable)


def read_message(request: ReadRequest) -> ReadExecution:
    """Interpret one message with the selected public protocol."""
    match request.condition:
        case Condition.STRUCTURED:
            parsed = parse_structured(request.text)
            return ReadExecution(parsed, None, 0, 0, 0, f"[parser] {parsed!r}")
        case Condition.TAGGED:
            tagged = parse_tagged(request.text)
            if isinstance(tagged, ParseFailure) or not tagged.reader_needed:
                return ReadExecution(tagged, None, 0, 0, 0, f"[tag] {tagged!r}")
            return _call_reader(request, tagged.performative)
        case Condition.FREE:
            return _call_reader(request, None)
        case unreachable:
            assert_never(unreachable)


def speak(model: ModelClient, system: str, history: tuple[ChatMessage, ...]) -> SpeakerExecution:
    """Normalize one model call for a public or internal protocol step."""
    reply = model.complete(system, history)
    match reply:
        case ModelFailure():
            return SpeakerExecution(None, reply, 0, reply.attempts)
        case ModelSuccess():
            return SpeakerExecution(reply.text, None, reply.tokens, reply.attempts)
        case unreachable:
            assert_never(unreachable)


def _resolve_act(parsed: ParsedMessage, other_price: int | None) -> ActResolution:
    match parsed.performative:
        case Performative.PROPOSE:
            if parsed.price is None:
                return ActResolution(format_errors=1)
            return ActResolution(proposed_price=parsed.price)
        case Performative.ACCEPT:
            if other_price is None:
                return ActResolution(format_errors=1)
            return ActResolution(outcome=Outcome.DEAL, deal_price=other_price)
        case Performative.REJECT:
            return ActResolution()
        case Performative.REFUSE:
            return ActResolution(outcome=Outcome.NO_DEAL)
        case unreachable:
            assert_never(unreachable)


def _crashed(
    failure: ModelFailure,
    transcript: list[str],
    reader_calls: int,
) -> EpisodeExecution:
    note = f"model_error={failure.code.value};detail={failure.detail};attempts={failure.attempts}"
    return EpisodeExecution(
        result=EpisodeResult(None, None, None, None, None, None, reader_calls, note),
        transcript=(*transcript, f"[error] {note}"),
        stop_batch=failure.stop_batch,
    )


def run_episode(context: EpisodeContext, model: ModelClient) -> EpisodeExecution:
    """Run one buyer-first episode and retain every protocol observation."""
    histories: dict[Negotiator, list[ChatMessage]] = {
        Negotiator.BUYER: [],
        Negotiator.SELLER: [],
    }
    last_price: dict[Negotiator, int] = {}
    transcript: list[str] = []
    visible_transcript: list[str] = []
    role = Negotiator.BUYER
    format_errors = 0
    reader_calls = 0
    total_tokens = 0
    total_attempts = 0
    outcome = Outcome.OPEN
    deal_price: int | None = None
    turns = 0

    for turn_number in range(1, context.max_turns + 1):
        turns = turn_number
        other = Negotiator.SELLER if role is Negotiator.BUYER else Negotiator.BUYER
        speaker = speak(
            model,
            system_prompt(role, context.scenario, context.condition),
            tuple(histories[role]),
        )
        if speaker.failure is not None:
            return _crashed(speaker.failure, transcript, reader_calls)
        total_tokens += speaker.tokens
        total_attempts += speaker.attempts
        text = speaker.text or ""

        histories[role].append(ChatMessage(ChatRole.ASSISTANT, text))
        histories[other].append(ChatMessage(ChatRole.USER, text))
        visible_line = f"[{role.value}] {text}"
        visible_transcript.append(visible_line)
        transcript.append(visible_line)
        read = read_message(ReadRequest(context.condition, text, tuple(visible_transcript), model))
        reader_calls += read.reader_calls
        total_tokens += read.tokens
        total_attempts += read.attempts
        transcript.append(f"  {read.evidence}")
        if read.failure is not None:
            return _crashed(read.failure, transcript, reader_calls)
        parsed = read.parsed
        if parsed is None or isinstance(parsed, ParseFailure):
            format_errors += 1
            role = other
            continue

        resolution = _resolve_act(parsed, last_price.get(other))
        format_errors += resolution.format_errors
        if resolution.proposed_price is not None:
            last_price[role] = resolution.proposed_price
        if resolution.outcome is not None:
            outcome = resolution.outcome
            deal_price = resolution.deal_price
            break
        role = other

    score = score_outcome(context.scenario, outcome, deal_price)
    note = f"tokens={total_tokens};api_attempts={total_attempts}"
    result = EpisodeResult(
        outcome=outcome,
        price=deal_price,
        correct=score.correct,
        violation=score.violation,
        turns=turns,
        format_errors=format_errors,
        reader_calls=reader_calls,
        note=note,
    )
    return EpisodeExecution(
        result=result,
        transcript=(*transcript, f"[result] {result!r}"),
    )
