from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from collections.abc import Generator

from cnp.archive import prepare
from cnp.settings import Settings, load_inputs

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def api_server(status: int) -> Generator[tuple[str, list[str]], None, None]:
    calls: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(body.decode())
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "id": "fixture",
                        "object": "chat.completion",
                        "created": 0,
                        "model": "fixture:free",
                        "choices": [
                            {
                                "index": 0,
                                "finish_reason": "stop",
                                "message": {
                                    "role": "assistant",
                                    "content": '{"bid":true,"confidence":80,"reason":"fixture"}',
                                },
                            }
                        ],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                    }
                    if status == 200
                    else {"error": {"message": "fixture quota", "code": status}}
                ).encode()
            )

        @override
        def log_message(self, format: str, *args: str) -> None:
            return

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1", calls
        finally:
            server.shutdown()
            thread.join(timeout=5)


def workspace(root: Path, endpoint: str) -> None:
    shutil.copytree(ROOT / "cnp", root / "cnp")
    for filename in ("run.py", "tasks.json", "uv.lock"):
        shutil.copy2(ROOT / filename, root / filename)
    config = Settings.model_validate_json((ROOT / "config.json").read_bytes())
    updated = config.model_copy(
        update={
            "base_url": endpoint,
            "model": "fixture:free",
            "min_call_interval_s": 0,
            "request_timeout_s": 1,
        }
    )
    (root / "config.json").write_text(updated.model_dump_json())


def cli(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "run.py", "--runs", "1"],
        cwd=root,
        env={**os.environ, "OPENAI_API_KEY": "fixture"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_cli_writes_real_run_artifacts_and_resumes_without_duplicates(tmp_path: Path) -> None:
    # Given the real CLI and a local wire-level API fixture.
    with api_server(200) as (endpoint, calls):
        workspace(tmp_path, endpoint)
        # When running three conditions once.
        result = cli(tmp_path)
        # Then all 54 contractor calls produce three official rows.
        assert result.returncode == 0, result.stderr
        with (tmp_path / "results.csv").open() as f:
            rows = list(csv.DictReader(f))
        assert [(r["tasks"], r["correct"], r["messages"]) for r in rows] == [("6", "2", "42")] * 3
        assert len(calls) == 54
        assert all('"gold"' not in c for c in calls)
        before = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (tmp_path / "logs").iterdir()
        }
        assert cli(tmp_path).returncode == 0
        assert len(calls) == 54
        assert before == {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (tmp_path / "logs").iterdir()
        }


def test_cli_retains_quota_failure_as_blank_counts(tmp_path: Path) -> None:
    # Given a provider that refuses the first call.
    with api_server(429) as (endpoint, calls):
        workspace(tmp_path, endpoint)
        # When the batch runs, then stop after one failed request.
        result = cli(tmp_path)
        assert result.returncode == 2, result.stderr
        with (tmp_path / "results.csv").open() as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["tasks"] == ""
        assert "429" in rows[0]["note"]
        assert len(calls) == 1
        assert len(list((tmp_path / "logs").iterdir())) == 1


def test_cli_refuses_configuration_change_in_same_output(tmp_path: Path) -> None:
    # Given an existing experiment identity, with no model calls needed.

    workspace(tmp_path, "http://127.0.0.1:1/v1")
    settings, _ = load_inputs(tmp_path)
    prepare(tmp_path, tmp_path, settings)
    updated = settings.model_copy(update={"temperature": 1.0})
    (tmp_path / "config.json").write_text(updated.model_dump_json())
    # When trying to append under a different setting, then stop before network.
    result = cli(tmp_path)
    assert result.returncode != 0
    assert "experiment_changed" in result.stderr
    assert not (tmp_path / "results.csv").exists()


def test_cli_preserves_interrupted_attempt_before_resuming(tmp_path: Path) -> None:
    # Given an interrupted allocation with a partial original console capture.
    with api_server(200) as (endpoint, calls):
        workspace(tmp_path, endpoint)
        directory = tmp_path / "runs" / "run-001"
        directory.mkdir(parents=True)
        (directory / "meta.json").write_text('{"run":1,"condition":"baseline"}')
        logs = tmp_path / "logs"
        logs.mkdir()
        original = logs / "run-001-baseline.txt"
        original.write_text("original partial capture\n")
        # When continuing, then retain the interrupted row plus three completed runs.
        result = cli(tmp_path)
        assert result.returncode == 0, result.stderr
        with (tmp_path / "results.csv").open() as f:
            results = list(csv.DictReader(f))
        assert len(results) == 4
        assert results[0]["tasks"] == ""
        assert "interrupted" in results[0]["note"]
        assert original.read_text() == "original partial capture\n"
        assert len(calls) == 54
