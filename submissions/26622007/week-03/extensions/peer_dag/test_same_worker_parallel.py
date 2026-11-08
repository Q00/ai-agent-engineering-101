"""Same-role task sessions overlap without bypassing dependencies or resource leases."""
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from audit import audit
from cli import Recorder
from core import Limits, Runtime, Task
from fixtures import artifact, proposal, step
from models import messages

ROOT = Path(__file__).resolve().parent


class SameWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self, parallel=3, conflict=False, serialize_workers=False, require_overlap=False):
        both = asyncio.Event()
        active, completed, observed = set(), set(), []
        peak = 0
        async def model(worker, phase, payload, path):
            nonlocal peak
            local = payload["task"]["id"]
            if phase == "propose":
                children = [step("left", writes=("shared" if conflict else "left_result",)),
                            step("right", writes=("shared" if conflict else "right_result",)),
                            step("join", ("left", "right"))] if payload["depth"] == 0 else None
                return json.dumps(proposal(children, confidence=95 if worker == "C" else 80))
            if phase == "review":
                return json.dumps({"scores": {w: {d: 2 for d in ("coverage", "feasibility", "verification")}
                                               for w in payload["candidates"]}})
            self.assertEqual(worker, "C")
            if phase == "execute" and local in ("left", "right"):
                self.assertEqual(payload["inputs"]["predecessors"], {})
                request = messages(worker, phase, payload)
                self.assertEqual(len(request), 2)
                self.assertEqual(json.loads(request[1]["content"])["task"]["id"], local)
                active.add(local)
                peak = max(peak, len(active))
                observed.append((local, worker))
                if len(active) == 2:
                    both.set()
                if require_overlap:
                    # With the old role-wide lock this cannot complete; elapsed-time guesses are unnecessary.
                    await both.wait()
                await asyncio.sleep(0.01)
                active.remove(local)
                completed.add(local)
                return json.dumps(artifact({local: 1}))
            if local == "join":
                self.assertEqual(completed, {"left", "right"})
                predecessors = payload["inputs"]["predecessors"]
                self.assertEqual(predecessors["left"]["facts"], {"left": 1})
                self.assertEqual(predecessors["right"]["facts"], {"right": 1})
            return json.dumps(artifact({"combined": 2}))

        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            path = Path(folder) / "trace.jsonl"
            with path.open("w") as stream:
                recorder = Recorder(stream)
                limits = Limits(max_parallel=parallel)
                runtime = Runtime(model, limits, recorder.emit, serialize_workers=serialize_workers)
                recorder.emit("run_start", settings={"mode": "test", "limits": asdict(limits),
                              "concurrency_policy": runtime.concurrency_policy})
                root = Task.parse(json.loads((ROOT / "case.json").read_text()), root=True)
                result = await asyncio.wait_for(runtime.run(root), 2)
                recorder.emit("run_end", result={"evaluation": {"passed": result.status == "succeeded"}})
            self.assertEqual(result.status, "succeeded")
            self.assertEqual({w for _, w in observed}, {"C"})
            self.assertEqual(runtime.resources.active, {})
            self.assertTrue(all(not lock.locked() for lock in runtime.sessions.values()))
            report = audit(path)
            self.assertTrue(report["passed"], report)
            self.assertLessEqual(report["peak_calls"], parallel)
            self.assertEqual(report["peak_execution_calls_by_worker"]["C"], peak)
            return peak, [json.loads(line) for line in path.read_text().splitlines()]

    async def test_same_c_executes_independent_tasks_together_then_join(self):
        peak, _ = await self.scenario(require_overlap=True)
        self.assertEqual(peak, 2)

    async def test_same_c_still_serializes_resource_conflicts_and_global_limit_one(self):
        for settings in ({"conflict": True}, {"parallel": 1}, {"serialize_workers": True}):
            with self.subTest(settings=settings):
                peak, _ = await self.scenario(**settings)
                self.assertEqual(peak, 1)

    async def test_audit_distinguishes_task_sessions_and_preserves_legacy_rule(self):
        _, records = await self.scenario(require_overlap=True)
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            path = Path(folder) / "trace.jsonl"
            records[0]["settings"].pop("concurrency_policy")
            path.write_text("".join(json.dumps(row) + "\n" for row in records))
            self.assertTrue(any("same worker" in e for e in audit(path)["errors"]))
            records[0]["settings"]["concurrency_policy"] = "task-worker-isolated-v1"
            for row in records:
                if (row["event"] in ("call_start", "call_end") and row.get("phase") == "execute"
                        and row.get("task_id") == "release-review/right"):
                    row["task_id"] = "release-review/left"
            path.write_text("".join(json.dumps(row) + "\n" for row in records))
            self.assertTrue(any("same task/worker session" in e for e in audit(path)["errors"]))

    async def test_cancelling_parallel_c_calls_releases_sessions_and_slots(self):
        ready = asyncio.Event()
        started = set()
        async def waiting(worker, phase, payload, task_id):
            started.add(task_id)
            if len(started) == 2:
                ready.set()
            await asyncio.Event().wait()
        runtime = Runtime(waiting, Limits(max_parallel=2))
        jobs = [asyncio.create_task(runtime.ask("C", "execute", {}, task_id)) for task_id in ("left", "right")]
        try:
            await asyncio.wait_for(ready.wait(), 1)
        finally:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
        async def immediate(*args):
            return "fixture"
        runtime.model = immediate
        result = await asyncio.wait_for(runtime.ask("C", "execute", {}, "left"), 1)
        self.assertEqual(result, "fixture")
        self.assertEqual(runtime.active_calls, 0)
        self.assertTrue(all(not lock.locked() for lock in runtime.sessions.values()))


if __name__ == "__main__":
    unittest.main()
