"""Peer proposals and bounded recursive DAG execution. No network or answer keys."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from graphlib import CycleError, TopologicalSorter
import hashlib
import json
import math
import re
import time

WORKERS = ("A", "B", "C")
DIMENSIONS = ("coverage", "feasibility", "verification")
IDENTIFIER = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,79}$")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False).encode()).hexdigest()


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("nonfinite JSON constant")

    if not isinstance(raw, str) or len(raw) > 160_000:
        raise ValueError("response must be bounded JSON text")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("object keys must be: " + ", ".join(sorted(keys)))


def nonempty(value, label, limit=12000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label} must be nonempty text <= {limit} characters")
    return value


def names(value, label):
    if (not isinstance(value, list) or len(value) > 20
            or any(not isinstance(v, str) or not IDENTIFIER.fullmatch(v) for v in value)
            or len(value) != len(set(value))):
        raise ValueError(f"{label} must be a unique list of resource/task identifiers")
    return tuple(value)


@dataclass(frozen=True)
class Task:
    id: str
    goal: str
    acceptance: str
    depends_on: tuple = ()
    reads: tuple = ()
    writes: tuple = ()
    require_delegate: bool = False

    @classmethod
    def parse(cls, data, root=False):
        keys = {"id", "goal", "acceptance", "depends_on", "reads", "writes"}
        exact(data, keys | ({"require_delegate"} if root else set()))
        if not isinstance(data["id"], str) or not IDENTIFIER.fullmatch(data["id"]):
            raise ValueError("invalid task id")
        if root and type(data["require_delegate"]) is not bool:
            raise ValueError("require_delegate must be boolean")
        return cls(data["id"], nonempty(data["goal"], "goal"),
                   nonempty(data["acceptance"], "acceptance"),
                   names(data["depends_on"], "depends_on"),
                   names(data["reads"], "reads"), names(data["writes"], "writes"),
                   data.get("require_delegate", False))


@dataclass(frozen=True)
class Limits:
    max_depth: int = 5
    max_tasks: int = 12
    max_steps: int = 4
    max_calls: int = 64
    max_parallel: int = 3

    def __post_init__(self):
        for key, value in asdict(self).items():
            if type(value) is not int or value < (0 if key == "max_depth" else 1):
                raise ValueError(f"invalid {key}")
        if (self.max_depth > 5 or self.max_tasks > 100 or self.max_steps > 12
                or self.max_parallel > 3 or self.max_calls > 256):
            raise ValueError("limits exceed supported experiment bounds")


@dataclass(frozen=True)
class Proposal:
    bid: bool
    confidence: float
    reason: str
    plan: dict

    @classmethod
    def parse(cls, raw, task, depth, limits):
        data = decode(raw)
        exact(data, {"bid", "confidence", "reason", "plan"})
        if type(data["bid"]) is not bool:
            raise ValueError("bid must be boolean")
        confidence = data["confidence"]
        if type(confidence) not in (int, float) or not 0 <= confidence <= 100:
            raise ValueError("confidence must be finite and between 0 and 100")
        nonempty(data["reason"], "reason", 2000)
        plan = data["plan"]
        exact(plan, {"mode", "actions", "steps"})
        if plan["mode"] not in ("execute", "delegate"):
            raise ValueError("invalid plan mode")
        if not isinstance(plan["actions"], list) or not 1 <= len(plan["actions"]) <= 8:
            raise ValueError("plan needs 1..8 actions")
        for action in plan["actions"]:
            nonempty(action, "action", 1500)
        if not isinstance(plan["steps"], list):
            raise ValueError("steps must be a list")
        if plan["mode"] == "execute":
            if plan["steps"] or (task.require_delegate and data["bid"]):
                raise ValueError("execute has no child steps; root requires delegation")
        else:
            if depth >= limits.max_depth or not 1 <= len(plan["steps"]) <= limits.max_steps:
                raise ValueError("delegation depth or child count exceeded")
            validate_graph([Task.parse(step) for step in plan["steps"]])
        return cls(data["bid"], float(confidence), data["reason"], plan)


def validate_graph(tasks):
    ids = {task.id for task in tasks}
    if len(ids) != len(tasks):
        raise ValueError("duplicate child task id")
    if any(task.id in task.depends_on or not set(task.depends_on) <= ids for task in tasks):
        raise ValueError("unknown or self dependency")
    try:
        TopologicalSorter({task.id: set(task.depends_on) for task in tasks}).prepare()
    except CycleError:
        raise ValueError("cyclic task dependency") from None


def rotation(requester, offset=0):
    start = (WORKERS.index(requester) + offset) % len(WORKERS)
    return WORKERS[start:] + WORKERS[:start]


def select(raw, proposals, tie_order=WORKERS):
    scores = decode(raw)
    exact(scores, {"scores"})
    exact(scores["scores"], proposals)
    eligible = []
    for worker, proposal in proposals.items():
        values = scores["scores"][worker]
        exact(values, DIMENSIONS)
        if any(type(v) is not int or v not in (0, 1, 2) for v in values.values()):
            raise ValueError("review scores must be integers 0..2")
        if all(v >= 1 for v in values.values()):
            eligible.append(worker)
    if not eligible:
        raise ValueError("no proposal passed all review criteria")
    winner = min(eligible, key=lambda w: (-sum(scores["scores"][w].values()),
                                         -proposals[w].confidence, tie_order.index(w)))
    return winner, scores["scores"]


def parse_artifact(raw):
    data = decode(raw)
    exact(data, {"summary", "facts", "evidence"})
    nonempty(data["summary"], "summary", 20000)
    if not isinstance(data["facts"], dict) or len(data["facts"]) > 80:
        raise ValueError("facts must be an object of at most 80 scalar values")
    for key, value in data["facts"].items():
        nonempty(key, "fact key", 100)
        if type(value) not in (str, bool, int, float) or (isinstance(value, str) and len(value) > 2000):
            raise ValueError("fact values must be bounded strings, booleans or finite numbers")
        if type(value) in (int, float) and not -1e100 <= value <= 1e100:
            raise ValueError("fact number out of bounds")
    if not isinstance(data["evidence"], list) or not 1 <= len(data["evidence"]) <= 30:
        raise ValueError("evidence must contain 1..30 entries")
    for item in data["evidence"]:
        nonempty(item, "evidence", 3000)
    return data


class Resources:
    """Logical leases, held only while producing an artifact, never while awaiting children."""
    def __init__(self):
        self.condition = asyncio.Condition()
        self.active = {}

    @asynccontextmanager
    async def lease(self, task_id, reads, writes):
        reads, writes = set(reads), set(writes)
        def available():
            return all(not (writes & (r | w) or reads & w) for r, w in self.active.values())
        async with self.condition:
            await self.condition.wait_for(available)
            self.active[task_id] = (reads, writes)
        try:
            yield
        finally:
            async with self.condition:
                del self.active[task_id]
                self.condition.notify_all()


@dataclass
class Outcome:
    status: str
    worker: str = ""
    artifact: dict | None = None
    error: str = ""


class Runtime:
    def __init__(self, model, limits, emit=lambda *args, **kwargs: None, context=None, *, serialize_workers=False):
        self.model, self.limits, self.emit = model, limits, emit
        self.context = context
        self.serialize_workers = serialize_workers
        self.concurrency_policy = "worker-serial-v1" if serialize_workers else "task-worker-isolated-v1"
        self.slots = asyncio.Semaphore(limits.max_parallel)
        self.sessions = {}
        self.resources = Resources()
        self.calls = self.tasks = self.active_calls = self.peak_calls = 0
        self.outcomes = {}
        self.source = {}

    async def ask(self, worker, phase, payload, task_id):
        if self.context is not None:
            payload = dict(payload, personal_memory=self.context.personal(worker, payload, task_id, phase))
        # Reserve the call before awaiting. All counters and recorder writes stay on the event loop.
        if self.calls >= self.limits.max_calls:
            raise ValueError("model call budget exhausted")
        self.calls += 1
        request_sha = fingerprint({"worker": worker, "phase": phase, "payload": payload})
        # A worker is a role, not a shared conversation. Different task sessions may overlap.
        # Keep one call per task/worker session; the global budget still caps all API calls.
        session = worker if self.serialize_workers else (task_id, worker)
        lock = self.sessions.setdefault(session, asyncio.Lock())
        async with lock, self.slots:
            self.active_calls += 1
            self.peak_calls = max(self.peak_calls, self.active_calls)
            started = time.monotonic()
            self.emit("call_start", task_id=task_id, worker=worker, phase=phase,
                      request_sha=request_sha, active_calls=self.active_calls)
            try:
                raw = await self.model(worker, phase, payload, task_id)
                self.emit("model_reply", task_id=task_id, worker=worker, phase=phase,
                          request_sha=request_sha, raw=raw)
                return raw
            finally:
                self.active_calls -= 1
                self.emit("call_end", task_id=task_id, worker=worker, phase=phase,
                          elapsed_seconds=round(time.monotonic() - started, 6))

    async def run(self, task, requester="A"):
        if requester not in WORKERS or task.depends_on:
            raise ValueError("invalid root requester or root dependencies")
        if self.tasks:
            raise ValueError("create a fresh Runtime for each run")
        self.tasks = 1
        self.source = {"goal": task.goal, "acceptance": task.acceptance}
        return await self.solve(task, requester, 0, task.id, {})

    async def solve(self, task, requester, depth, path, inputs, tie_order=None):
        tie_order = tie_order or rotation(requester)
        self.emit("task_start", task_id=path, requester=requester, depth=depth,
                  task=asdict(task), input_tasks=sorted(inputs))
        try:
            payload = {"task": asdict(task), "depth": depth, "source": self.source,
                       "inputs": inputs, "max_depth": self.limits.max_depth,
                       "max_steps": self.limits.max_steps}
            if self.context is not None:
                payload["handoff_memory"] = self.context.handoff(requester, task, path)

            async def propose(worker):
                try:
                    raw = await self.ask(worker, "propose", payload, path)
                    proposal = Proposal.parse(raw, task, depth, self.limits)
                    self.emit("proposal", task_id=path, worker=worker, proposal=asdict(proposal))
                    return proposal if proposal.bid else None
                except Exception as exc:
                    self.emit("proposal_rejected", task_id=path, worker=worker, error=str(exc))
                    return None

            offered = await asyncio.gather(*(propose(w) for w in WORKERS))
            proposals = {w: p for w, p in zip(WORKERS, offered) if p is not None}
            if not proposals:
                raise ValueError("no valid bids")
            review_input = dict(payload, candidates={
                w: {"reason": p.reason, "plan": p.plan} for w, p in proposals.items()})
            winner, scores = select(await self.ask(requester, "review", review_input, path), proposals, tie_order)
            proposal = proposals[winner]
            self.emit("award", task_id=path, requester=requester, worker=winner,
                      scores=scores, confidence=proposal.confidence, tie_order=tie_order, plan=proposal.plan)
            execution = dict(payload, plan=proposal.plan)
            if proposal.plan["mode"] == "delegate":
                children = [Task.parse(step) for step in proposal.plan["steps"]]
                if self.tasks + len(children) > self.limits.max_tasks:
                    raise ValueError("total task budget exhausted")
                self.tasks += len(children)
                results = await self.run_graph(children, winner, depth + 1, path, inputs)
                if any(r.status != "succeeded" for r in results.values()):
                    raise ValueError("child failed or blocked; partial results retained")
                execution["children"] = {k: v.artifact for k, v in sorted(results.items())}
                phase = "synthesize"
            else:
                phase = "execute"
            async with self.resources.lease(path, task.reads, task.writes):
                self.emit("execution_start", task_id=path, worker=winner, phase=phase,
                          reads=task.reads, writes=task.writes)
                try:
                    artifact = parse_artifact(await self.ask(winner, phase, execution, path))
                finally:
                    self.emit("execution_end", task_id=path, worker=winner, phase=phase)
            outcome = Outcome("succeeded", winner, artifact)
            if self.context is not None:
                self.context.remember(winner, task, path, artifact)
        except asyncio.CancelledError:
            self.emit("task_cancelled", task_id=path)
            raise
        except Exception as exc:
            outcome = Outcome("failed", error=f"{type(exc).__name__}: {exc}")
        self.outcomes[path] = outcome
        self.emit("task_end", task_id=path, outcome=asdict(outcome))
        return outcome

    async def run_graph(self, tasks, requester, depth, parent, inherited):
        validate_graph(tasks)
        pending = {task.id: task for task in tasks}
        # Fixed by the accepted plan order, never by timing or current load; replay remains comparable.
        preferences = {task.id: rotation(requester, i + 1) for i, task in enumerate(tasks)}
        running, results = {}, {}
        try:
            while pending or running:
                for task_id, task in sorted(list(pending.items())):
                    if any(dep in results and results[dep].status != "succeeded" for dep in task.depends_on):
                        result = Outcome("blocked", error="predecessor did not succeed")
                        results[task_id] = result
                        self.outcomes[f"{parent}/{task_id}"] = result
                        self.emit("task_blocked", task_id=f"{parent}/{task_id}",
                                  depends_on=task.depends_on)
                        del pending[task_id]
                    elif all(dep in results for dep in task.depends_on):
                        # Parent inputs and local predecessor artifacts use distinct namespaces.
                        inputs = {"parent_inputs": inherited,
                                  "predecessors": {dep: results[dep].artifact for dep in sorted(task.depends_on)}}
                        running[task_id] = asyncio.create_task(
                            self.solve(task, requester, depth, f"{parent}/{task_id}", inputs, preferences[task_id]))
                        del pending[task_id]
                if running:
                    done, _ = await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
                    for task_id, future in list(running.items()):
                        if future in done:
                            results[task_id] = future.result()
                            del running[task_id]
                elif pending:
                    # A newly blocked predecessor may still need to propagate through the next pass.
                    if any(any(dep in results for dep in t.depends_on) for t in pending.values()):
                        continue
                    raise ValueError("unresolvable dependencies")
            return results
        finally:
            for future in running.values():
                future.cancel()
            if running:
                await asyncio.gather(*running.values(), return_exceptions=True)


def evaluate(outcome, expected):
    """Offline evaluation only. The runtime never receives expected values."""
    facts = outcome.artifact["facts"] if outcome.artifact else {}
    checks = {}
    for key, expected_value in expected.items():
        actual = facts.get(key)
        if type(expected_value) is bool:
            checks[key] = type(actual) is bool and actual == expected_value
        else:
            checks[key] = (type(actual) in (int, float)
                           and math.isclose(actual, expected_value, rel_tol=0, abs_tol=1e-6))
    return {"passed": outcome.status == "succeeded" and all(checks.values()), "checks": checks}
