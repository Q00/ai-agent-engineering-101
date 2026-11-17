"""Persist SDK tasks and guarded negotiation summaries across process replacement."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING, Final, override

import anyio
from a2a.server.owner_resolver import resolve_user_scope
from a2a.server.tasks import InMemoryTaskStore, TaskStore
from a2a.types import Task
from pydantic import TypeAdapter

from market_a2a.models import HarnessError
from market_a2a.seller_policy import Negotiation

if TYPE_CHECKING:
    from pathlib import Path

    from a2a.server.context import ServerCallContext
    from a2a.types import ListTasksRequest, ListTasksResponse

NEGOTIATION: Final = TypeAdapter(Negotiation)
WIRE_ROWS: Final = TypeAdapter(tuple[tuple[bytes], ...])
STATE_ROWS: Final = TypeAdapter(tuple[tuple[str, str | None, bool], ...])


class PersistentSellerStore(TaskStore):
    """Keep SDK owner/list semantics while journaling state before publication."""

    def __init__(self, path: Path) -> None:
        """Open a private local database; secrets and model prompts are not stored."""
        self.path: Path = path
        self.memory: InMemoryTaskStore = InMemoryTaskStore()
        self.loaded: set[str] = set()
        self.load_lock: anyio.Lock = anyio.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as database, database:
            version = TypeAdapter(tuple[int]).validate_python(
                database.execute("PRAGMA user_version").fetchone()
            )[0]
            if version not in (0, 1):
                detail = "Unsupported seller state database version."
                raise HarnessError(detail)
            _ = database.execute("PRAGMA journal_mode=WAL")
            _ = database.execute("PRAGMA synchronous=FULL")
            _ = database.execute(
                """CREATE TABLE IF NOT EXISTS tasks
                (owner TEXT NOT NULL, task_id TEXT NOT NULL,
                payload BLOB NOT NULL, PRIMARY KEY(owner, task_id))"""
            )
            _ = database.execute(
                """CREATE TABLE IF NOT EXISTS negotiations
                (task_id TEXT PRIMARY KEY, payload TEXT,
                closed INTEGER NOT NULL CHECK(closed IN (0, 1)))"""
            )
            _ = database.execute("PRAGMA user_version=1")
        path.chmod(0o600)

    def _wire(self, owner: str) -> tuple[Task, ...]:
        with closing(sqlite3.connect(self.path)) as database:
            rows = WIRE_ROWS.validate_python(
                database.execute(
                    "SELECT payload FROM tasks WHERE owner=?", (owner,)
                ).fetchall()
            )
        tasks: list[Task] = []
        for (payload,) in rows:
            task = Task()
            _ = task.ParseFromString(payload)
            tasks.append(task)
        return tuple(tasks)

    async def _ensure_owner(self, context: ServerCallContext) -> None:
        owner = resolve_user_scope(context)
        async with self.load_lock:
            if owner in self.loaded:
                return
            tasks = await anyio.to_thread.run_sync(self._wire, owner)
            for task in tasks:
                await self.memory.save(task, context)
            self.loaded.add(owner)

    def _save_task(self, owner: str, task_id: str, payload: bytes) -> None:
        with closing(sqlite3.connect(self.path)) as database, database:
            _ = database.execute("PRAGMA synchronous=FULL")
            _ = database.execute(
                """INSERT INTO tasks(owner, task_id, payload) VALUES(?, ?, ?)
                ON CONFLICT(owner, task_id)
                DO UPDATE SET payload=excluded.payload""",
                (owner, task_id, payload),
            )

    @override
    async def save(self, task: Task, context: ServerCallContext) -> None:
        """Commit wire state before acknowledging the SDK save."""
        await self._ensure_owner(context)
        await anyio.to_thread.run_sync(
            self._save_task,
            resolve_user_scope(context),
            task.id,
            task.SerializeToString(),
        )
        await self.memory.save(task, context)

    @override
    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        """Load only the requesting SDK owner, including after restart."""
        await self._ensure_owner(context)
        return await self.memory.get(task_id, context)

    @override
    async def list(
        self, params: ListTasksRequest, context: ServerCallContext
    ) -> ListTasksResponse:
        """Delegate filtering and cursor pagination to the pinned SDK."""
        await self._ensure_owner(context)
        return await self.memory.list(params, context)

    def _delete_task(self, owner: str, task_id: str) -> None:
        with closing(sqlite3.connect(self.path)) as database, database:
            _ = database.execute(
                "DELETE FROM tasks WHERE owner=? AND task_id=?", (owner, task_id)
            )

    @override
    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        """Delete SDK wire state without reopening a closed negotiation."""
        await self._ensure_owner(context)
        await anyio.to_thread.run_sync(
            self._delete_task, resolve_user_scope(context), task_id
        )
        await self.memory.delete(task_id, context)

    def restore(self) -> tuple[dict[str, Negotiation], set[str]]:
        """Validate persisted summaries at startup; corrupt state fails closed."""
        with closing(sqlite3.connect(self.path)) as database:
            rows = STATE_ROWS.validate_python(
                database.execute(
                    "SELECT task_id, payload, closed FROM negotiations"
                ).fetchall()
            )
        opened: dict[str, Negotiation] = {}
        closed: set[str] = set()
        for task_id, payload, terminal in rows:
            if terminal:
                closed.add(task_id)
            elif payload is not None:
                negotiation = NEGOTIATION.validate_json(payload, strict=True)
                if (
                    negotiation.ask < negotiation.product.reserve
                    or negotiation.rounds < 0
                    or negotiation.incoming_offers < 0
                ):
                    detail = "Persisted seller state violates price or counter bounds."
                    raise HarnessError(detail)
                opened[task_id] = negotiation
            else:
                detail = "Open seller state lacks a negotiation summary."
                raise HarnessError(detail)
        return opened, closed

    def _journal(self, task_id: str, payload: str | None) -> None:
        with closing(sqlite3.connect(self.path)) as database, database:
            _ = database.execute("PRAGMA synchronous=FULL")
            if payload is None:
                _ = database.execute(
                    """INSERT INTO negotiations VALUES(?, NULL, 1)
                    ON CONFLICT(task_id) DO UPDATE SET payload=NULL, closed=1""",
                    (task_id,),
                )
            else:
                changed = database.execute(
                    """INSERT INTO negotiations VALUES(?, ?, 0)
                    ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload
                    WHERE negotiations.closed=0""",
                    (task_id, payload),
                ).rowcount
                if changed != 1:
                    detail = "A closed seller task cannot reopen."
                    raise HarnessError(detail)

    async def save_negotiation(self, task_id: str, negotiation: Negotiation) -> None:
        """Commit a guarded quote before exposing its price to the buyer."""
        await anyio.to_thread.run_sync(
            self._journal, task_id, NEGOTIATION.dump_json(negotiation).decode()
        )

    async def close(self, task_id: str) -> None:
        """Persist a terminal tombstone before publishing a receipt or rejection."""
        await anyio.to_thread.run_sync(self._journal, task_id, None)
