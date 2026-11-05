"""Contract Net manager: announce, collect, score, award, execute, monitor."""

from __future__ import annotations

import math
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable

from agents import BidAttempt, Contractor, execute_task, make_team, request_bid
from model_client import Backend
from monitor import Monitor, Profile


POLICIES = ("confidence_only", "token_aware", "reputation_aware")
CONTRACTOR_ORDER = {"A": 0, "B": 1, "C": 2}


@dataclass(frozen=True)
class BidEnvelope:
    auction_id: str
    request_id: str
    contractor_id: str
    received_at: float
    attempt: BidAttempt
    profile: Profile

    def protocol_message(self) -> dict[str, Any] | None:
        decision = self.attempt.decision
        if self.attempt.status != "valid" or decision is None:
            return None
        return {
            "type": "bid_response",
            "auction_id": self.auction_id,
            "request_id": self.request_id,
            "contractor_id": self.contractor_id,
            "bid": decision.bid,
            "confidence": decision.confidence,
            "last_task_tokens": self.profile.last_task_tokens,
            "reason": decision.reason,
        }


@dataclass(frozen=True)
class AuditEvent:
    event: str
    contractor_id: str
    request_id: str
    detail: str = ""


@dataclass
class FrozenResponses:
    accepted: list[BidEnvelope] = field(default_factory=list)
    rejected: list[AuditEvent] = field(default_factory=list)
    pending_contractors: list[str] = field(default_factory=list)


def freeze_responses(
    responses: Iterable[BidEnvelope],
    auction_id: str,
    expected_requests: dict[str, str],
    deadline: float,
) -> FrozenResponses:
    """Apply deadline, identity, and at-most-one-response protocol rules.

    ``received_at`` and ``deadline`` share an injected/relative clock, which
    lets the boundary rules be tested with virtual time and no model calls.
    ``expected_requests`` maps request_id to contractor_id.
    """
    frozen = FrozenResponses()
    seen_requests: set[str] = set()
    seen_contractors: set[str] = set()
    ordered = sorted(responses, key=lambda item: (item.received_at, CONTRACTOR_ORDER.get(item.contractor_id, 99)))
    for response in ordered:
        if response.auction_id != auction_id:
            frozen.rejected.append(
                AuditEvent("stale_response", response.contractor_id, response.request_id)
            )
            continue
        if expected_requests.get(response.request_id) != response.contractor_id:
            frozen.rejected.append(
                AuditEvent("foreign_response", response.contractor_id, response.request_id)
            )
            continue
        if response.request_id in seen_requests or response.contractor_id in seen_contractors:
            frozen.rejected.append(
                AuditEvent("duplicate_response", response.contractor_id, response.request_id)
            )
            continue
        if response.received_at > deadline:
            frozen.rejected.append(
                AuditEvent("late_response", response.contractor_id, response.request_id)
            )
            continue
        seen_requests.add(response.request_id)
        seen_contractors.add(response.contractor_id)
        frozen.accepted.append(response)

    expected_contractors = set(expected_requests.values())
    frozen.pending_contractors = sorted(
        expected_contractors - seen_contractors,
        key=lambda name: CONTRACTOR_ORDER.get(name, 99),
    )
    return frozen


def valid_candidates(responses: Iterable[BidEnvelope]) -> list[BidEnvelope]:
    return [
        response
        for response in responses
        if response.attempt.status == "valid"
        and response.attempt.decision is not None
        and response.attempt.decision.bid
    ]


def token_efficiencies(candidates: Iterable[BidEnvelope], epsilon: float = 1e-9) -> dict[str, float]:
    candidates = list(candidates)
    measured = [
        response.profile.last_task_tokens
        for response in candidates
        if type(response.profile.last_task_tokens) is int
    ]
    if len(measured) < 2 or min(measured, default=0) == max(measured, default=0):
        return {response.contractor_id: 0.5 for response in candidates}
    low, high = min(measured), max(measured)
    efficiencies: dict[str, float] = {}
    for response in candidates:
        tokens = response.profile.last_task_tokens
        if type(tokens) is int:
            efficiencies[response.contractor_id] = 1 - (tokens - low) / (high - low + epsilon)
        else:
            efficiencies[response.contractor_id] = 0.5
    return efficiencies


@dataclass(frozen=True)
class CandidateScore:
    contractor_id: str
    confidence: float
    token_efficiency: float
    reputation: float
    score: float
    received_at: float
    profile_version: int


def score_candidates(candidates: Iterable[BidEnvelope], policy: str) -> list[CandidateScore]:
    if policy not in POLICIES:
        raise ValueError(f"unknown award policy: {policy}")
    candidates = list(candidates)
    efficiencies = token_efficiencies(candidates)
    scored: list[CandidateScore] = []
    for response in candidates:
        decision = response.attempt.decision
        assert decision is not None
        confidence = decision.confidence / 100
        efficiency = efficiencies[response.contractor_id]
        reputation = response.profile.reputation
        if policy == "confidence_only":
            score = confidence
        elif policy == "token_aware":
            score = 0.8 * confidence + 0.2 * efficiency
        else:
            score = 0.6 * confidence + 0.2 * efficiency + 0.2 * reputation
        scored.append(
            CandidateScore(
                contractor_id=response.contractor_id,
                confidence=confidence,
                token_efficiency=efficiency,
                reputation=reputation,
                score=score,
                received_at=response.received_at,
                profile_version=response.profile.profile_version,
            )
        )
    return scored


@dataclass(frozen=True)
class AwardDecision:
    winner: str | None
    scores: list[CandidateScore]
    tie_break_used: bool = False


def choose_winner(candidates: Iterable[BidEnvelope], policy: str) -> AwardDecision:
    scores = score_candidates(candidates, policy)
    if not scores:
        return AwardDecision(None, [])
    ranked = sorted(
        scores,
        key=lambda item: (
            -item.score,
            item.received_at,
            CONTRACTOR_ORDER.get(item.contractor_id, 99),
        ),
    )
    tie = len(ranked) > 1 and math.isclose(ranked[0].score, ranked[1].score, abs_tol=1e-12)
    return AwardDecision(ranked[0].contractor_id, scores, tie)


@dataclass
class RoundResult:
    run: str
    condition: str
    award_policy: str
    tasks: int
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0
    timeouts: int = 0
    request_errors: int = 0
    late_responses: int = 0
    duplicate_responses: int = 0
    manager_tokens: int = 0
    contractor_tokens: int = 0
    monitor_tokens: int = 0
    validation_cost: int = 0
    validated_successes: int = 0
    interventions: int = 0
    avg_reputation: dict[str, float] = field(default_factory=dict)

    @property
    def quality_per_1k_tokens(self) -> float | None:
        total = self.manager_tokens + self.contractor_tokens + self.monitor_tokens
        return self.correct * 1000 / total if total else None


class ContractNetManager:
    def __init__(
        self,
        backend: Backend,
        *,
        harness: str = "react",
        bid_timeout_seconds: float = 60.0,
        allow_write_tools: bool = False,
        log: Callable[[dict[str, Any]], None] = lambda event: None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.backend = backend
        self.harness = harness
        self.bid_timeout_seconds = bid_timeout_seconds
        self.allow_write_tools = allow_write_tools
        self.log = log
        self.clock = clock
        self.monitor = Monitor()
        self._awarded_auctions: set[str] = set()

    def _collect_sequential(
        self,
        auction_id: str,
        task: dict[str, Any],
        team: list[Contractor],
        profiles: dict[str, Profile],
    ) -> tuple[FrozenResponses, str, float]:
        started = self.clock()
        expected: dict[str, str] = {}
        responses: list[BidEnvelope] = []
        for contractor in team:
            request_id = f"{auction_id}-{contractor.contractor_id}"
            expected[request_id] = contractor.contractor_id
            self.log(
                {
                    "event": "announcement",
                    "auction_id": auction_id,
                    "request_id": request_id,
                    "contractor_id": contractor.contractor_id,
                    "task_id": task["id"],
                    "task": task["desc"],
                    "profile": profiles[contractor.contractor_id].as_dict(),
                }
            )
            attempt = request_bid(self.backend, contractor, task)
            responses.append(
                BidEnvelope(
                    auction_id,
                    request_id,
                    contractor.contractor_id,
                    self.clock() - started,
                    attempt,
                    profiles[contractor.contractor_id],
                )
            )
        elapsed = self.clock() - started
        return freeze_responses(responses, auction_id, expected, float("inf")), "all_finished", elapsed

    def _collect_async(
        self,
        auction_id: str,
        task: dict[str, Any],
        team: list[Contractor],
        profiles: dict[str, Profile],
    ) -> tuple[FrozenResponses, str, float]:
        started = self.clock()
        deadline = self.bid_timeout_seconds
        expected: dict[str, str] = {}
        futures: dict[Future[tuple[BidAttempt, float]], tuple[str, str, Profile]] = {}
        executor = ThreadPoolExecutor(max_workers=len(team), thread_name_prefix="week03-bid")

        def call(contractor: Contractor) -> tuple[BidAttempt, float]:
            attempt = request_bid(self.backend, contractor, task)
            return attempt, self.clock() - started

        for contractor in team:
            request_id = f"{auction_id}-{contractor.contractor_id}"
            expected[request_id] = contractor.contractor_id
            profile = profiles[contractor.contractor_id]
            self.log(
                {
                    "event": "announcement",
                    "auction_id": auction_id,
                    "request_id": request_id,
                    "contractor_id": contractor.contractor_id,
                    "task_id": task["id"],
                    "task": task["desc"],
                    "deadline_seconds": deadline,
                    "profile": profile.as_dict(),
                }
            )
            future = executor.submit(call, contractor)
            futures[future] = (contractor.contractor_id, request_id, profile)

        remaining = max(0.0, deadline - (self.clock() - started))
        done, pending = wait(futures, timeout=remaining)
        responses: list[BidEnvelope] = []
        for future in done:
            contractor_id, request_id, profile = futures[future]
            try:
                attempt, received_at = future.result()
            except Exception as exc:
                attempt = BidAttempt("request_error", "", error=f"{type(exc).__name__}: {exc}")
                received_at = self.clock() - started
            responses.append(
                BidEnvelope(auction_id, request_id, contractor_id, received_at, attempt, profile)
            )

        log_lock = threading.Lock()
        for future in pending:
            contractor_id, request_id, _ = futures[future]

            def note_late(
                completed: Future[tuple[BidAttempt, float]],
                cid: str = contractor_id,
                rid: str = request_id,
            ) -> None:
                if completed.cancelled():
                    return
                try:
                    _, received = completed.result()
                    detail = f"received_at={received:.6f}"
                except Exception as exc:
                    detail = f"late request error: {type(exc).__name__}: {exc}"
                with log_lock:
                    self.log(
                        {
                            "event": "late_response",
                            "auction_id": auction_id,
                            "request_id": rid,
                            "contractor_id": cid,
                            "detail": detail,
                            "ignored": True,
                        }
                    )

            future.add_done_callback(note_late)
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

        elapsed = self.clock() - started
        frozen = freeze_responses(responses, auction_id, expected, deadline)
        reason = "deadline" if pending or frozen.pending_contractors else "all_finished"
        return frozen, reason, elapsed

    def _record_responses(self, frozen: FrozenResponses, result: RoundResult) -> list[BidEnvelope]:
        for rejection in frozen.rejected:
            self.log(asdict(rejection))
            if rejection.event == "late_response":
                result.late_responses += 1
            elif rejection.event == "duplicate_response":
                result.duplicate_responses += 1

        for contractor_id in frozen.pending_contractors:
            result.timeouts += 1
            self.log({"event": "timeout", "contractor_id": contractor_id})

        for response in frozen.accepted:
            attempt = response.attempt
            result.contractor_tokens += attempt.usage.total_tokens
            if attempt.status == "parse_fail":
                result.parse_fails += 1
                self.monitor.note_parse_fail(response.contractor_id)
            elif attempt.status == "request_error":
                result.request_errors += 1
            message = response.protocol_message()
            if message is not None and message["bid"]:
                result.messages += 1
            self.log(
                {
                    "event": "bid_response",
                    "status": attempt.status,
                    "message": message,
                    "raw": attempt.raw if message is None else None,
                    "error": attempt.error or None,
                    "received_at": response.received_at,
                    "profile_version": response.profile.profile_version,
                    "token_history_status": response.profile.token_history_status,
                    "usage_tokens": attempt.usage.total_tokens,
                }
            )
        return valid_candidates(frozen.accepted)

    def _award_once(self, auction_id: str, candidates: list[BidEnvelope], policy: str) -> AwardDecision:
        if auction_id in self._awarded_auctions:
            raise RuntimeError(f"auction already awarded: {auction_id}")
        decision = choose_winner(candidates, policy)
        self._awarded_auctions.add(auction_id)
        return decision

    def run_round(
        self,
        tasks: list[dict[str, Any]],
        *,
        run_id: str,
        condition: str,
        award_policy: str = "confidence_only",
        async_bids: bool = False,
    ) -> RoundResult:
        if award_policy not in POLICIES:
            raise ValueError(f"unknown award policy: {award_policy}")
        team = make_team(condition)
        by_id = {contractor.contractor_id: contractor for contractor in team}
        result = RoundResult(run_id, condition, award_policy, tasks=len(tasks))

        for index, task in enumerate(tasks, 1):
            auction_id = f"{run_id}-{condition}-{award_policy}-{index:02d}-{task['id']}"
            profiles = {
                contractor.contractor_id: self.monitor.snapshot(
                    contractor.contractor_id, task["domain"]
                )
                for contractor in team
            }
            result.messages += len(team)  # one announcement per contractor
            if async_bids:
                frozen, close_reason, elapsed = self._collect_async(
                    auction_id, task, team, profiles
                )
            else:
                frozen, close_reason, elapsed = self._collect_sequential(
                    auction_id, task, team, profiles
                )
            self.log(
                {
                    "event": "collection_closed",
                    "auction_id": auction_id,
                    "reason": close_reason,
                    "collection_wait_ms": round(elapsed * 1000, 3),
                    "responses": len(frozen.accepted),
                    "pending": frozen.pending_contractors,
                }
            )
            candidates = self._record_responses(frozen, result)
            award = self._award_once(auction_id, candidates, award_policy)
            score_rows = [asdict(score) for score in award.scores]
            if award.winner is None:
                result.unassigned += 1
                self.log(
                    {
                        "event": "unassigned",
                        "auction_id": auction_id,
                        "task_id": task["id"],
                        "candidate_scores": score_rows,
                    }
                )
                continue

            result.messages += 1
            if award.winner == task["gold"]:
                result.correct += 1
            else:
                result.misawards += 1
            self.log(
                {
                    "event": "award",
                    "auction_id": auction_id,
                    "task_id": task["id"],
                    "winner": award.winner,
                    "gold": task["gold"],
                    "correct": award.winner == task["gold"],
                    "policy": award_policy,
                    "tie_break_used": award.tie_break_used,
                    "candidate_scores": score_rows,
                }
            )

            try:
                agent_run = execute_task(
                    self.harness,
                    self.backend,
                    by_id[award.winner],
                    task,
                    allow_write=self.allow_write_tools,
                    log=self.log,
                )
                result.contractor_tokens += agent_run.usage.total_tokens
                result.interventions += agent_run.interventions
                task_tokens = agent_run.usage.total_tokens or None
                validation = self.monitor.evaluate_and_update(
                    award.winner, task, agent_run.answer, task_tokens
                )
                result.validation_cost += validation.cost
                if validation.success:
                    result.validated_successes += 1
                self.log(
                    {
                        "event": "execution_result",
                        "auction_id": auction_id,
                        "contractor_id": award.winner,
                        "harness": self.harness,
                        "status": agent_run.status,
                        "answer": agent_run.answer,
                        "task_tokens": task_tokens,
                        "iterations": agent_run.iterations,
                        "interventions": agent_run.interventions,
                        "validation_success": validation.success,
                        "validation_detail": validation.detail,
                    }
                )
            except Exception as exc:
                validation = self.monitor.evaluate_and_update(
                    award.winner, task, "", None
                )
                result.validation_cost += validation.cost
                self.log(
                    {
                        "event": "execution_error",
                        "auction_id": auction_id,
                        "contractor_id": award.winner,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

        result.validation_cost = self.monitor.validation_cost
        result.avg_reputation = self.monitor.reputation_summary()
        return result
