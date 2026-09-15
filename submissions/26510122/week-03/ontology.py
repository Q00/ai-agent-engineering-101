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
        })
        self._event(
            "award_observed",
            task=task_id,
            contractor=contractor,
            gold_match=gold_match,
        )

    def observe_unassigned(self, task_id: str) -> None:
        self.data["manager"]["observer_view"]["negotiations"] += 1
        self._event("unassigned", task=task_id)

    def observe_clarification(self, task_id: str, contractor: str) -> None:
        self.data["manager"]["observer_view"]["clarification_requests"] += 1
        self._event("clarification", task=task_id, contractor=contractor)

    def reliability_for(self, contractor: str) -> float:
        value = self.data["contractors"][contractor]["manager_view"][
            "calibrated_reliability"
        ]
        return 1.0 if value is None else float(value)

    def supported_capabilities(self, contractor: str) -> set[str]:
        node = self.data["contractors"][contractor]
        claimed = node["self_view"].get("capability_tags", [])
        observed = node["manager_view"].get("inferred_skills", [])
        return set(claimed) | set(observed)

    def _event(self, kind: str, **data) -> None:
        self.data["shared_events"].append({
            "kind": kind,
            "at": datetime.now(timezone.utc).isoformat(),
            **data,
        })
