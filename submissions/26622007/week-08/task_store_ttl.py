"""An A2A task store that forgets old tasks. The SDK's InMemoryTaskStore keeps every task the shop ever answered, with
its whole history, and nothing in the SDK deletes one: ~870 talks an hour, 13-26 kB each, so a seller up for three days
held ~60 k tasks (11/15, seller.py at 5-6.7 GB). A finished task is dropped TERMINAL_TTL after its last save, any other
IDLE_TTL after it (Seller.sweep_stale drops the shop's own record of such a talk at the same age)."""
import sys
import time

from a2a.server.tasks import InMemoryTaskStore
from a2a.types.a2a_pb2 import TaskState

TERMINAL_TTL = 3600         # a finished talk: a buyer may still read it for a while
IDLE_TTL = 6 * 3600         # a talk silent this long is over (a hold lasts 300 s, a payment wait less than this)
SWEEP_EVERY = 60
TERMINAL = {TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_CANCELED,
            TaskState.TASK_STATE_REJECTED}


class ExpiringTaskStore(InMemoryTaskStore):
    def __init__(self, terminal_ttl=TERMINAL_TTL, idle_ttl=IDLE_TTL, sweep_every=SWEEP_EVERY, clock=time.time, **kw):
        super().__init__(**kw)
        self.terminal_ttl, self.idle_ttl, self.sweep_every, self.clock = terminal_ttl, idle_ttl, sweep_every, clock
        self.saved = {}         # (owner, task id) -> (last save, finished)
        self.last_sweep = clock()
        impl = getattr(self, "_impl", None)     # the SDK's private store (a2a-sdk 1.2.2): without it, store as the SDK does
        self.on = all(hasattr(impl, a) for a in ("tasks", "lock", "owner_resolver"))
        if not self.on:     # an a2a-sdk without these keeps every task again (5-6.7 GB in three days, 11/15)
            print("[seller] task TTL is off: this a2a-sdk's InMemoryTaskStore has no _impl.tasks/lock/owner_resolver, "
                  "every task is kept", file=sys.stderr, flush=True)

    async def save(self, task, context):
        await super().save(task, context)
        if not self.on:
            return
        now = self.clock()
        self.saved[(self._impl.owner_resolver(context), task.id)] = (now, task.status.state in TERMINAL)
        if now - self.last_sweep >= self.sweep_every:
            self.sweep(now)

    async def delete(self, task_id, context):
        await super().delete(task_id, context)
        if self.on:
            self.saved.pop((self._impl.owner_resolver(context), task_id), None)

    def sweep(self, now=None):
        """Drop the tasks past their TTL; the number dropped."""
        now = self.clock() if now is None else now
        self.last_sweep = now
        gone = 0
        with self._impl.lock:
            for key, (at, finished) in list(self.saved.items()):
                if now - at < (self.terminal_ttl if finished else self.idle_ttl):
                    continue
                owner, task_id = key
                tasks = self._impl.tasks.get(owner)
                if tasks is not None and tasks.pop(task_id, None) is not None:
                    gone += 1
                    if not tasks:
                        del self._impl.tasks[owner]
                del self.saved[key]
        return gone

    def size(self):
        with self._impl.lock:
            return sum(len(t) for t in self._impl.tasks.values())
