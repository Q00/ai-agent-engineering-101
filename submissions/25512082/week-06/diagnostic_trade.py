"""Bounded, opt-in live diagnostic for one Week 06 lamp negotiation.

Importing this module never starts a server or invokes Codex.  ``run_diagnostic``
is intentionally separate from the submitted seller and buyer commands so the
diagnostic limits do not alter their normal behavior.
"""
from __future__ import annotations

import math
import json
import signal
import socket
import threading
import time
from dataclasses import asdict, dataclass, replace
from typing import Callable
from unittest.mock import patch

import httpx
import uvicorn

import buyer
import harness
import seller
from harness import BuyerDecisionContext, HarnessConfig, SellerDecisionContext


MAX_MODEL_CALLS = 5
MAX_BUYER_ROUNDS = 2
TOTAL_TIMEOUT_SECONDS = 180
MODEL_TIMEOUT_SECONDS = 120
CLEANUP_GRACE_SECONDS = 10
WORK_TIMEOUT_SECONDS = TOTAL_TIMEOUT_SECONDS - CLEANUP_GRACE_SECONDS


class DiagnosticLimitError(RuntimeError):
    """Raised before a model call when a diagnostic safety limit is reached."""


class RecordedModelError(RuntimeError):
    """Carries only an allow-listed Harness error category, never raw stderr."""

    def __init__(self, error_type: str) -> None:
        super().__init__(error_type)
        self.error_type = error_type


@dataclass(frozen=True)
class DiagnosticResult:
    outcome: str
    purchase_succeeded: bool
    price: int | None
    model_calls: int
    seller_calls: int
    buyer_calls: int
    turns: tuple[DiagnosticTurn, ...] = ()


@dataclass(frozen=True)
class DiagnosticTurn:
    """Allow-listed decision metadata; never stores prompts or model reasoning."""

    turn: int
    role: str
    action: str | None
    price: int | None
    a2a_state: str | None
    termination_reason: str | None
    model_calls: int


class CallGuard:
    """One shared call/deadline guard for both seller and buyer decisions."""

    def __init__(
        self,
        *,
        max_calls: int = MAX_MODEL_CALLS,
        max_buyer_rounds: int = MAX_BUYER_ROUNDS,
        timeout_seconds: int = TOTAL_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_calls = max_calls
        self.max_buyer_rounds = max_buyer_rounds
        self._clock = clock
        self.deadline = clock() + timeout_seconds
        self._lock = threading.Lock()
        self.total_calls = 0
        self.seller_calls = 0
        self.buyer_calls = 0
        self.model_error_type: str | None = None
        self.turns: list[DiagnosticTurn] = []

    def _record(
        self,
        *,
        role: str,
        action: str | None,
        price: int | None,
        a2a_state: str | None,
        termination_reason: str | None,
    ) -> None:
        with self._lock:
            self.turns.append(DiagnosticTurn(
                turn=len(self.turns) + 1,
                role=role,
                action=action,
                price=price,
                a2a_state=a2a_state,
                termination_reason=termination_reason,
                model_calls=self.total_calls,
            ))

    def remaining_seconds(self) -> int:
        remaining = self.deadline - self._clock() - CLEANUP_GRACE_SECONDS
        # Floor rather than ceil so a subprocess timeout can never extend past
        # the monotonic deadline.  Keep a cleanup window for the Codex process
        # group, HTTP client, and in-process seller server.
        if remaining < 1:
            raise DiagnosticLimitError("DIAGNOSTIC_TIMEOUT")
        return min(MODEL_TIMEOUT_SECONDS, math.floor(remaining))

    def claim(self, role: str) -> HarnessConfig:
        """Atomically reserve one call across both roles before Codex starts."""
        timeout = self.remaining_seconds()
        with self._lock:
            if self.total_calls >= self.max_calls:
                raise DiagnosticLimitError("MODEL_CALL_LIMIT")
            if role == "buyer" and self.buyer_calls >= self.max_buyer_rounds:
                raise DiagnosticLimitError("BUYER_ROUND_LIMIT")
            if role not in {"seller", "buyer"}:
                raise ValueError("role must be seller or buyer")
            self.total_calls += 1
            if role == "seller":
                self.seller_calls += 1
            else:
                self.buyer_calls += 1
        return HarnessConfig(timeout_seconds=timeout)

    def decide_seller(self, context: SellerDecisionContext) -> dict:
        config = self.claim("seller")
        remaining = max(0, self.max_buyer_rounds - self.buyer_calls)
        context = replace(
            context,
            remaining_buyer_decisions=remaining,
            is_final_decision=remaining == 0,
        )
        try:
            decision = harness.decide_seller(context, config)
        except harness.HarnessError as exc:
            self.model_error_type = exc.error_type.value
            self._record(
                role="Seller", action=None, price=None,
                a2a_state=None, termination_reason="model_error",
            )
            raise
        action = decision["action"]
        if action in {"ask", "counter"}:
            state = buyer.INPUT_REQUIRED
            reason = "round_limit" if self.buyer_calls >= self.max_buyer_rounds else None
        elif action == "accept":
            state, reason = buyer.COMPLETED, "deal_completed"
        else:
            state, reason = buyer.REJECTED, "seller_rejected"
        self._record(
            role="Seller", action=action, price=decision["price"],
            a2a_state=state, termination_reason=reason,
        )
        return decision

    def decide_buyer(
        self,
        context: BuyerDecisionContext,
        _config: HarnessConfig = HarnessConfig(),
    ) -> dict:
        config = self.claim("buyer")
        remaining = max(0, self.max_buyer_rounds - self.buyer_calls)
        context = replace(
            context,
            remaining_buyer_decisions=remaining,
            is_final_decision=remaining == 0,
        )
        try:
            decision = harness.decide_buyer(context, config)
        except harness.HarnessError as exc:
            self.model_error_type = exc.error_type.value
            self._record(
                role="Buyer", action=None, price=None,
                a2a_state=None, termination_reason="model_error",
            )
            raise
        walked_away = decision["action"] == "walk_away"
        self._record(
            role="Buyer", action=decision["action"], price=decision["price"],
            a2a_state=buyer.CANCELED if walked_away else buyer.INPUT_REQUIRED,
            termination_reason="buyer_walked_away" if walked_away else None,
        )
        return decision


def integral_price(value: object) -> int:
    """Accept JSON integer-valued numbers, excluding bool and non-finite floats."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("receipt price must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise ValueError("receipt price must be finite and integer-valued")
    return int(numeric)


def classify_outcome(outcome: buyer.BuyerOutcome, guard: CallGuard) -> DiagnosticResult:
    """Separate a valid no-deal negotiation from a successful lamp purchase."""
    if outcome.outcome == "deal":
        price = integral_price(outcome.price)
        succeeded = 100 <= price <= 120
        label = "purchase_success" if succeeded else "purchase_contract_failure"
    elif outcome.outcome in {"walked_away", "rejected", "canceled", "round_limit"}:
        price = None
        succeeded = False
        label = outcome.outcome
    else:
        price = None
        succeeded = False
        label = "program_error"
    return DiagnosticResult(
        outcome=label,
        purchase_succeeded=succeeded,
        price=price,
        model_calls=guard.total_calls,
        seller_calls=guard.seller_calls,
        buyer_calls=guard.buyer_calls,
        turns=tuple(guard.turns),
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _RunningSeller:
    """In-process server; cleanup leaves no seller subprocess behind."""

    def __init__(self, startup_timeout: float = 5.0) -> None:
        self.port = _free_port()
        self.startup_timeout = startup_timeout
        config = uvicorn.Config(
            seller.build_app(self.port), host="127.0.0.1", port=self.port,
            log_level="error",
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> _RunningSeller:
        self.thread.start()
        deadline = time.monotonic() + self.startup_timeout
        card_url = f"{self.url}/.well-known/agent-card.json"
        while time.monotonic() < deadline:
            try:
                if httpx.get(card_url, timeout=0.2).status_code == 200:
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.02)
        self.server.should_exit = True
        self.thread.join(timeout=5)
        raise RuntimeError("diagnostic seller did not start")

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("diagnostic seller did not stop")


def run_diagnostic(guard: CallGuard | None = None) -> DiagnosticResult:
    """Run one bounded live trade; call only after explicit model-use approval.

    The shared guard is installed in both modules before either can invoke the
    harness.  Buyer constants are patched only for this context and restored on
    exit; the submitted default ``MAX_ROUNDS == 8`` remains unchanged.
    """
    guard = guard or CallGuard()
    with (
        patch("seller.decide_seller", side_effect=guard.decide_seller),
        patch("buyer.decide_buyer", side_effect=guard.decide_buyer),
        patch("buyer.MAX_ROUNDS", MAX_BUYER_ROUNDS),
        patch("buyer.RUN_TIMEOUT_SECONDS", TOTAL_TIMEOUT_SECONDS),
        _RunningSeller() as running,
        buyer.BuyerClient(running.url) as client,
    ):
        outcome = client.buy("lamp", 120)
    if guard.model_error_type is not None:
        raise RecordedModelError(guard.model_error_type)
    return classify_outcome(outcome, guard)


def _watchdog_expired(_signum, _frame) -> None:
    raise DiagnosticLimitError("DIAGNOSTIC_TIMEOUT")


def _failure_payload(exc: Exception, guard: CallGuard, elapsed: float) -> dict:
    if isinstance(exc, DiagnosticLimitError):
        error_type = str(exc)
        status = "timeout" if error_type == "DIAGNOSTIC_TIMEOUT" else "limit_error"
    elif isinstance(exc, RecordedModelError):
        error_type = exc.error_type
        status = "model_error"
    elif isinstance(exc, harness.HarnessError):
        error_type = exc.error_type.value
        status = "model_error"
    else:
        # Do not expose exception text: transport exceptions may contain URLs,
        # credentials, or raw service output.
        error_type = type(exc).__name__
        status = "program_error"
    return {
        "status": status,
        "outcome": "program_error",
        "purchase_succeeded": False,
        "price": None,
        "model_calls": guard.total_calls,
        "seller_calls": guard.seller_calls,
        "buyer_calls": guard.buyer_calls,
        "elapsed_seconds": round(elapsed, 3),
        "error_type": error_type,
        "turns": [asdict(turn) for turn in guard.turns],
    }


def main() -> int:
    """Run the one-trade diagnostic and always emit exactly one JSON result."""
    guard = CallGuard()
    started = time.monotonic()
    previous_handler = signal.signal(signal.SIGALRM, _watchdog_expired)
    # Stop active work early enough to leave a bounded cleanup window while
    # keeping the complete CLI run inside TOTAL_TIMEOUT_SECONDS.
    signal.setitimer(signal.ITIMER_REAL, WORK_TIMEOUT_SECONDS)
    try:
        result = run_diagnostic(guard)
        payload = asdict(result)
        payload["status"] = "success" if result.purchase_succeeded else "negotiation_end"
        payload["elapsed_seconds"] = round(time.monotonic() - started, 3)
        payload["error_type"] = None
        exit_code = 0 if result.purchase_succeeded else 2
    except Exception as exc:
        payload = _failure_payload(exc, guard, time.monotonic() - started)
        exit_code = 124 if payload["status"] == "timeout" else 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
