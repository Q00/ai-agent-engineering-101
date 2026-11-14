"""Identity-state and language-repair extension kept outside the graded conditions."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
from typing import Callable

from contract_net import CONTRACTOR_ORDER, _json_object
from identity_state import IdentityState


EXTENDED_PROTOCOL = """
You are bidding in a Contract Net negotiation. Use the supplied identity context,
but treat your self-view as a claim and the manager-view as revisable evidence.
Do not perform the task. Return only this JSON shape:
{
  "participate": boolean,
  "confidence": number from 0 to 100,
  "reason": string,
  "task_interpretation": string,
  "dimensions": {
    "task_understanding": number from 0 to 100,
    "capability": number from 0 to 100,
    "expected_success": number from 0 to 100,
    "willingness": number from 0 to 100
  }
}
The four dimensions have separate meanings. Do not use willingness or familiarity
as a synonym for expected_success. Claim high capability only when your declared
or manager-inferred capability tags support it. Follow the shared lexicon.
""".strip()


@dataclass(frozen=True)
class ExtendedBid:
    contractor: str
    participate: bool
    confidence: float
    reason: str
    task_interpretation: str
    dimensions: dict[str, float]
    raw: str
    parse_error: str | None = None
    warnings: tuple[str, ...] = ()

    def as_record(self) -> dict:
        return {
            "participate": self.participate,
            "confidence": self.confidence,
            "reason": self.reason,
            "task_interpretation": self.task_interpretation,
            "dimensions": self.dimensions,
        }


def prompt_for(contractor: str, identity_state: IdentityState) -> str:
    context = json.dumps(identity_state.context_for(contractor), ensure_ascii=False)
    return f"{EXTENDED_PROTOCOL}\n\nIDENTITY CONTEXT:\n{context}"


def parse_extended_bid(contractor: str, raw: str) -> ExtendedBid:
    try:
        data = _json_object(raw)
        participate = data.get("participate")
        confidence = _score(data.get("confidence"), "confidence")
        reason = data.get("reason")
        interpretation = data.get("task_interpretation")
        dimensions = data.get("dimensions")
        if type(participate) is not bool:
            raise ValueError("participate must be boolean")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        if not isinstance(interpretation, str) or not interpretation.strip():
            raise ValueError("task_interpretation must be a non-empty string")
        if not isinstance(dimensions, dict):
            raise ValueError("dimensions must be an object")
        parsed_dimensions = {
            key: _score(dimensions.get(key), key)
            for key in (
                "task_understanding", "capability", "expected_success", "willingness"
            )
        }
        bid = ExtendedBid(
            contractor, participate, confidence, reason.strip(),
            interpretation.strip(), parsed_dimensions, raw,
        )
        return ExtendedBid(**{**bid.__dict__, "warnings": tuple(find_warnings(bid))})
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return ExtendedBid(
            contractor, False, 0.0, "invalid bid", "unavailable",
            {key: 0.0 for key in (
                "task_understanding", "capability", "expected_success", "willingness"
            )},
            raw, str(exc), ("unparseable_bid",),
        )


def _score(value, field: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{field} must be numeric")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError(f"{field} must be between 0 and 100")
    return value


def find_warnings(bid: ExtendedBid) -> list[str]:
    dimensions = bid.dimensions
    warnings = []
    if abs(bid.confidence - dimensions["expected_success"]) > 20:
        warnings.append("confidence_expected_success_conflict")
    if bid.participate and dimensions["willingness"] < 50:
        warnings.append("participation_willingness_conflict")
    if bid.participate and dimensions["task_understanding"] < 50:
        warnings.append("low_understanding_for_bid")
    if bid.confidence >= 80 and dimensions["capability"] < 50:
        warnings.append("high_confidence_low_capability")
    return warnings


def clarification_prompt(announcement: dict, bid: ExtendedBid) -> str:
    return json.dumps({
        "instruction": (
            "Your bid contains the listed semantic conflicts. Re-read the shared "
            "definitions and return one corrected full bid in the original JSON shape."
        ),
        "announcement": announcement,
        "previous_bid": bid.raw,
        "warnings": list(bid.warnings),
    }, ensure_ascii=False)


def add_profile_warnings(
    bid: ExtendedBid, announcement: dict, identity_state: IdentityState
) -> ExtendedBid:
    required = set(announcement.get("required_capabilities", []))
    supported = identity_state.supported_capabilities(bid.contractor)
    extra = []
    if bid.dimensions["capability"] >= 80 and required.isdisjoint(supported):
        extra.append("capability_not_supported_by_profile")
    return replace(bid, warnings=tuple(dict.fromkeys((*bid.warnings, *extra))))


def choose_extended_winner(
    bids: list[ExtendedBid],
    identity_state: IdentityState,
    required_capabilities: list[str],
) -> tuple[ExtendedBid | None, dict[str, dict]]:
    scores = {}
    winner = None
    winner_score = -1.0
    for bid in bids:
        if not bid.participate or bid.parse_error:
            continue
        evidence = identity_state.manager_evidence(
            bid.contractor, required_capabilities
        )
        evidence_weight = evidence["observations"] / (
            evidence["observations"] + 4
        )
        observed_reliability = evidence["reliability"]
        if evidence["recent_success_rate"] is not None:
            observed_reliability = (
                observed_reliability * 0.7
                + evidence["recent_success_rate"] * 0.3
            )
        raw_score = bid.dimensions["expected_success"]
        blended_score = (
            raw_score * (1 - evidence_weight)
            + observed_reliability * 100 * evidence_weight
        )
        calibration_gap = evidence["calibration_gap"] or 0.0
        calibration_penalty = min(
            20.0, calibration_gap * evidence_weight * 0.25
        )
        semantic_penalty = 15.0 if bid.warnings else 0.0
        score = max(0.0, blended_score - calibration_penalty - semantic_penalty)
        scores[bid.contractor] = {
            "final": round(score, 3),
            "raw_expected_success": raw_score,
            "evidence_source": evidence["source"],
            "evidence_count": evidence["observations"],
            "evidence_weight": round(evidence_weight, 3),
            "observed_reliability": round(observed_reliability, 3),
            "calibration_gap": calibration_gap,
            "calibration_penalty": round(calibration_penalty, 3),
            "semantic_penalty": semantic_penalty,
        }
        if score > winner_score:
            winner = bid
            winner_score = score
    return winner, scores


def run_extended_contract_net(
    tasks: list[dict],
    chat: Callable[[str, str], str],
    emit: Callable[..., None],
    identity_state: IdentityState,
) -> dict[str, int]:
    metrics = {
        "tasks": len(tasks),
        "correct": 0,
        "messages": 0,
        "unassigned": 0,
        "misawards": 0,
        "clarifications": 0,
        "semantic_warnings": 0,
    }

    for task in tasks:
        announcement = {
            "id": task["id"],
            "desc": task["desc"],
            "required_capabilities": task.get("required_capabilities", []),
        }
        bids = []
        for contractor in CONTRACTOR_ORDER:
            emit("announcement", task=announcement, contractor=contractor)
            metrics["messages"] += 1
            system = prompt_for(contractor, identity_state)
            raw = chat(system, json.dumps(announcement, ensure_ascii=False))
            metrics["messages"] += 1
            bid = add_profile_warnings(
                parse_extended_bid(contractor, raw), announcement, identity_state
            )
            metrics["semantic_warnings"] += len(bid.warnings)
            emit("bid", task=task["id"], contractor=contractor, **bid.as_record(),
                 warnings=bid.warnings, parse_error=bid.parse_error, raw=raw)

            if bid.warnings:
                identity_state.observe_clarification(task["id"], contractor)
                emit("clarification_request", task=task["id"], contractor=contractor,
                     warnings=bid.warnings)
                metrics["messages"] += 1
                repaired_raw = chat(system, clarification_prompt(announcement, bid))
                metrics["messages"] += 1
                repaired = add_profile_warnings(
                    parse_extended_bid(contractor, repaired_raw), announcement, identity_state
                )
                metrics["clarifications"] += 1
                metrics["semantic_warnings"] += len(repaired.warnings)
                emit("clarification_response", task=task["id"], contractor=contractor,
                     **repaired.as_record(), warnings=repaired.warnings,
                     parse_error=repaired.parse_error, raw=repaired_raw)
                bid = repaired

            identity_state.observe_bid(task["id"], contractor, bid.as_record())
            bids.append(bid)

        winner, scores = choose_extended_winner(
            bids, identity_state, task.get("required_capabilities", [])
        )
        if winner is None:
            metrics["unassigned"] += 1
            identity_state.observe_unassigned(task["id"])
            emit("unassigned", task=task["id"], selection_scores=scores)
            continue

        metrics["messages"] += 1
        correct = winner.contractor == task["gold"]
        metrics["correct"] += int(correct)
        metrics["misawards"] += int(not correct)
        identity_state.observe_award(
            task["id"], winner.contractor, correct,
            task.get("required_capabilities", []),
            winner.confidence,
            winner.dimensions["expected_success"],
        )
        emit("award", task=task["id"], contractor=winner.contractor,
             selection_score=scores[winner.contractor]["final"],
             score_evidence=scores[winner.contractor], gold_match=correct)

    return metrics
