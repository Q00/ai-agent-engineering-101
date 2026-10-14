"""Two private agent histories and a deterministic negotiation state machine."""

from __future__ import annotations

from typing import Callable

from model_client import Usage
from prompts import CONDITIONS, OPENING_CUE, system_prompt
from protocol import read_message


def evaluate_outcome(scenario: dict, outcome: str, price: int | None) -> tuple[int, int]:
    """Score the recorded protocol result, without enforcing private limits."""
    possible = scenario["reserve"] <= scenario["budget"]
    violation = int(
        outcome == "deal"
        and price is not None
        and (price < scenario["reserve"] or price > scenario["budget"])
    )
    correct = int(
        (outcome == "deal" and price is not None and possible and not violation)
        or (outcome == "no_deal" and not possible)
    )
    return correct, violation


def run_episode(
    scenario: dict,
    condition: str,
    backend,
    *,
    max_turns: int = 8,
    log: Callable[[dict], None] = lambda event: None,
) -> dict:
    """Run one episode; preserve counters and safe error metadata on failure.

    Agent/reader call counts include physical retries and failed calls; retries
    also have a separate count so the extra API traffic remains visible.
    A failed call without usage marks the episode token total as unknown.
    KeyboardInterrupt returns a preserved crash result with interrupted=True;
    the runner can persist that row before stopping the complete experiment.
    """
    if condition not in CONDITIONS:
        raise ValueError("unknown condition")
    if type(max_turns) is not int or max_turns < 1:
        raise ValueError("max_turns must be a positive integer")
    for field in ("reserve", "budget"):
        if type(scenario[field]) is not int or scenario[field] < 0:
            raise ValueError("scenario limits must be nonnegative integers")

    histories = {
        role: [{"role": "system", "content": system_prompt(role, scenario["item"], limit, condition)}]
        for role, limit in (("buyer", scenario["budget"]), ("seller", scenario["reserve"]))
    }
    histories["buyer"].append({"role": "user", "content": OPENING_CUE})
    transcript: list[dict[str, str]] = []
    last_price: dict[str, int | None] = {"buyer": None, "seller": None}
    turns = format_errors = reader_calls = agent_calls = retries = 0
    semantic_errors = 0
    usage = Usage()
    outcome, price = "open", None
    error: dict | None = None
    interrupted = False
    current_role = "buyer"

    def episode_log(event: dict) -> None:
        log({"turn": turns, "actor": current_role, **event})

    def call_model(kind: str, messages: list[dict[str, str]]):
        nonlocal reader_calls, agent_calls, retries, usage
        if kind == "reader":
            reader_calls += 1
        else:
            agent_calls += 1
        call_turn = turns if kind == "reader" else turns + 1
        episode_log({"event": "model_call", "kind": kind, "turn": call_turn})
        try:
            reply = backend.complete([dict(message) for message in messages])
        except (Exception, KeyboardInterrupt) as exc:
            failed_retries = getattr(exc, "retries", 0)
            if type(failed_retries) is int and failed_retries >= 0:
                retries += failed_retries
                if kind == "reader":
                    reader_calls += failed_retries
                else:
                    agent_calls += failed_retries
            failed_usage = getattr(exc, "usage", None)
            usage = usage + (failed_usage if isinstance(failed_usage, Usage) else Usage(None, None))
            raise
        # A timed-out attempt can consume tokens without returning usage.
        # Keep final-response usage in model_result, but do not call it the total.
        usage = usage + (reply.usage if reply.retries == 0 else Usage(None, None))
        retries += reply.retries
        if kind == "reader":
            reader_calls += reply.retries
        else:
            agent_calls += reply.retries
        episode_log({
            "event": "model_result", "kind": kind, "turn": call_turn, "model": reply.model,
            "input_tokens": reply.usage.input_tokens,
            "output_tokens": reply.usage.output_tokens,
            "total_tokens": reply.usage.total_tokens, "retries": reply.retries,
        })
        return reply

    log({
        "event": "episode_start", "scenario": scenario["id"],
        "item": scenario["item"], "reserve": scenario["reserve"], "budget": scenario["budget"],
        "deal_possible": int(scenario["reserve"] <= scenario["budget"]),
        "condition": condition, "max_turns": max_turns,
    })
    try:
        for turn_index in range(max_turns):
            current_role = "buyer" if turn_index % 2 == 0 else "seller"
            other_role = "seller" if current_role == "buyer" else "buyer"
            reply = call_model("agent", histories[current_role])
            raw = reply.text
            turns += 1
            # Deliver first: a parse failure must not hide a message from either agent.
            histories[current_role].append({"role": "assistant", "content": raw})
            histories[other_role].append({"role": "user", "content": raw})
            transcript.append({"role": current_role, "content": raw})
            episode_log({"event": "message", "raw": raw})
            parsed = read_message(
                condition, raw, transcript,
                lambda messages: call_model("reader", messages), episode_log,
            )
            before = {"last_price": dict(last_price), "outcome": outcome, "price": price}
            reason = ""
            if not parsed.ok:
                format_errors += 1
                reason = "parse_failure_continue"
            elif parsed.performative == "propose":
                last_price[current_role] = parsed.price
                reason = "proposal_recorded"
            elif parsed.performative == "accept-proposal":
                if last_price[other_role] is None:
                    semantic_errors += 1
                    reason = "accept_without_opponent_proposal"
                else:
                    price = last_price[other_role]
                    outcome = "deal"
                    reason = "opponent_proposal_accepted"
            elif parsed.performative == "reject-proposal":
                reason = "rejection_continue"
            elif parsed.performative == "refuse":
                outcome = "no_deal"
                reason = "refusal_ends_negotiation"
            episode_log({
                "event": "transition", "reason": reason, "before": before,
                "after": {"last_price": dict(last_price), "outcome": outcome, "price": price},
            })
            if outcome != "open":
                break
    except (Exception, KeyboardInterrupt) as exc:
        interrupted = isinstance(exc, KeyboardInterrupt)
        status_code = getattr(exc, "status_code", None)
        error_type = getattr(exc, "error_type", type(exc).__name__)
        if not isinstance(error_type, str) or not error_type.isidentifier():
            error_type = type(exc).__name__
        error = {
            "type": error_type,
            "status_code": status_code if type(status_code) is int else None,
        }
        episode_log({"event": "episode_error", **error, "interrupted": interrupted})

    metrics = {
        "agent_calls": agent_calls, "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens, "total_tokens": usage.total_tokens,
        "retries": retries,
    }
    note_parts = [f"{key}={value if value is not None else 'unknown'}" for key, value in metrics.items()]
    note_parts.append(f"semantic_errors={semantic_errors}")
    if error is not None:
        note_parts.append(f"error={error['type']}")
        if error["status_code"] is not None:
            note_parts.append(f"status_code={error['status_code']}")
        if interrupted:
            note_parts.append("interrupted=1")
        result = {"outcome": "", "price": "", "correct": "", "violation": ""}
    else:
        correct, violation = evaluate_outcome(scenario, outcome, price)
        result = {"outcome": outcome, "price": price, "correct": correct, "violation": violation}
    result.update({
        "turns": turns, "format_errors": format_errors, "reader_calls": reader_calls,
        "note": ";".join(note_parts), "metrics": metrics,
        "status": "crashed" if error is not None else "completed",
        "error": error, "interrupted": interrupted,
        "error_type": error["type"] if error else "",
        "status_code": error["status_code"] if error else None,
    })
    log({"event": "episode_result", **result})
    return result
