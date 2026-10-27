"""Safe, structured Codex CLI boundary shared by the buyer and seller."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any


CODEX_MODEL = "gpt-6.1-sol"
CODEX_REASONING_EFFORT = "low"
DEFAULT_TIMEOUT_SECONDS = 120
SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


@dataclass(frozen=True)
class HarnessConfig:
    executable: str = "codex"
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class NegotiationTurn:
    role: str
    message: str
    price: int | None = None


@dataclass(frozen=True)
class SellerDecisionContext:
    item: str
    reserve: int
    state: str
    last_buyer_offer: int | None
    history: tuple[NegotiationTurn, ...] = ()
    remaining_buyer_decisions: int | None = None
    is_final_decision: bool | None = None


@dataclass(frozen=True)
class BuyerDecisionContext:
    item: str
    budget: int
    current_ask: int | None
    task_id: str | None
    remaining_budget: int
    last_buyer_offer: int | None = None
    history: tuple[NegotiationTurn, ...] = ()
    remaining_buyer_decisions: int | None = None
    is_final_decision: bool | None = None


@dataclass(frozen=True)
class CatalogCandidate:
    id: str
    name: str
    description: str


@dataclass(frozen=True)
class CatalogSelectionContext:
    request: str
    candidates: tuple[CatalogCandidate, ...]


class ErrorType(str, Enum):
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    MODEL_ACCESS_DENIED = "MODEL_ACCESS_DENIED"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    INVALID_CLI_OPTION = "INVALID_CLI_OPTION"
    SCHEMA_ERROR = "SCHEMA_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    SANDBOX_PERMISSION_ERROR = "SANDBOX_PERMISSION_ERROR"
    TIMEOUT = "TIMEOUT"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


SAFE_ERROR_DESCRIPTIONS = {
    ErrorType.MODEL_NOT_FOUND: "The requested model was not recognized.",
    ErrorType.MODEL_ACCESS_DENIED: "The current account cannot access the requested model.",
    ErrorType.AUTHENTICATION_ERROR: "Codex authentication failed.",
    ErrorType.INVALID_CLI_OPTION: "Codex rejected a command-line option.",
    ErrorType.SCHEMA_ERROR: "Codex rejected or failed the response schema.",
    ErrorType.NETWORK_ERROR: "Codex could not reach its service.",
    ErrorType.SANDBOX_PERMISSION_ERROR: "Codex encountered a sandbox or file permission error.",
    ErrorType.TIMEOUT: "Codex exceeded the configured timeout.",
    ErrorType.UNKNOWN_ERROR: "Codex failed for an unclassified reason.",
}


class HarnessError(RuntimeError):
    """A safe failure that never contains raw Codex stderr."""

    def __init__(
        self,
        message: str,
        error_type: ErrorType = ErrorType.UNKNOWN_ERROR,
        exit_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.exit_code = exit_code


def _classify_stderr(stderr: str | None) -> ErrorType:
    """Classify in memory using allow-listed phrases; never return raw text."""
    text = (stderr or "").lower()
    # Specific service and CLI failures must win over generic transport text.
    # For example, an authentication response may also say that its HTTP
    # request failed, but it should remain an authentication error.
    rules = (
        (ErrorType.MODEL_NOT_FOUND,
         ("model not found", "unknown model", "unsupported model", "model does not exist")),
        (ErrorType.MODEL_ACCESS_DENIED,
         ("model access denied", "not have access to", "model is not available for your account",
          "permission to use this model")),
        (ErrorType.AUTHENTICATION_ERROR,
         ("authentication failed", "not logged in", "unauthorized", "invalid api key", "status 401")),
        (ErrorType.INVALID_CLI_OPTION,
         ("unexpected argument", "unknown option", "unrecognized option", "invalid cli option")),
        (ErrorType.SCHEMA_ERROR,
         ("invalid json schema", "output schema", "output-schema", "schema validation")),
        (ErrorType.SANDBOX_PERMISSION_ERROR,
         ("sandbox permission", "permission denied", "read-only file system", "operation not permitted")),
        (ErrorType.NETWORK_ERROR,
         ("network error", "connection refused", "failed to connect", "connect failed",
          "dns error", "could not resolve", "service unavailable", "error sending request",
          "failed to send request", "stream disconnected before completion",
          "request timed out")),
    )
    for error_type, phrases in rules:
        if any(phrase in text for phrase in phrases):
            return error_type

    # "request failed" alone is too broad: it may describe a malformed
    # request or an internal service failure. Treat it as a transport failure
    # only when an allow-listed network context is present.
    network_context = (
        "network", "connection", "connect", "dns", "proxy", "tls", "socket",
        "websocket", "http", "https",
    )
    if "request failed" in text and any(context in text for context in network_context):
        return ErrorType.NETWORK_ERROR
    return ErrorType.UNKNOWN_ERROR


def _process_error(error_type: ErrorType, exit_code: int | None = None) -> HarnessError:
    suffix = f" (exit status {exit_code})" if exit_code is not None else ""
    return HarnessError(
        f"{error_type.value}: {SAFE_ERROR_DESCRIPTIONS[error_type]}{suffix}",
        error_type=error_type,
        exit_code=exit_code,
    )


def _command(config: HarnessConfig, schema_path: Path, output_path: Path) -> list[str]:
    return [
        config.executable,
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--sandbox",
        "read-only",
        "--model",
        CODEX_MODEL,
        "--config",
        f'model_reasoning_effort="{CODEX_REASONING_EFFORT}"',
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "-",
    ]


def _stop_process_group(process: subprocess.Popen[str]) -> None:
    """Stop Codex and any children it started, without logging their output."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    return False


def _validate_schema(value: Any, schema: dict[str, Any]) -> None:
    """Validate the JSON-Schema subset used by the committed decision schemas."""
    if schema.get("type") == "object" and not isinstance(value, dict):
        raise HarnessError("Codex output failed schema validation")
    required = schema.get("required", [])
    if any(key not in value for key in required):
        raise HarnessError("Codex output failed schema validation")
    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False and any(key not in properties for key in value):
        raise HarnessError("Codex output failed schema validation")
    for key, item in value.items():
        rule = properties.get(key, {})
        expected = rule.get("type")
        types = expected if isinstance(expected, list) else [expected]
        if expected is not None and not any(_matches_type(item, kind) for kind in types):
            raise HarnessError("Codex output failed schema validation")
        if "enum" in rule and item not in rule["enum"]:
            raise HarnessError("Codex output failed schema validation")
        if isinstance(item, str) and len(item) < rule.get("minLength", 0):
            raise HarnessError("Codex output failed schema validation")
        if isinstance(item, int) and not isinstance(item, bool) and item < rule.get("minimum", item):
            raise HarnessError("Codex output failed schema validation")


def _validate_action_price(decision: dict[str, Any]) -> None:
    action, price = decision["action"], decision["price"]
    if action in {"reject", "walk_away"}:
        valid = price is None
    else:
        valid = isinstance(price, int) and not isinstance(price, bool)
    if not valid:
        raise HarnessError("Codex output has an invalid action-price combination")


def run_decision(
    prompt: str,
    schema_path: Path,
    config: HarnessConfig = HarnessConfig(),
) -> dict[str, Any]:
    """Run Codex exactly once and return a locally validated JSON decision.

    The caller decides whether invoking a model is authorized. This function
    never retries and never falls back to another model.
    """
    if not prompt.strip():
        raise HarnessError("Codex prompt is empty")
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(
            "SCHEMA_ERROR: Decision schema is unavailable or invalid.",
            error_type=ErrorType.SCHEMA_ERROR,
        ) from exc

    with tempfile.TemporaryDirectory(prefix="week06-codex-") as tmp:
        workdir = Path(tmp)
        isolated_schema = workdir / "decision.schema.json"
        output_path = workdir / "decision.json"
        isolated_schema.write_text(json.dumps(schema), encoding="utf-8")
        command = _command(config, isolated_schema, output_path)
        try:
            process = subprocess.Popen(
                command,
                cwd=workdir,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
                shell=False,
            )
        except OSError as exc:
            raise HarnessError("Codex CLI could not be started") from exc
        try:
            _, stderr = process.communicate(input=prompt, timeout=config.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            _stop_process_group(process)
            raise _process_error(ErrorType.TIMEOUT) from exc
        except BaseException:
            # A diagnostic-wide watchdog (or an operator interrupt) may fire
            # while Codex is active.  Do not let its separate process group
            # survive the Python caller.
            _stop_process_group(process)
            raise
        if process.returncode != 0:
            error_type = _classify_stderr(stderr)
            del stderr
            raise _process_error(error_type, process.returncode)
        try:
            decision = json.loads(output_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise HarnessError(
                "SCHEMA_ERROR: Codex did not produce an output file.",
                error_type=ErrorType.SCHEMA_ERROR,
            ) from exc
        except json.JSONDecodeError as exc:
            raise HarnessError(
                "SCHEMA_ERROR: Codex returned invalid JSON.",
                error_type=ErrorType.SCHEMA_ERROR,
            ) from exc
        _validate_schema(decision, schema)
        if "action" in decision or "price" in decision:
            _validate_action_price(decision)
        return decision


def _history_payload(history: tuple[NegotiationTurn, ...]) -> list[dict[str, Any]]:
    return [asdict(turn) for turn in history]


def decide_seller(
    context: SellerDecisionContext,
    config: HarnessConfig = HarnessConfig(),
) -> dict[str, Any]:
    payload = asdict(context)
    payload["history"] = _history_payload(context.history)
    diagnostic_guidance = ""
    if context.remaining_buyer_decisions is None:
        payload.pop("remaining_buyer_decisions")
        payload.pop("is_final_decision")
    else:
        diagnostic_guidance = (
            " The diagnostic allows "
            f"{context.remaining_buyer_decisions} more buyer decision(s) after this decision."
        )
        if context.is_final_decision:
            diagnostic_guidance += (
                " This is the final seller decision opportunity. If the current buyer offer "
                "is valid and at or above the reserve, actively consider accepting because "
                "another counteroffer will end the diagnostic without a deal. You still choose "
                "whether to accept, counter, or reject."
            )
    prompt = (
        "You are the seller's negotiation decision maker. Do not use tools. "
        "Choose the next action and return only the required JSON. "
        "Never ask or accept below the reserve."
        + diagnostic_guidance + "\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    decision = run_decision(prompt, SCHEMA_DIR / "seller_decision.json", config)
    price = decision["price"]
    if price is not None and price < context.reserve:
        raise HarnessError("Seller decision violates the reserve")
    return decision


def decide_buyer(
    context: BuyerDecisionContext,
    config: HarnessConfig = HarnessConfig(),
) -> dict[str, Any]:
    payload = asdict(context)
    payload["history"] = _history_payload(context.history)
    diagnostic_guidance = ""
    if context.remaining_buyer_decisions is None:
        payload.pop("remaining_buyer_decisions")
        payload.pop("is_final_decision")
    else:
        diagnostic_guidance = (
            " The diagnostic allows "
            f"{context.remaining_buyer_decisions} more buyer decision(s) after this decision."
        )
        if context.is_final_decision:
            diagnostic_guidance += (
                " This is your final buyer decision. Consider the chance of completing the deal "
                "when choosing an offer or acceptance, while never exceeding the remaining budget. "
                "You still choose whether to offer, accept, or walk away."
            )
    prompt = (
        "You are the buyer's negotiation decision maker. Do not use tools. "
        "Choose the next action and return only the required JSON. "
        "Never offer or accept above the remaining budget."
        + diagnostic_guidance + "\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    decision = run_decision(prompt, SCHEMA_DIR / "buyer_decision.json", config)
    price = decision["price"]
    limit = min(context.budget, context.remaining_budget)
    if price is not None and price > limit:
        raise HarnessError("Buyer decision violates the budget")
    return decision


def decide_catalog_item(
    context: CatalogSelectionContext,
    config: HarnessConfig = HarnessConfig(),
) -> dict[str, Any]:
    """Choose only among locally retrieved candidates; never expose the catalog."""
    if not context.candidates:
        raise HarnessError("Catalog search returned no candidates")
    payload = {
        "request": context.request,
        "candidates": [asdict(candidate) for candidate in context.candidates],
    }
    prompt = (
        "Match the buyer's request to exactly one candidate. Do not use tools. "
        "Return only the required JSON and copy selected_id exactly from the candidates.\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    decision = run_decision(prompt, SCHEMA_DIR / "catalog_selection.json", config)
    candidate_ids = {candidate.id for candidate in context.candidates}
    if decision["selected_id"] not in candidate_ids:
        raise HarnessError("Catalog selection is outside the retrieved candidates")
    return decision
