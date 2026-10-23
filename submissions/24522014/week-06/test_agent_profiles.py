"""STEP 8 command/configuration checks. No servers or model calls."""

from copy import deepcopy
import json
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import agent_profiles
from harness import select_harness


class ProfileChecks(unittest.TestCase):
    def test_all_commands_select_the_declared_harnesses(self):
        for name in agent_profiles.PROFILES:
            config = agent_profiles.load_profile(name)
            for role in ("seller", "buyer"):
                with self.subTest(profile=name, role=role):
                    command = agent_profiles.command_for(config, role, port=12345, url="http://127.0.0.1:12345",
                                                         item="lamp", budget=120)
                    mode = command[command.index("--mode") + 1]
                    flag = "--" + role + "-harness"
                    chosen = command[command.index(flag) + 1] if flag in command else config[role]["harness"]
                    provider = select_harness(mode, role, seller=chosen, buyer=chosen)
                    self.assertEqual(provider, config[role]["harness"])
                    self.assertEqual(command[0], sys.executable)
                    self.assertNotIn("--manual", command)

    def test_substitutions_remain_single_arguments(self):
        url = "http://fixture/rpc?x=a b;y=c"
        command = agent_profiles.command_for(agent_profiles.load_profile("claude"), "buyer",
                                             url=url, item="lamp", budget=120)
        self.assertEqual(command[command.index("--url") + 1], url)

    def test_invalid_contract_or_model_is_rejected(self):
        config = agent_profiles.load_profile("claude")
        for role, replacement in (("seller", "python seller.py --port 9999"),
                                  ("buyer", "python buyer.py --url {url}")):
            modified = deepcopy(config)
            modified[role]["cmd"] = replacement
            with self.assertRaises(ValueError):
                agent_profiles.validate_profile(modified)
        config["buyer"]["model"] = "not-the-selected-model"
        with self.assertRaises(ValueError):
            agent_profiles.validate_profile(config)
        with self.assertRaises(ValueError):
            agent_profiles.load_profile("../agent")

    def test_switching_changes_only_configuration_not_evidence(self):
        root = agent_profiles.ROOT / "logs/step8-tests" / uuid4().hex
        (root / "profiles").mkdir(parents=True)
        for name in agent_profiles.PROFILES:
            (root / "profiles" / (name + ".json")).write_text(
                json.dumps(agent_profiles.load_profile(name)), encoding="utf-8")
        report = b'{"scope": "local fixture, not a trade-test result"}'
        (root / "test-report.json").write_bytes(report)
        with patch.object(agent_profiles, "ROOT", root):
            for name in agent_profiles.PROFILES:
                selected = agent_profiles.select_profile(name)
                self.assertEqual(json.loads((root / "agent.json").read_text(encoding="utf-8")), selected)
                self.assertEqual((root / "test-report.json").read_bytes(), report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
