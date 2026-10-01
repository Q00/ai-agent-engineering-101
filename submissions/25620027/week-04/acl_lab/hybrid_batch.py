"""Stable, resumable execution of the twelve hybrid episodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from acl_lab.hybrid_domain import HybridContext, HybridLogRecord, HybridResultRecord
from acl_lab.hybrid_engine import run_hybrid_episode

if TYPE_CHECKING:
    from acl_lab.domain import ModelClient, Scenario, Settings
    from acl_lab.hybrid_archive import HybridArchive


@dataclass(frozen=True, slots=True)
class HybridBatch:
    """Run each repeat and scenario while preserving evidence immediately."""

    archive: HybridArchive
    settings: Settings
    scenarios: tuple[Scenario, ...]
    model: ModelClient

    def run(self) -> bool:
        """Execute missing keys and stop only on a fatal model failure."""
        completed = set(self.archive.completed_keys())
        for run in range(1, self.settings.repetitions + 1):
            for scenario in self.scenarios:
                scenario_id = str(scenario.id)
                key = (run, scenario_id)
                if key in completed:
                    continue
                execution = run_hybrid_episode(
                    HybridContext(scenario, self.settings.max_turns),
                    self.model,
                )
                self.archive.append_log(
                    HybridLogRecord(run, scenario_id, execution, self.settings)
                )
                self.archive.append(
                    HybridResultRecord(
                        run,
                        scenario_id,
                        int(scenario.deal_possible),
                        execution.result,
                    )
                )
                completed.add(key)
                if execution.stop_batch:
                    return False
        return True
