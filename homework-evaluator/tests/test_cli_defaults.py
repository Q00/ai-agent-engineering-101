"""The public commands must enter full-file itemwise grading by default."""

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_checks import fixture

from ax_evaluator.__main__ import main
from ax_evaluator.storage import digest, read_json, write_new


class DefaultsTest(unittest.TestCase):
    def test_judge_uses_itemwise_and_never_silently_uses_legacy_review(self):
        with (
            patch("ax_evaluator.__main__.read_json", return_value={}),
            patch(
                "ax_evaluator.__main__.judge_items",
                return_value={"provenance": {"metrics": {"failures": []}}},
            ) as call,
            patch("ax_evaluator.__main__.judge") as legacy,
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            self.assertEqual(
                main(
                    ["judge", "packet", "--config", "provider.json", "--out", "model"]
                ),
                0,
            )
            self.assertEqual(call.call_count, 1)
            legacy.assert_not_called()

    def test_batch_has_shared_itemwise_settings_without_extra_flags(self):
        with (
            patch("ax_evaluator.__main__.read_json", return_value={}),
            patch(
                "ax_evaluator.__main__.run_batch",
                return_value={"counts": {}, "items": []},
            ) as call,
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            self.assertEqual(
                main(
                    [
                        "batch",
                        "--prs",
                        "1",
                        "2",
                        "--spec-ref",
                        "a" * 40,
                        "--policy",
                        "policy.json",
                        "--out",
                        "batch",
                    ]
                ),
                0,
            )
            settings = call.call_args.kwargs["itemwise"]
            self.assertEqual(settings["workers"], 4)
            self.assertEqual(Path(settings["ledger"]).name, "judgments")

    def test_prepare_default_preserves_whole_files(self):
        snapshot, _, rubric = fixture(atomic=True)
        snapshot["id"] = digest({k: v for k, v in snapshot.items() if k != "id"})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_new(root / "snapshot.json", snapshot)
            write_new(root / "rubric.json", rubric)
            write_new(root / "policy.json", rubric["bonus_policy"])
            with patch("sys.stdout", new_callable=io.StringIO):
                code = main(
                    [
                        "prepare",
                        str(root / "snapshot.json"),
                        "--rubric",
                        str(root / "rubric.json"),
                        "--policy",
                        str(root / "policy.json"),
                        "--out",
                        str(root / "packet"),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertTrue(
                all(
                    v["state"] in {"full", "unreadable"}
                    for v in read_json(root / "packet/bundle.json")["coverage"].values()
                )
            )
