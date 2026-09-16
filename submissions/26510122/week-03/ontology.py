"""Mutable social knowledge for the optional ontology-aware experiment."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path


class OntologyState:
    """Keep self claims, manager observations, and evidence as separate layers."""

    def __init__(self, data: dict) -> None:
        self.data = deepcopy(data)
        self.data.setdefault("shared_events", [])
        for contractor in self.data["contractors"].values():
            contractor.setdefault("self_history", [])
            view = contractor["manager_view"]
            view.setdefault("domain_reliability", {})
            view.setdefault("calibration", {
                "observations": 0,
                "average_claimed_confidence": None,
                "mean_absolute_gap": None,
                "brier_score": None,
            })
            view.setdefault("recent_trajectory", [])
            view.setdefault("recent_success_rate", None)

    @classmethod
    def from_file(cls, path: Path) -> "OntologyState":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def context_for(self, contractor: str) -> dict:
        node = self.data["contractors"][contractor]
        return {
            "your_identity": node["identity"],
            "your_self_view": node["self_view"],
            "manager_view_of_you": node["manager_view"],
            "manager_self_view": self.data["manager"]["self_view"],
            "shared_lexicon": self.data["shared_lexicon"],
            "recent_shared_evidence": self.data["shared_events"][-6:],
        }

    def observe_bid(self, task_id: str, contractor: str, bid: dict) -> None:
        node = self.data["contractors"][contractor]
        node["manager_view"]["observed_bids"] += 1
        node["self_history"].append({
            "task": task_id,
            "action": "bid" if bid["participate"] else "refuse",
            "confidence": bid["confidence"],
            "reason": bid["reason"],
        })
        self._event(
            "bid_observed",
            task=task_id,
            contractor=contractor,
            confidence=bid["confidence"],
            participate=bid["participate"],
        )

    def observe_award(
        self,
        task_id: str,
        contractor: str,
        gold_match: bool,
        required_capabilities: list[str],
        claimed_confidence: float,
        expected_success: float,
    ) -> None:
        manager = self.data["manager"]["observer_view"]
        manager["negotiations"] += 1
        manager["awards"] += 1
        manager["misawards"] += int(not gold_match)

        node = self.data["contractors"][contractor]
        view = node["manager_view"]
        view["awards"] += 1
        view["gold_matches"] += int(gold_match)
        view["misawards"] += int(not gold_match)
        view["calibrated_reliability"] = round(
            view["gold_matches"] / view["awards"], 3
        )
        self._update_domain_reliability(view, required_capabilities, gold_match)
        self._update_calibration(view, claimed_confidence, gold_match)
        self._update_recent_trajectory(
            view, task_id, required_capabilities, claimed_confidence,
            expected_success, gold_match,
        )
        if gold_match:
            for capability in required_capabilities:
                if capability not in view["inferred_skills"]:
                    view["inferred_skills"].append(capability)
        if view["awards"] >= 3 and view["calibrated_reliability"] >= 0.8:
            view["derived_status"] = "veteran"
        elif view["awards"] >= 2:
            view["derived_status"] = "observed"

        node["self_history"].append({
            "task": task_id,
            "action": "award",
            "gold_match": gold_match,
            "claimed_confidence": claimed_confidence,
            "expected_success": expected_success,
        })
        self._event(
            "award_observed",
            task=task_id,
            contractor=contractor,
            gold_match=gold_match,
            claimed_confidence=claimed_confidence,
            expected_success=expected_success,
        )

    def observe_unassigned(self, task_id: str) -> None:
        self.data["manager"]["observer_view"]["negotiations"] += 1
        self._event("unassigned", task=task_id)

    def observe_clarification(self, task_id: str, contractor: str) -> None:
        self.data["manager"]["observer_view"]["clarification_requests"] += 1
        self._event("clarification", task=task_id, contractor=contractor)

    def manager_evidence(
        self, contractor: str, required_capabilities: list[str]
    ) -> dict:
        """Return domain evidence with global history as a fallback."""
        view = self.data["contractors"][contractor]["manager_view"]
        matching = [
            view["domain_reliability"][capability]
            for capability in required_capabilities
            if capability in view["domain_reliability"]
        ]
        if matching:
            reliability = sum(item["reliability"] for item in matching) / len(matching)
            observations = max(item["awards"] for item in matching)
            source = "domain"
        else:
            reliability = view["calibrated_reliability"]
            observations = view["awards"]
            source = "global"
        return {
            "source": source,
            "observations": observations,
            "reliability": 0.5 if reliability is None else float(reliability),
            "recent_success_rate": view["recent_success_rate"],
            "calibration_gap": view["calibration"]["mean_absolute_gap"],
        }

    def supported_capabilities(self, contractor: str) -> set[str]:
        node = self.data["contractors"][contractor]
        claimed = node["self_view"].get("capability_tags", [])
        observed = node["manager_view"].get("inferred_skills", [])
        return set(claimed) | set(observed)

    @staticmethod
    def _update_domain_reliability(
        view: dict, capabilities: list[str], gold_match: bool
    ) -> None:
        for capability in capabilities:
            stats = view["domain_reliability"].setdefault(capability, {
                "awards": 0,
                "gold_matches": 0,
                "reliability": None,
            })
            stats["awards"] += 1
            stats["gold_matches"] += int(gold_match)
            stats["reliability"] = round(
                stats["gold_matches"] / stats["awards"], 3
            )

    @staticmethod
    def _update_calibration(
        view: dict, claimed_confidence: float, gold_match: bool
    ) -> None:
        calibration = view["calibration"]
        count = calibration["observations"]
        target = 100.0 if gold_match else 0.0
        absolute_gap = abs(claimed_confidence - target)
        squared_error = ((claimed_confidence / 100) - int(gold_match)) ** 2

        def running_average(previous, value):
            return value if count == 0 else (previous * count + value) / (count + 1)

        calibration["average_claimed_confidence"] = round(running_average(
            calibration["average_claimed_confidence"] or 0.0,
            claimed_confidence,
        ), 3)
        calibration["mean_absolute_gap"] = round(running_average(
            calibration["mean_absolute_gap"] or 0.0,
            absolute_gap,
        ), 3)
        calibration["brier_score"] = round(running_average(
            calibration["brier_score"] or 0.0,
            squared_error,
        ), 4)
        calibration["observations"] = count + 1

    @staticmethod
    def _update_recent_trajectory(
        view: dict,
        task_id: str,
        capabilities: list[str],
        claimed_confidence: float,
        expected_success: float,
        gold_match: bool,
    ) -> None:
        view["recent_trajectory"].append({
            "task": task_id,
            "required_capabilities": capabilities,
            "claimed_confidence": claimed_confidence,
            "expected_success": expected_success,
            "gold_match": gold_match,
        })
        view["recent_trajectory"] = view["recent_trajectory"][-8:]
        view["recent_success_rate"] = round(
            sum(int(item["gold_match"]) for item in view["recent_trajectory"])
            / len(view["recent_trajectory"]),
            3,
        )

    def _event(self, kind: str, **data) -> None:
        self.data["shared_events"].append({
            "kind": kind,
            "at": datetime.now(timezone.utc).isoformat(),
            **data,
        })
