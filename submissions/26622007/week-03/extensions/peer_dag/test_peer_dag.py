"""Offline behavioral tests. Fixture results are never evidence of model quality."""
import asyncio
import copy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import (Limits, Outcome, Proposal, Resources, Runtime, Task, decode, evaluate,
                  fingerprint, parse_artifact, select, validate_graph)
from fixtures import DemoModel, artifact, proposal, step
from models import PHASES, ReplayModel, messages

from response_formats import response_format

ROOT = Path(__file__).resolve().parent


def root():
    return Task.parse(json.loads((ROOT / "case.json").read_text()), root=True)


def task(name, deps=()):
    return Task.parse(step(name, deps))


class ProtocolTests(unittest.TestCase):
    def test_rejects_cycles_self_dependencies_missing_dependencies_and_duplicates(self):
        invalid = [[task("a", ("b",)), task("b", ("a",))], [task("a", ("a",))],
                   [task("a", ("missing",))], [task("a"), task("a")]]
        for graph in invalid:
            with self.subTest(graph=graph), self.assertRaises(ValueError):
                validate_graph(graph)

    def test_plan_depth_count_and_schema(self):
        valid = proposal([step("a"), step("b", ("a",))])
        self.assertTrue(Proposal.parse(json.dumps(valid), root(), 0, Limits()).bid)
        for candidate, depth, limits in [(valid, 2, Limits(max_depth=2)), (valid, 0, Limits(max_steps=1)),
                                         (proposal(), 0, Limits())]:
            with self.subTest(depth=depth, limits=limits), self.assertRaises(ValueError):
                Proposal.parse(json.dumps(candidate), root(), depth, limits)
        for value in (True, "90", -1, 101, float("nan"), float("inf")):
            candidate = proposal()
            candidate["confidence"] = value
            with self.subTest(confidence=value), self.assertRaises(ValueError):
                Proposal.parse(json.dumps(candidate), task("a"), 1, Limits())

    def test_depth_five_boundary_and_supported_configuration(self):
        limits = Limits()
        self.assertEqual(limits.max_depth, 5)
        for folder in (ROOT, ROOT.parent / "peer_research"):
            config = json.loads((folder / "config.json").read_text())
            configured = Limits(**{key: config[key] for key in asdict(limits)})
            self.assertEqual(configured.max_depth, 5)
        with self.assertRaises(ValueError):
            Limits(max_depth=6)
        delegated = json.dumps(proposal([step("child")]))
        self.assertEqual(Proposal.parse(delegated, task("parent"), 4, limits).plan["mode"], "delegate")
        with self.assertRaisesRegex(ValueError, "delegation depth"):
            Proposal.parse(delegated, task("parent"), 5, limits)
        self.assertEqual(Proposal.parse(json.dumps(proposal()), task("leaf"), 5, limits).plan["mode"], "execute")

    def test_parser_rejects_duplicate_keys_and_nonfinite_or_nested_facts(self):
        with self.assertRaises(ValueError):
            decode('{"bid":true,"bid":false}')
        for value in (float("nan"), float("inf"), {}, [], None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_artifact(json.dumps(artifact({"a": value})))
        with self.assertRaises(ValueError):
            Task.parse(dict(step("a"), id="../outside"))

    def test_score_precedes_confidence_and_ties_are_stable(self):
        offered = {w: Proposal.parse(json.dumps(proposal(confidence=99 if w == "C" else 80)),
                                    task("a"), 0, Limits()) for w in ("C", "B", "A")}
        raw = {"scores": {w: {d: 1 if w == "C" else 2 for d in
                              ("coverage", "feasibility", "verification")} for w in offered}}
        self.assertEqual(select(json.dumps(raw), offered)[0], "A")
        self.assertEqual(select(json.dumps(raw), offered, ("B", "C", "A"))[0], "B")
        raw["scores"]["A"]["coverage"] = True
        with self.assertRaises(ValueError):
            select(json.dumps(raw), offered)

    def test_evaluation_distinguishes_bool_from_numeric_and_needs_completed_result(self):
        expected = {"count": 1, "allowed": True}
        self.assertTrue(evaluate(Outcome("succeeded", artifact=artifact(expected)), expected)["passed"])
        self.assertFalse(evaluate(Outcome("succeeded", artifact=artifact({"count": True, "allowed": 1})), expected)["passed"])
        self.assertFalse(evaluate(Outcome("failed", artifact=artifact(expected)), expected)["passed"])


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_depth_five_chain_completes_and_depth_six_is_blocked(self):
        for target_depth in (5, 6):
            with self.subTest(target_depth=target_depth):
                events = []
                async def model(worker, phase, payload, path):
                    await asyncio.sleep(0)
                    if phase == "propose":
                        children = [step("child")] if payload["depth"] < target_depth else None
                        return json.dumps(proposal(children))
                    if phase == "review":
                        return json.dumps({"scores": {w: {d: 2 for d in
                            ("coverage", "feasibility", "verification")} for w in payload["candidates"]}})
                    if phase == "execute":
                        return json.dumps(artifact({"leaf_depth": payload["depth"]}))
                    return json.dumps(payload["children"]["child"])

                runtime = Runtime(model, Limits(), lambda event, **data: events.append({"event": event, **data}))
                outcome = await asyncio.wait_for(runtime.run(root()), 2)
                starts = [e for e in events if e["event"] == "task_start"]
                self.assertEqual([e["depth"] for e in starts], list(range(6)))
                self.assertEqual(runtime.tasks, 6)
                self.assertEqual(runtime.resources.active, {})
                if target_depth == 5:
                    self.assertEqual(outcome.status, "succeeded")
                    self.assertEqual(outcome.artifact["facts"], {"leaf_depth": 5})
                    self.assertEqual(runtime.calls, 30)
                    self.assertEqual({item.worker for item in runtime.outcomes.values()}, {"A", "B", "C"})
                    self.assertEqual(sum(e["event"] == "execution_start" for e in events), 6)
                else:
                    self.assertEqual(outcome.status, "failed")
                    self.assertTrue(all(item.status == "failed" for item in runtime.outcomes.values()))
                    rejected = [e for e in events if e["event"] == "proposal_rejected"]
                    self.assertEqual(len(rejected), 3)
                    self.assertTrue(all("delegation depth" in e["error"] for e in rejected))
                    self.assertFalse(any(e["event"] == "execution_start" for e in events))

    async def test_nested_delegation_worker_reentry_and_no_permanent_manager(self):
        events, payloads = [], []
        demo = DemoModel(0.001)
        async def model(worker, phase, payload, path):
            payloads.append((worker, phase, copy.deepcopy(payload), path))
            return await demo(worker, phase, payload, path)
        runtime = Runtime(model, Limits(), lambda event, **data: events.append({"event": event, **data}))
        result = await asyncio.wait_for(runtime.run(root(), requester="C"), 4)
        expected = json.loads((ROOT / "expected.json").read_text())
        self.assertTrue(evaluate(result, expected)["passed"])
        self.assertEqual(runtime.tasks, 6)
        self.assertEqual(runtime.calls, 30)
        self.assertEqual(runtime.peak_calls, 3)
        starts = {e["task_id"]: e for e in events if e["event"] == "task_start"}
        self.assertEqual(starts["release-review"]["requester"], "C")
        self.assertEqual(starts["release-review/economics/p"]["requester"], "B")
        self.assertEqual(runtime.outcomes["release-review/economics/p"].worker, "A")
        for worker, phase, payload, path in payloads:
            if phase == "review":
                self.assertTrue(all("confidence" not in p for p in payload["candidates"].values()))
            prompt = json.dumps(messages(worker, phase, payload))
            self.assertNotIn("expected.json", prompt)
        active = set()
        for event in events:
            if event["event"] == "call_start":
                session = (event["task_id"], event["worker"])
                self.assertNotIn(session, active)
                active.add(session)
                self.assertLessEqual(len(active), 3)
            elif event["event"] == "call_end":
                active.remove((event["task_id"], event["worker"]))
        self.assertEqual(active, set())

    async def test_dependency_starts_immediately_without_wave_barrier(self):
        slow_release = asyncio.Event()
        observed = []
        runtime = Runtime(None, Limits())
        async def solve(item, requester, depth, path, inputs, tie_order):
            observed.append(item.id)
            if item.id == "slow":
                await slow_release.wait()
            elif item.id == "after_fast":
                self.assertEqual(inputs["predecessors"]["fast"]["facts"], {"fast": 1})
                self.assertNotIn("slow", inputs["predecessors"])
                self.assertEqual(inputs["parent_inputs"], {"inherited": "fixture"})
                slow_release.set()
            return Outcome("succeeded", "A", artifact({item.id: 1}))
        runtime.solve = solve
        result = await asyncio.wait_for(runtime.run_graph(
            [task("fast"), task("slow"), task("after_fast", ("fast",))], "A", 1, "root",
            {"inherited": "fixture"}), 2)
        self.assertEqual(len(result), 3)
        self.assertIn("after_fast", observed)

    async def test_tied_peer_plans_execute_on_distinct_workers_at_the_same_time(self):
        both_executing = asyncio.Event()
        executing = set()
        async def model(worker, phase, payload, path):
            if phase == "propose":
                return json.dumps(proposal([step("left"), step("right")] if payload["depth"] == 0 else None))
            if phase == "review":
                return json.dumps({"scores": {w: {d: 2 for d in ("coverage", "feasibility", "verification")}
                                               for w in payload["candidates"]}})
            if phase == "execute":
                executing.add(worker)
                if len(executing) == 2:
                    both_executing.set()
                await both_executing.wait()
            return json.dumps(artifact())
        runtime = Runtime(model, Limits())
        result = await asyncio.wait_for(runtime.run(root()), 2)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(executing, {"B", "C"})

    async def test_failed_dependency_blocks_descendants_and_preserves_independent_result(self):
        observed = []
        runtime = Runtime(None, Limits())
        async def solve(item, *_):
            observed.append(item.id)
            return Outcome("failed", error="fixture error") if item.id == "z" else Outcome("succeeded", artifact=artifact())
        runtime.solve = solve
        results = await asyncio.wait_for(runtime.run_graph(
            [task("z"), task("b", ("z",)), task("a", ("b",)), task("independent")], "A", 1, "root", {}), 2)
        self.assertEqual(set(observed), {"z", "independent"})
        self.assertEqual(results["a"].status, "blocked")
        self.assertEqual(results["b"].status, "blocked")
        self.assertEqual(results["independent"].status, "succeeded")

    async def test_call_task_and_depth_limits_stop_work(self):
        for limits in (Limits(max_calls=3), Limits(max_tasks=3), Limits(max_depth=1)):
            with self.subTest(limits=limits):
                runtime = Runtime(DemoModel(0), limits)
                outcome = await asyncio.wait_for(runtime.run(root()), 2)
                self.assertEqual(outcome.status, "failed")
                self.assertLessEqual(runtime.calls, limits.max_calls)
                self.assertLessEqual(runtime.tasks, limits.max_tasks)
                self.assertEqual(runtime.resources.active, {})

    async def test_malformed_execution_blocks_downstream_without_silent_retry(self):
        demo = DemoModel(0)
        seen = []
        async def model(worker, phase, payload, path):
            seen.append((path, phase))
            if path.endswith("/technical") and phase == "execute":
                return "not JSON"
            return await demo(worker, phase, payload, path)
        runtime = Runtime(model, Limits())
        result = await runtime.run(root())
        self.assertEqual(result.status, "failed")
        self.assertEqual(runtime.outcomes["release-review/recommendation"].status, "blocked")
        self.assertEqual(runtime.outcomes["release-review/economics"].status, "succeeded")
        self.assertEqual(seen.count(("release-review/technical", "execute")), 1)

    async def test_exact_response_replay_preserves_outcomes_in_serial_and_parallel_modes(self):
        records = []
        runtime = Runtime(DemoModel(0), Limits(), lambda event, **data: records.append({"event": event, **data}))
        original = await runtime.run(root())
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            tape = Path(folder) / "tape.jsonl"
            tape.write_text("".join(json.dumps(r) + "\n" for r in records))
            for concurrency in (1, 3):
                replay = ReplayModel(tape, 0.001)
                other = Runtime(replay, Limits(max_parallel=concurrency))
                result = await other.run(root())
                self.assertTrue(replay.complete())
                self.assertEqual(asdict(original), asdict(result))
                self.assertEqual({k: asdict(v) for k, v in runtime.outcomes.items()},
                                 {k: asdict(v) for k, v in other.outcomes.items()})
                self.assertEqual(other.peak_calls, concurrency)
            changed = root()
            changed = Task(**dict(asdict(changed), goal="Different source input"))
            mismatched = await Runtime(ReplayModel(tape), Limits()).run(changed)
            self.assertEqual(mismatched.status, "failed")

    async def test_live_replay_rejects_changed_system_prompt_and_transport(self):
        payload = {"fixture": "public input"}
        records = [
            {"event": "run_start", "settings": {"mode": "live", "transport": {"model": "fixture"}}},
            {"event": "http_request", "task_id": "root", "contractor": "A", "phase": "execute",
             "payload": {"messages": messages("A", "execute", payload),
                         "response_format": response_format("execute", payload)}},
            {"event": "model_reply", "task_id": "root", "worker": "A", "phase": "execute",
             "request_sha": fingerprint({"worker": "A", "phase": "execute", "payload": payload}),
             "raw": json.dumps(artifact())}]
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            tape = Path(folder) / "live-like.jsonl"
            tape.write_text("".join(json.dumps(r) + "\n" for r in records))
            self.assertEqual(await ReplayModel(tape)("A", "execute", payload, "root"), json.dumps(artifact()))
            with patch.dict(PHASES, {"execute": "A changed system instruction"}):
                with self.assertRaisesRegex(ValueError, "messages differ"):
                    await ReplayModel(tape)("A", "execute", payload, "root")
            with self.assertRaisesRegex(ValueError, "transport settings differ"):
                ReplayModel(tape, transport={"model": "another model"})

    async def test_readers_overlap_writer_waits_and_cancellation_releases_lease(self):
        resources = Resources()
        readers_ready, release = asyncio.Event(), asyncio.Event()
        readers = 0
        events = []
        async def read(name):
            nonlocal readers
            async with resources.lease(name, ["shared"], []):
                readers += 1
                if readers == 2:
                    readers_ready.set()
                await release.wait()
        async def write():
            async with resources.lease("writer", [], ["shared"]):
                events.append("writer")
        a, b = asyncio.create_task(read("a")), asyncio.create_task(read("b"))
        await asyncio.wait_for(readers_ready.wait(), 1)
        writer = asyncio.create_task(write())
        await asyncio.sleep(0)
        self.assertEqual(events, [])
        a.cancel()
        await asyncio.gather(a, return_exceptions=True)
        self.assertEqual(events, [])
        release.set()
        await asyncio.wait_for(asyncio.gather(b, writer), 1)
        self.assertEqual(events, ["writer"])
        self.assertEqual(resources.active, {})

    async def test_cancelling_parent_drains_children_and_releases_slots(self):
        ready, cancelled = asyncio.Event(), []
        runtime = Runtime(None, Limits())
        async def solve(item, *_):
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(item.id)
        runtime.solve = solve
        parent = asyncio.create_task(runtime.run_graph([task("a"), task("b")], "A", 1, "root", {}))
        await ready.wait()
        parent.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await parent
        self.assertEqual(set(cancelled), {"a", "b"})


if __name__ == "__main__":
    unittest.main()
