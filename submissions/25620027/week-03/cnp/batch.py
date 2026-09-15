"""Run reservation, immutable evidence, failure retention and CSV export."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from importlib.metadata import version
from time import monotonic
from typing import TYPE_CHECKING

from pydantic import ValidationError

from cnp.archive import RunMeta, export_csv, pending, rows
from cnp.domain import Task, runtime_task
from cnp.engine import BatchBlockedError, Engine, Trace
from cnp.evaluate import ResultRow, evaluate
from cnp.prompts import system_prompt
from cnp.protocol import AGENTS, Protocol, ProtocolError
from cnp.store import Store, StoreError

if TYPE_CHECKING:
    from pathlib import Path

    from cnp.adapter import ChatAdapter
    from cnp.settings import Settings


@dataclass(frozen=True, slots=True)
class Batch:
    """One immutable configuration with a reusable network client."""

    output: Path
    settings: Settings
    tasks: tuple[Task, ...]
    adapter: ChatAdapter

    def run(self, repetitions: int) -> bool:
        """Return false when a run crashed; retain all preceding evidence."""
        results = rows(self.output)
        export_csv(self.output, results)
        for condition in pending(results, repetitions):
            number = max((r.run for r in results), default=0) + 1
            meta = RunMeta(run=number, condition=condition)
            directory = self.output / "runs" / f"run-{number:03d}"
            directory.mkdir()
            with (directory / "meta.json").open("x", encoding="utf-8") as f:
                f.write(meta.model_dump_json())
            result = self.execute(meta, directory)
            with (directory / "result.json").open("x", encoding="utf-8") as f:
                f.write(result.model_dump_json(indent=2))
            results = (*results, result)
            export_csv(self.output, results)
            if result.tasks is None:
                return False
        return True

    def execute(self, meta: RunMeta, directory: Path) -> ResultRow:
        """Keep gold outside the runtime and score only after allocation closes."""
        log_path = self.output / "logs" / f"run-{meta.run:03d}-{meta.condition}.txt"
        with (
            log_path.open("x", encoding="utf-8") as log,
            closing(sqlite3.connect(directory / "state.sqlite")) as connection,
        ):
            trace = Trace(log)
            store = Store(connection)
            trace.emit("RUN", meta.model_dump_json())
            trace.emit("SETTINGS", self.settings.model_dump_json())
            trace.emit(
                "VERSIONS",
                json.dumps({name: version(name) for name in ("openai", "pydantic", "httpx2")}),
            )
            for agent in AGENTS:
                trace.emit(f"PROMPT {agent}", json.dumps(system_prompt(meta.condition, agent)))
            engine = Engine(
                meta.run,
                meta.condition,
                self.settings,
                Protocol(store, monotonic),
                self.adapter,
                trace,
            )
            try:
                contracts = engine.execute(tuple(runtime_task(t) for t in self.tasks))
                metrics = evaluate(contracts, self.tasks, store.events("protocol_messages"))
                result = ResultRow(
                    run=meta.run,
                    condition=meta.condition,
                    tasks=metrics.tasks,
                    correct=metrics.correct,
                    messages=metrics.messages,
                    unassigned=metrics.unassigned,
                    misawards=metrics.misawards,
                    note=metrics.note,
                )
                with (directory / "allocations.json").open("x", encoding="utf-8") as f:
                    f.write(
                        json.dumps(
                            [c.model_dump(mode="json") for c in contracts],
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
            except (
                BatchBlockedError,
                ProtocolError,
                StoreError,
                sqlite3.Error,
                OSError,
                ValidationError,
            ) as exc:
                # API details are sanitized by the adapter before reaching this boundary.
                trace.emit("CRASH", f"{type(exc).__name__}: {exc}")
                result = ResultRow(
                    run=meta.run,
                    condition=meta.condition,
                    note=f"crashed: {type(exc).__name__}: {exc}",
                )
            trace.emit("RESULT", result.model_dump_json())
            with (directory / "events.jsonl").open("x", encoding="utf-8") as f:
                for event in (*store.events("protocol_messages"), *store.events("system_events")):
                    f.write(event.model_dump_json() + "\n")
            return result
