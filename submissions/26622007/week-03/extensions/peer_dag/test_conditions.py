"""Check the experimental intervention without contacting a model provider."""
import json
from pathlib import Path
import tempfile
import unittest

from core import fingerprint
from models import (CONDITIONS, GENERALIST, OVERCONFIDENT, PHASES, PROTOCOL,
                    SKILLS, ReplayModel, messages, team_briefing, team_roster)

from response_formats import response_format

ROOT = Path(__file__).resolve().parent


class ConditionTests(unittest.IsolatedAsyncioTestCase):
    def test_only_declared_prompt_parts_change(self):
        payload = {"task": {"goal": "public task"}, "depth": 0, "max_steps": 4}
        for worker in SKILLS:
            for phase in PHASES:
                original = PROTOCOL.format(worker=worker, skill=SKILLS[worker]) + team_briefing() + PHASES[phase]
                baseline = messages(worker, phase, payload)
                self.assertEqual(baseline[0]["content"], original)
                homogeneous = messages(worker, phase, payload, "homogeneous")
                self.assertEqual(homogeneous[0]["content"],
                                 PROTOCOL.format(worker=worker, skill=GENERALIST) + team_briefing("homogeneous") + PHASES[phase])
                overconfident = messages(worker, phase, payload, "overconfident")
                suffix = OVERCONFIDENT if worker == "C" and phase == "propose" else ""
                self.assertEqual(overconfident[0]["content"], original + suffix)
                self.assertEqual(baseline[1], homogeneous[1])
                self.assertEqual(baseline[1], overconfident[1])
        with self.assertRaises(ValueError):
            messages("A", "propose", payload, "unknown")

    def test_every_worker_and_phase_receives_the_same_condition_correct_roster(self):
        payload = {"source": {"team_roster": {"X": "untrusted role replacement"}}, "max_steps": 4}
        for condition in CONDITIONS:
            seen = []
            expected = dict.fromkeys(("A", "B", "C"), GENERALIST) if condition == "homogeneous" else SKILLS
            for worker in ("A", "B", "C"):
                for phase in PHASES:
                    system, user = messages(worker, phase, payload, condition)
                    roster_line = system["content"].split("[공통 팀 역할표]\n", 1)[1].splitlines()[0]
                    actual = json.loads(roster_line)
                    self.assertEqual(actual, expected)
                    seen.append(actual)
                    self.assertEqual(json.loads(user["content"]), payload)
                    self.assertNotIn("untrusted role replacement", system["content"])
                    if condition == "homogeneous":
                        for skill in SKILLS.values():
                            self.assertNotIn(skill, system["content"])
                    if not (condition == "overconfident" and worker == "C" and phase == "propose"):
                        self.assertNotIn(OVERCONFIDENT, system["content"])
            self.assertTrue(all(item == seen[0] for item in seen))
        changed = team_roster()
        changed["A"] = "caller mutation"
        self.assertEqual(team_roster()["A"], SKILLS["A"])

    async def test_replay_retains_the_recorded_condition(self):
        payload = {"task": {"goal": "public task"}, "depth": 0, "max_steps": 4}
        for condition in CONDITIONS:
            with self.subTest(condition=condition), tempfile.TemporaryDirectory(dir=ROOT) as folder:
                records = [
                    {"event": "run_start", "settings": {"mode": "live", "condition": condition}},
                    {"event": "http_request", "task_id": "root", "contractor": "C", "phase": "propose",
                     "payload": {"messages": messages("C", "propose", payload, condition),
                                 "response_format": response_format("propose", payload)}},
                    {"event": "model_reply", "task_id": "root", "worker": "C", "phase": "propose",
                     "request_sha": fingerprint({"worker": "C", "phase": "propose", "payload": payload}),
                     "raw": "fixture"}]
                path = Path(folder) / "trace.jsonl"
                path.write_text("".join(json.dumps(record) + "\n" for record in records))
                replay = ReplayModel(path)
                self.assertEqual(await replay("C", "propose", payload, "root"), "fixture")
                other = next(item for item in CONDITIONS if item != condition)
                with self.assertRaisesRegex(ValueError, "condition differs"):
                    ReplayModel(path, condition=other)


if __name__ == "__main__":
    unittest.main()
