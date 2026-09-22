"""Run every condition and repetition while preserving resumable evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from acl_lab.domain import (
    Condition,
    EpisodeContext,
    LogRecord,
    ModelClient,
    ResultRecord,
    Scenario,
    Settings,
)
from acl_lab.engine import run_episode

if TYPE_CHECKING:
    from acl_lab.archive import ResultArchive


@dataclass(frozen=True, slots=True)
class Batch:
    """Execute the frozen matrix in a stable condition-run-scenario order."""

    archive: ResultArchive
    settings: Settings
    scenarios: tuple[Scenario, ...]
    model: ModelClient

    def run(self) -> bool:
        """Append each episode immediately and stop only on a fatal model failure."""
        completed = set(self.archive.completed_keys())
        for condition in Condition:
            for run in range(1, self.settings.repetitions + 1):
                for scenario in self.scenarios:
                    scenario_id = str(scenario.id)
                    key = (run, condition, scenario_id)
                    if key in completed:
                        continue
                    execution = run_episode(
                        EpisodeContext(scenario, condition, self.settings.max_turns),
                        self.model,
                    )
                    self.archive.append_log(
                        LogRecord(
                            run=run,
                            condition=condition,
                            scenario=scenario_id,
                            execution=execution,
                            settings=self.settings,
                        )
                    )
                    self.archive.append(
                        ResultRecord(
                            run=run,
                            condition=condition,
                            scenario=scenario_id,
                            deal_possible=int(scenario.deal_possible),
                            result=execution.result,
                        )
                    )
                    completed.add(key)
                    if execution.stop_batch:
                        return False
        return True
