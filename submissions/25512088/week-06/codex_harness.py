"""Small, auditable adapter from Python to a non-interactive Codex decision."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path


MODEL = os.getenv("WEEK06_MODEL", "gpt-5.6-luna")
EFFORT = os.getenv("WEEK06_REASONING_EFFORT", "low")
DEFAULT_SCHEMA = Path(__file__).with_name("decision.schema.json")


def decide(prompt: str, *, schema: Path = DEFAULT_SCHEMA, timeout: int = 180) -> dict:
    """Ask Codex for one decision and return the schema-validated JSON object."""
    with tempfile.TemporaryDirectory(prefix="week06-codex-") as run_dir:
        answer = Path(run_dir) / "answer.json"
        command = [
            "codex", "exec", "--skip-git-repo-check", "--ephemeral",
            "--ignore-user-config", "--ignore-rules", "--sandbox", "read-only",
            "--model", MODEL, "--config", f'model_reasoning_effort="{EFFORT}"',
            "--output-schema", str(schema), "--output-last-message", str(answer),
            "--cd", run_dir, "-",
        ]
        result = subprocess.run(
            command, input=prompt, text=True, capture_output=True,
            timeout=timeout, check=False,
        )
        if result.returncode != 0 or not answer.is_file():
            detail = (result.stderr or result.stdout)[-1200:]
            raise RuntimeError(f"Codex decision failed ({result.returncode}): {detail}")
        decision = json.loads(answer.read_text(encoding="utf-8"))
        print("HARNESS", json.dumps(
            {"model": MODEL, "effort": EFFORT, "decision": decision},
            ensure_ascii=False,
        ), flush=True)
        return decision
