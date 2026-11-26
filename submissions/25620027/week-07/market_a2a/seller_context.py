"""Recover the stored context for taskId-only requests after an SDK restart."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from a2a.server.agent_execution import RequestContext, RequestContextBuilder

if TYPE_CHECKING:
    from a2a.server.context import ServerCallContext
    from a2a.server.tasks import TaskStore
    from a2a.types import SendMessageRequest, Task


class StoredContextBuilder(RequestContextBuilder):
    """Resolve only identifiers; the SDK still reads current task state later."""

    def __init__(self, store: TaskStore) -> None:
        """Use the same owner-scoped store as the request handler."""
        self.store: TaskStore = store

    @override
    async def build(
        self,
        context: ServerCallContext,
        params: SendMessageRequest | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        task: Task | None = None,
    ) -> RequestContext:
        """Preserve explicit contexts; recover an omitted one from its owned task."""
        saved = task
        if saved is None and task_id is not None and context_id is None:
            saved = await self.store.get(task_id, context)
        resolved_context = context_id
        if resolved_context is None and saved is not None:
            resolved_context = saved.context_id
        return RequestContext(
            call_context=context,
            request=params,
            task_id=task_id,
            context_id=resolved_context,
            task=task,
        )
