"""Prove a completed suite resumes without a client, mutation, or exposed key."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from unittest.mock import patch

import run_korean as runner


def main():
    root, here = runner.ROOT, runner.HERE
    out = here / "runs" / runner.SUITE
    files = [out / "manifest.json", out / "results.csv", *sorted((root / "logs").glob(runner.SUITE + "-*.*"))]
    assert len(files) == 38
    def snapshot():
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    before = snapshot()
    output = io.StringIO()
    # There must be no work left, so client construction itself is forbidden.
    env_file = root.parent / ".env"
    with patch.object(runner.lab, "OpenRouterClient", side_effect=AssertionError("Unexpected API client construction")), \
         patch("sys.argv", ["run_korean.py", "--env-file", str(env_file), "--jobs", "3"]), redirect_stdout(output):
        runner.main()
    assert "72 episodes" in output.getvalue()
    assert before == snapshot()
    key = runner.lab.read_key(env_file).encode()
    scanned = 0
    for p in root.rglob("*"):
        if p.is_file() and "__pycache__" not in p.parts and p.name != ".env":
            assert key not in p.read_bytes(), f"Credential found in {p.relative_to(root)}"
            scanned += 1
    report = {"completed_episodes": 72, "api_client_constructions_on_resume": 0,
              "resume_preserved_all_raw_artifacts": True, "secret_scan_files": scanned,
              "secret_scan_passed": True, "preserved_artifact_hashes": before}
    (out / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(output.getvalue().strip())
    print(f"PASS: resume built no API client; {len(files)} raw files unchanged; {scanned} files passed secret scan.")


if __name__ == "__main__": main()
