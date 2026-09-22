"""Task-set alignment, case isolation, answer privacy and safe replay selection."""
import argparse
import contextlib
from dataclasses import asdict
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import case_catalog as cases
import cli
from fixtures import artifact, proposal
from models import BASE


class CatalogTests(unittest.TestCase):
    def test_five_tasks_match_the_executable_public_cases(self):
        base = json.loads((BASE / "tasks.json").read_text())
        catalog = cases.catalog()
        self.assertEqual(len(base), 5)
        self.assertEqual([item["id"] for item in base], [item["id"] for item in catalog])
        self.assertGreaterEqual(len({item["gold"] for item in base}), 2)
        for item in base:
            task, expected, files = cases.load_case(item["id"])
            self.assertEqual(task.goal, item["desc"])
            self.assertNotIn("gold", asdict(task))
            self.assertTrue(files)
            self.assertTrue(expected)
            self.assertTrue(all(key in task.acceptance for key in expected))
            if task.id != cases.DEFAULT_CASE:
                self.assertFalse(task.require_delegate)

    def test_numeric_answer_keys_follow_the_provided_formulas(self):
        game = cases.load_case("game-design-architecture")[1]
        self.assertEqual(game["developer_hours"], 2*20*4)
        self.assertEqual(game["core_scope_hours"], sum([24,24,32,24,20,24,12]))
        self.assertEqual(game["with_coop_hours"], game["core_scope_hours"]+80)
        payment = cases.load_case("payment-redesign")[1]
        self.assertEqual(payment["backfill_batches"], 1200000//5000)
        self.assertEqual(payment["two_worker_backfill_seconds"], (payment["backfill_batches"]//2)*15)
        growth = cases.load_case("growth-roadmap")[1]
        for name, price, variable, customers in (("s",60000,12000,100),("e",300000,60000,18)):
            margin = price-variable
            self.assertEqual(growth[name+"_profit"], margin*customers-2000000)
            self.assertEqual(growth[name+"_low_profit"], margin*(customers*70//100)-2000000)
            self.assertEqual(growth[name+"_high_profit"], margin*(customers*130//100)-2000000)
            self.assertEqual(growth[name+"_break_even"], (2000000+margin-1)//margin)
        operations = cases.load_case("launch-operations")[1]
        self.assertEqual(operations["expected_contacts"], 800*20//100)
        self.assertEqual(operations["additional_agents"], 160//(5*4)-4)

    def test_unknown_and_escaping_case_paths_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown case"):
            cases.load_case("../../secret")
        with tempfile.TemporaryDirectory(dir=cases.ROOT) as folder:
            catalog = Path(folder)/"catalog.json"
            catalog.write_text(json.dumps([{"id":"x","title":"x","case":"../../config.json","expected":"expected.json"}]))
            with patch.object(cases, "CATALOG", catalog), self.assertRaisesRegex(ValueError, "inside peer_dag"):
                cases.catalog()

    def test_replay_infers_case_and_rejects_wrong_case_or_unsupported_demo(self):
        with tempfile.TemporaryDirectory(dir=cases.ROOT) as folder:
            path = Path(folder)/"trace.jsonl"
            path.write_text(json.dumps({"event":"run_start","settings":{"case_id":"game-design-architecture"}})+'\n')
            self.assertEqual(cases.select_case("replay", replay=path), "game-design-architecture")
            with self.assertRaisesRegex(ValueError, "differs"):
                cases.select_case("replay", "release-review", path)
            path.write_text(json.dumps({"event":"run_start","settings":{}})+'\n')
            self.assertEqual(cases.select_case("replay", replay=path), "release-review")
        with self.assertRaisesRegex(ValueError, "scripted demo"):
            cases.select_case("demo", "game-design-architecture")

    def test_uncommitted_selected_input_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=cases.ROOT) as folder:
            root = Path(folder)
            path = root/"selected-case.json"
            path.write_bytes(b"changed")
            with patch.object(cli, "ROOT", root), \
                 patch.object(cli.subprocess, "check_output", side_effect=[str(root), b"committed"]), \
                 self.assertRaisesRegex(ValueError, "commit selected-case.json"):
                cli.committed_inputs((path,))


class CasePrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_runs_selected_task_without_private_expected_values(self):
        task, _, files = cases.load_case("game-design-architecture")
        config = cli.load_config()
        captured = []
        async def model(worker, phase, payload, task_id):
            captured.append(json.loads(json.dumps(payload)))
            if phase == "propose":
                return json.dumps(proposal())
            if phase == "review":
                return json.dumps({"scores": {w:{d:2 for d in ("coverage","feasibility","verification")}
                                               for w in payload["candidates"]}})
            return json.dumps(artifact({"fixture":1}))
        with tempfile.TemporaryDirectory(dir=cases.ROOT) as folder:
            root = Path(folder)
            args = argparse.Namespace(mode="live", case=task.id, replay=None, parallel=None,
                env_file=None, requester="A", condition="baseline", run_id="private-answer-fixture", replay_delay_ms=0)
            with patch.object(cli, "ROOT", root), patch.object(cli, "load_config", return_value=config), \
                 patch.object(cli, "load_case", return_value=(task,{"PRIVATE_CHECK":"PRIVATE_ANSWER"},files)), \
                 patch.object(cli, "committed_inputs", return_value="fixture"), \
                 patch.object(cli, "read_key", return_value="fixture-key"), \
                 patch.object(cli, "LiveModel", return_value=model), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(await cli.run(args), 1)
            serialized = json.dumps(captured)
            self.assertNotIn("PRIVATE_CHECK", serialized)
            self.assertNotIn("PRIVATE_ANSWER", serialized)
            self.assertTrue(all(payload["task"]["id"] == task.id for payload in captured))
            self.assertTrue((root/"runs/private-answer-fixture/artifacts"/(task.id+".json")).exists())


if __name__ == "__main__":
    unittest.main()
