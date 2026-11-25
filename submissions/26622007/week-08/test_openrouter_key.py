"""HARNESS=openrouter: the key comes from the environment first, else from the submission's .env; agent.json runs luna there."""
import json
import os
import tempfile
import unittest
from unittest import mock

import harness

HERE = os.path.dirname(os.path.abspath(__file__))


class OpenRouterKeyTest(unittest.TestCase):
    def _env_file(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".env", delete=False)
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_environment_wins(self):
        path = self._env_file("OPENROUTER_API_KEY=from-file\n")
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "from-env", "OPENROUTER_ENV_FILE": path}):
            self.assertEqual(harness._openrouter_key(), "from-env")

    def test_falls_back_to_env_file_with_quotes_and_export(self):
        path = self._env_file("OTHER=x\nexport OPENROUTER_API_KEY=\"from-file\"\n")
        env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
        env["OPENROUTER_ENV_FILE"] = path
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(harness._openrouter_key(), "from-file")

    def test_missing_file_gives_none_so_the_call_raises(self):
        env = {k: v for k, v in os.environ.items() if k != "OPENROUTER_API_KEY"}
        env["OPENROUTER_ENV_FILE"] = os.path.join(tempfile.gettempdir(), "no-such-openrouter.env")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(harness._openrouter_key())
            with self.assertRaises(harness.HarnessError):
                harness._ask_openrouter("p", {"type": "object"}, "s", "t")

    def test_agent_json_runs_luna_on_openrouter(self):
        with open(os.path.join(HERE, "agent.json"), encoding="utf-8") as f:
            agent = json.load(f)
        for role in ("seller", "buyer", "market_buyer", "restock"):
            self.assertIn("HARNESS=openrouter ", agent[role]["cmd"], role)
        self.assertEqual(harness.OR_MAX_CALLS_PER_HOUR if "OR_MAX_CALLS_PER_HOUR" not in os.environ else 0, 0)




class OpenRouterDeadlineTest(unittest.TestCase):
    """The caller's timeout bounds the whole decision: the buyer's shop choice has 15 s, not 2 x 90 s plus back-off."""

    def _run(self, post, timeout):
        clock = [1000.0]
        posts = []

        def fake_post(self_, url, json=None, headers=None, timeout=None):
            posts.append(timeout)
            return post(clock)

        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}), \
                mock.patch.object(harness.time, "time", lambda: clock[0]), \
                mock.patch.object(harness.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s)), \
                mock.patch.object(harness.httpx.Client, "post", fake_post), \
                mock.patch.object(harness, "record_call", lambda *a, **k: None), \
                mock.patch.object(harness, "BACKEND", "openrouter"):
            with self.assertRaises(harness.HarnessError):
                harness.ask("p", {"type": "object", "properties": {}}, "s", "t", timeout=timeout)
        return posts

    def test_slow_failure_is_not_retried_past_half_the_deadline(self):
        def slow_429(clock):
            clock[0] += 9
            return mock.Mock(status_code=429, text="rate limited")
        posts = self._run(slow_429, 15)
        self.assertEqual(len(posts), 1)
        self.assertLessEqual(posts[0], 15)

    def test_fast_failure_retries_within_the_deadline(self):
        def fast_503(clock):
            clock[0] += 1
            return mock.Mock(status_code=503, text="busy")
        posts = self._run(fast_503, 15)
        self.assertEqual(len(posts), 2)
        self.assertLess(posts[1], 15)


if __name__ == "__main__":
    unittest.main()
