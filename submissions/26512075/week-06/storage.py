"""
Task and Seller-state persistence, via REDIS_URL
"""

from __future__ import annotations

import json
import os
import hashlib
from typing import Any

from a2a.server.tasks import InMemoryTaskStore
from a2a.server.tasks.task_store import TaskStore

from a2a.server.owner_resolver import resolve_user_scope
from a2a.types import Task

TTL = int(os.getenv("TASK_TTL_SECONDS", str(30 * 24 * 3600)))


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
        value = await self.client.get(f"A2A:deal:{task_id}")
        return json.loads(value) if value is not None else None
    
    async def save(self, task_id: str, deal: dict[str, Any]) -> None:
        await self.client.set(f"A2A:deal:{task_id}", json.dumps(deal), ex=TTL)
    
    async def delete(self, task_id: str) -> None:
        await self.client.delete(f"A2A:deal:{task_id}")

class RedisTaskStore(TaskStore):
    def __init__(self, client):

        self.client = client
    
    @staticmethod
    def _prefix(context) -> str:
        owner = resolve_user_scope(context)
        owner_hash = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:16]
        
        return f"A2A:task:{owner_hash}:"
    
    
    async def save(self, task: Task, context) -> Task | None:
        data = await self.client.get(self._prefix(context) + task_id)
        if data is None:
            return None
        task = Task()
        task.ParseFromString(data)
        
        return task
    
    
    async def delete(self, task_id: str, context) -> None:
        await self.client.delete(self._prefix(context) + task_id)
    
    async def list(self, params, context):
        snapshot = InMemoryTaskStore()
        async for key in self.client.scan_iter(match = self._prefix(context) + "*"):
            task_id = key.decode().rsplit(":", 1)[-1] if isinstance(key, bytes) else key.rsplit(":", 1)[-1]
            task = await self.get(task_id, context)
            if task is not None:
                await snapshot.save(task, context)
            
        return await snapshot.list(params, context)
    

def stores():
    url = os.getenv("REDIS_URL")
    
    if not url:
            return InMemoryTaskStore(), MemoryDeals()

    from redis.asyncio import Redis
    client = Redis.from_url(url)
    
    return RedisTaskStore(client), RedisDeals(client)







