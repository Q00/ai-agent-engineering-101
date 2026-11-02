"""
Task and Seller-state persistence, via REDIS_URL
"""

from __future__ import annotations

import json
import os
import hashlib
from typing import Any

from redis.exceptions import WatchError
from a2a.server.cluster.task_store import (
    ConcurrentTaskModificationError, StoredTask, VersionedTaskStore,
)
from a2a.server.cluster.version import TaskVersion
from a2a.server.tasks import InMemoryTaskStore

from a2a.server.owner_resolver import resolve_user_scope
from a2a.types import Task, TaskState

TTL = int(os.getenv("TASK_TTL_SECONDS", str(30 * 24 * 3600)))
DEAL_KEY_PREFIX = "A2A:deal:"


# =============== utils ================
class MemoryDeals:
    def __init__(self):

        self.deals: dict[str, dict[str, Any]] = {}
    
    async def get(self, task_id: str) -> dict[str, Any] | None:
        return self.deals.get(task_id)
    
    async def save(self, task_id: str, deal: dict[str, Any]) -> None:
        self.deals[task_id] = deal

    async def delete(self, task_id: str) -> None:
        self.deals.pop(task_id, None)


class RedisDeals:
    
    def __init__(self, client):
        self.client = client
    
    async def get(self, task_id: str) -> dict[str, Any] | None:
        value = await self.client.get(DEAL_KEY_PREFIX + task_id)
        return json.loads(value) if value is not None else None
    
    async def save(self, task_id: str, deal: dict[str, Any]) -> None:
        await self.client.set(DEAL_KEY_PREFIX + task_id, json.dumps(deal), ex=TTL)
    
    async def delete(self, task_id: str) -> None:
        await self.client.delete(DEAL_KEY_PREFIX + task_id)

TERMINAL_STATES = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_REJECTED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_FAILED,
}


class RedisTaskStore(VersionedTaskStore):
    """Owner-scoped tasks with Redis transactions that reject stale writes."""

    def __init__(self, client):

        self.client = client
    
    @staticmethod
    def _prefix(context) -> str:
        owner = resolve_user_scope(context)
        owner_hash = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:16]
        
        return f"A2A:task:{owner_hash}:"
    
    
    async def save(self, task: Task, *, event=None, prev=None,
                   prev_version: TaskVersion, context) -> TaskVersion:
        del event, prev
        key = self._prefix(context) + task.id
        for _ in range(5):
            async with self.client.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(key)
                    stored = await pipe.hgetall(key)
                    version = int(stored[b"version"]) if stored else 0
                    if task.status.state == TaskState.TASK_STATE_CANCELED:
                        if not stored or int(stored[b"terminal"]):
                            if stored and int(stored[b"terminal"]):
                                await self.client.delete(DEAL_KEY_PREFIX + task.id)
                            raise ConcurrentTaskModificationError(task.id)
                    elif TaskVersion(version) != prev_version:
                        if stored and int(stored[b"terminal"]):
                            # A losing request may have written a counteroffer deal first.
                            await self.client.delete(DEAL_KEY_PREFIX + task.id)
                        raise ConcurrentTaskModificationError(task.id)

                    next_version = version + 1
                    pipe.multi()
                    pipe.hset(key, mapping={
                        "version": next_version,
                        "terminal": int(task.status.state in TERMINAL_STATES),
                        "task": task.SerializeToString(),
                    })
                    pipe.expire(key, TTL)
                    if task.status.state in TERMINAL_STATES:
                        pipe.delete(DEAL_KEY_PREFIX + task.id)
                    await pipe.execute()
                    return TaskVersion(next_version)
                except WatchError:
                    continue
        raise ConcurrentTaskModificationError(task.id)

    async def get(self, task_id: str, context) -> StoredTask | None:
        stored = await self.client.hgetall(self._prefix(context) + task_id)
        if not stored:
            return None
        task = Task()
        task.ParseFromString(stored[b"task"])
        return StoredTask(task, TaskVersion(int(stored[b"version"])))
    
    
    async def delete(self, task_id: str, context) -> None:
        await self.client.delete(self._prefix(context) + task_id)
    
    async def list(self, params, context):
        snapshot = InMemoryTaskStore()
        async for key in self.client.scan_iter(match = self._prefix(context) + "*"):
            task_id = key.decode().rsplit(":", 1)[-1] if isinstance(key, bytes) else key.rsplit(":", 1)[-1]
            stored = await self.get(task_id, context)
            if stored is not None:
                await snapshot.save(stored.task, context)
            
        return await snapshot.list(params, context)
    

def stores():
    url = os.getenv("REDIS_URL")
    
    if not url:
            return InMemoryTaskStore(), MemoryDeals()

    from redis.asyncio import Redis
    client = Redis.from_url(url)
    
    return RedisTaskStore(client), RedisDeals(client)







