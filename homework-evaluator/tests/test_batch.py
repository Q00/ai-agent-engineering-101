"""Batch isolation and resumption without GitHub writes or model calls."""

import copy
import fcntl
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_itemwise import fake_response
from test_policy_comments import complete_fixture

from ax_evaluator.batch import run_batch
from ax_evaluator.snapshot import document
from ax_evaluator.storage import digest, read_json, write_new


class BatchTest(unittest.TestCase):
    def test_all_prs_share_one_bounded_model_pool_and_isolated_cache_keys(self):
        active, peak, calls = 0, 0, 0
        mutex = threading.Lock()

        def transport(config, prompt, schema):
            nonlocal active, peak, calls
            with mutex:
                active += 1
                peak = max(peak, active)
                calls += 1
            try:
                time.sleep(0.005)
                return fake_response(prompt, schema)
            finally:
                with mutex:
                    active -= 1

        ledger = self.out.parent / "shared-ledger"
        with patch("ax_evaluator.itemwise.http_review", side_effect=transport):
            first = self.run_batch(
                stage="evaluate",
                config=self.config,
                itemwise={
                    "ledger": str(ledger),
                    "workers": 2,
                    "max_task_chars": 400000,
                },
            )
            self.assertEqual(first["counts"], {"complete": 3})
            self.assertEqual(peak, 2)
            before = calls
            self.run_batch(
                stage="evaluate",
                config=self.config,
                resume=True,
                itemwise={
                    "ledger": str(ledger),
                    "workers": 2,
                    "max_task_chars": 400000,
                },
            )
            self.assertEqual(calls, before)
        students = {
            read_json(p)["request"]["submission"]["student_id"]
            for p in ledger.glob("judgments/*/result.json")
        }
        self.assertEqual(students, {"101", "102", "103"})
        self.assertEqual(len(read_json(self.out / "progress.json")["items"]), 3)
        self.github_write.assert_not_called()

    def test_global_provider_stop_does_not_dispatch_queued_model_calls(self):
        from ax_evaluator.providers import ProviderUnavailable

        calls = []

        def transport(*args):
            calls.append(1)
            raise ProviderUnavailable("usage-limit")

        with patch("ax_evaluator.itemwise.http_review", side_effect=transport):
            result = self.run_batch(
                stage="evaluate",
                config=self.config,
                itemwise={
                    "ledger": str(self.out.parent / "ledger"),
                    "workers": 2,
                    "max_task_chars": 400000,
                },
            )
        self.assertLessEqual(len(calls), 2)
        self.assertTrue(any(i.get("provider_unavailable") for i in result["items"]))
        self.assertEqual(len(result["items"]), 3)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / "batch"
        self.snapshot, bundle, self.review = complete_fixture()
        self.rubric = bundle["rubric"]
        self.config = {
            "kind": "openai",
            "model": "fixture",
            "base_url": "http://localhost:11434/v1",
            "api_key_env": "",
        }
        self.args = dict(
            repo="example/course",
            prs=[1, 2, 3],
            week="week-05",
            spec_ref="a" * 40,
            rubric=self.rubric,
            policy=self.rubric["bonus_policy"],
            out=self.out,
            notify=lambda _: None,
        )
        self.collect_mock = self.enterContext(
            patch("ax_evaluator.batch.collect_pr", side_effect=self.collect)
        )
        self.judge_mock = self.enterContext(
            patch("ax_evaluator.batch.judge", side_effect=self.judge)
        )
        self.github_write = self.enterContext(
            patch(
                "ax_evaluator.comments.github",
                side_effect=AssertionError("No publication"),
            )
        )

    def collect(self, repo, pr, out, spec, week):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["metadata"].update(
            source="github",
            repository=repo,
            pr=pr,
            student_id=str(100 + pr),
            head_sha=str(pr) * 40,
            spec_sha=spec,
            week=week,
        )
        index = json.loads(snapshot["files"]["@history-index.json"]["text"])
        index.update(head_sha=str(pr) * 40, prefix=f"submissions/{100 + pr}/{week}/")
        snapshot["files"]["@history-index.json"] = document(json.dumps(index).encode())
        snapshot["id"] = digest({k: v for k, v in snapshot.items() if k != "id"})
        write_new(out, snapshot)
        return snapshot

    def judge(self, bundle, snapshot, config, out):
        response = {
            "bundle_id": bundle["id"],
            "review": copy.deepcopy(self.review),
            "provenance": {"kind": "fixture"},
        }
        write_new(out / "response.json", response)
        return response

    def run_batch(self, **kwargs):
        return run_batch(**(self.args | kwargs))

    def test_itemwise_path_keeps_full_files_and_uses_shared_ledger(self):
        def itemwise(bundle, snapshot, config, out, **options):
            self.assertTrue(
                all(
                    item["state"] in {"full", "unreadable"}
                    for item in bundle["coverage"].values()
                )
            )
            self.assertEqual(options["workers"], 2)
            self.assertEqual(options["ledger"], str(self.out.parent / "ledger"))
            return self.judge(bundle, snapshot, config, out)

        with patch("ax_evaluator.batch.judge_items", side_effect=itemwise) as call:
            result = self.run_batch(
                stage="evaluate",
                config=self.config,
                itemwise=dict(
                    ledger=str(self.out.parent / "ledger"),
                    workers=2,
                    max_task_chars=400000,
                ),
            )
        self.assertEqual(result["counts"], {"complete": 3})
        self.assertEqual(call.call_count, 3)
        self.judge_mock.assert_not_called()

    def test_provider_limit_waits_and_explicit_retry_reuses_frozen_snapshot(self):
        options = dict(
            ledger=str(self.out.parent / "ledger"), workers=1, max_task_chars=400000
        )

        def limited(bundle, snapshot, config, out, **kwargs):
            review = copy.deepcopy(self.review)
            review["items"][0].update(
                status="pending",
                pending_reason="not-reviewed",
                feedback_type="confirmation",
                deduction_reason="",
            )
            response = {
                "bundle_id": bundle["id"],
                "review": review,
                "provenance": {
                    "metrics": {
                        "provider_unavailable": True,
                        "failures": [{"task": "F06"}],
                    }
                },
            }
            write_new(out / "response.json", response)
            return response

        with patch("ax_evaluator.batch.judge_items", side_effect=limited) as call:
            first = self.run_batch(
                stage="evaluate", config=self.config, itemwise=options
            )
            self.assertEqual(call.call_count, 1)
            self.assertEqual(
                first["counts"], {"needs-review": 1, "awaiting-provider": 2}
            )
            self.run_batch(
                stage="evaluate", config=self.config, itemwise=options, resume=True
            )
            self.assertEqual(call.call_count, 1)
        with patch(
            "ax_evaluator.batch.judge_items",
            side_effect=lambda b, s, c, o, **k: self.judge(b, s, c, o),
        ):
            final = self.run_batch(
                stage="evaluate",
                config=self.config,
                itemwise=options,
                resume=True,
                retry_failed=True,
            )
        self.assertEqual(final["counts"], {"complete": 3})
        self.assertEqual(self.collect_mock.call_count, 3)

    def test_prepare_then_manual_finalize_is_offline_and_independent(self):
        result = self.run_batch()
        self.assertEqual(result["counts"], {"prepared": 3})
        self.judge_mock.assert_not_called()
        for pr in (1, 2, 3):
            packet = self.out / f"pr-{pr}/packet-001"
            bundle = read_json(packet / "bundle.json")
            write_new(
                self.out / f"pr-{pr}/review.json",
                {
                    "bundle_id": bundle["id"],
                    "review": self.review,
                    "provenance": {"kind": "current-session"},
                },
            )
        result = self.run_batch(stage="finalize", resume=True)
        self.assertEqual(result["counts"], {"complete": 3})
        self.assertEqual(self.collect_mock.call_count, 3)
        self.judge_mock.assert_not_called()
        self.github_write.assert_not_called()
        self.assertTrue((self.out / "summary.md").exists())
        self.assertEqual(len({r["head_sha"] for r in result["items"]}), 3)
        self.assertTrue(all(r["base_max"] == 18 for r in result["items"]))

    def test_missing_manual_reviews_are_waiting_not_zero_scores(self):
        self.run_batch()
        result = self.run_batch(stage="finalize", resume=True)
        self.assertEqual(result["counts"], {"awaiting-review": 3})
        self.assertTrue(all("base_earned" not in r for r in result["items"]))
        self.judge_mock.assert_not_called()

    def test_one_failure_does_not_stop_other_prs_and_retry_is_explicit(self):
        def flaky(bundle, snapshot, config, out):
            if snapshot["metadata"]["pr"] == 2:
                write_new(out / "request.json", {"test": "keep failed attempt"})
                raise TimeoutError("NEVER PRINT PROVIDER SECRET")
            return self.judge(bundle, snapshot, config, out)

        self.judge_mock.side_effect = flaky
        result = self.run_batch(stage="evaluate", config=self.config)
        self.assertEqual(result["counts"], {"complete": 2, "failed": 1})
        self.assertNotIn("NEVER PRINT", (self.out / "summary.md").read_text())
        self.run_batch(stage="evaluate", config=self.config, resume=True)
        self.assertEqual(self.judge_mock.call_count, 3)
        self.judge_mock.side_effect = self.judge
        result = self.run_batch(
            stage="evaluate", config=self.config, resume=True, retry_failed=True
        )
        self.assertEqual(result["counts"], {"complete": 3})
        self.assertEqual(self.judge_mock.call_count, 4)
        self.assertEqual(self.collect_mock.call_count, 3)
        self.assertTrue((self.out / "pr-2/model-001/request.json").exists())
        self.assertTrue((self.out / "pr-2/model-002/response.json").exists())

    def test_completed_batch_never_recollects_or_rejudges(self):
        self.run_batch(stage="evaluate", config=self.config)
        before = (self.out / "pr-1/final-001/evaluation.json").read_bytes()
        self.run_batch(
            stage="evaluate", config=self.config, resume=True, retry_failed=True
        )
        self.assertEqual(self.collect_mock.call_count, 3)
        self.assertEqual(self.judge_mock.call_count, 3)
        self.assertEqual(
            before, (self.out / "pr-1/final-001/evaluation.json").read_bytes()
        )

    def test_saved_response_survives_interrupted_model_return(self):
        def interrupted(bundle, snapshot, config, out):
            self.judge(bundle, snapshot, config, out)
            raise KeyboardInterrupt()

        self.judge_mock.side_effect = interrupted
        with self.assertRaises(KeyboardInterrupt):
            self.run_batch(stage="evaluate", config=self.config)
        self.judge_mock.side_effect = self.judge
        result = self.run_batch(stage="evaluate", config=self.config, resume=True)
        self.assertEqual(result["counts"], {"complete": 3})
        self.assertEqual(self.judge_mock.call_count, 3)  # 1 saved + 2 remaining

    def test_interrupted_model_without_response_does_not_silently_retry(self):
        self.judge_mock.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.run_batch(stage="evaluate", config=self.config)
        self.judge_mock.side_effect = self.judge
        result = self.run_batch(stage="evaluate", config=self.config, resume=True)
        self.assertEqual(result["counts"], {"failed": 1, "complete": 2})
        self.assertEqual(self.judge_mock.call_count, 3)
        self.assertEqual(result["items"][0]["error_type"], "InterruptedModelCall")
        self.run_batch(
            stage="evaluate", config=self.config, resume=True, retry_failed=True
        )
        self.assertEqual(self.judge_mock.call_count, 4)

    def test_failed_collection_continues_and_retries_only_failed_pr(self):
        def flaky(repo, pr, out, spec, week):
            if pr == 2:
                raise ValueError("unavailable PR")
            return self.collect(repo, pr, out, spec, week)

        self.collect_mock.side_effect = flaky
        self.assertEqual(self.run_batch()["counts"], {"prepared": 2, "failed": 1})
        self.collect_mock.side_effect = self.collect
        self.assertEqual(
            self.run_batch(resume=True, retry_failed=True)["counts"], {"prepared": 3}
        )
        self.assertEqual(self.collect_mock.call_count, 4)

    def test_reviewed_but_incomplete_is_not_marked_complete(self):
        self.review["success_audit"]["judge_logic"]["status"] = "insufficient"
        result = self.run_batch(stage="evaluate", config=self.config)
        self.assertEqual(result["counts"], {"needs-review": 3})
        self.assertTrue(all(not r["comment_ready"] for r in result["items"]))
        self.run_batch(stage="evaluate", config=self.config, resume=True)
        self.assertEqual(self.judge_mock.call_count, 3)

    def test_changed_inputs_are_rejected_before_external_calls(self):
        self.run_batch(config=self.config)
        for change in (
            {"prs": [1, 2]},
            {"spec_ref": "b" * 40},
            {"config": self.config | {"model": "different"}},
            {"max_chars": 999},
        ):
            with self.subTest(change=change):
                with self.assertRaisesRegex(ValueError, "inputs/code changed"):
                    self.run_batch(**({"config": self.config, "resume": True} | change))
        self.assertEqual(self.collect_mock.call_count, 3)
        self.judge_mock.assert_not_called()

    def test_model_requires_explicit_config_and_prepare_never_calls_it(self):
        with self.assertRaisesRegex(ValueError, "explicit model config"):
            self.run_batch(stage="evaluate")
        self.run_batch(config=self.config)
        self.judge_mock.assert_not_called()

    def test_unpinned_spec_and_duplicate_prs_are_rejected(self):
        for change in (
            {"spec_ref": "main"},
            {"prs": [1, 1]},
            {"prs": []},
            {"prs": [True]},
        ):
            with self.assertRaises(ValueError):
                self.run_batch(**change)
        self.collect_mock.assert_not_called()

    def test_second_process_cannot_evaluate_same_batch(self):
        self.run_batch(config=self.config)
        with (self.out / ".batch.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, "already running"):
                self.run_batch(config=self.config, stage="evaluate", resume=True)
        self.judge_mock.assert_not_called()

    def test_finalize_failure_reuses_model_response(self):
        from ax_evaluator.review import finalize

        with patch("ax_evaluator.batch.finalize", side_effect=OSError("disk error")):
            result = self.run_batch(stage="evaluate", config=self.config)
        self.assertEqual(result["counts"], {"failed": 3})
        with patch("ax_evaluator.batch.finalize", side_effect=finalize):
            result = self.run_batch(
                stage="evaluate", config=self.config, resume=True, retry_failed=True
            )
        self.assertEqual(result["counts"], {"complete": 3})
        self.assertEqual(self.judge_mock.call_count, 3)

    def test_finalize_rejects_review_from_other_pr(self):
        self.run_batch()
        bundle = read_json(self.out / "pr-1/packet-001/bundle.json")
        for pr in (1, 2, 3):
            write_new(
                self.out / f"pr-{pr}/review.json",
                {
                    "bundle_id": bundle["id"],
                    "review": self.review,
                    "provenance": {"kind": "current-session"},
                },
            )
        result = self.run_batch(stage="finalize", resume=True)
        self.assertEqual(result["counts"], {"complete": 1, "failed": 2})


if __name__ == "__main__":
    unittest.main()
