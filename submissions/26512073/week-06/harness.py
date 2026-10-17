"""Small transport adapter: prompt in, JSON object out; no trading rules yet."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


BASE = Path(__file__).resolve().parent


def find_codex():
    """Support normal terminals as well as the VS Code extension environment."""
    override = os.environ.get("CODEX_EXECUTABLE")
    if override:
        executable = shutil.which(override)
        if executable:
            return executable
        raise RuntimeError("CODEX_EXECUTABLE does not name an available executable")
    executable = shutil.which("codex")
    if executable:
        return executable
    # VS Code exposes its bundled CLI on PATH only in some child environments.
    extensions = Path.home() / ".vscode" / "extensions"
    candidates = list(extensions.glob("openai.chatgpt-*/bin/windows-x86_64/codex.exe"))
    if candidates:
        return str(max(candidates, key=lambda path: path.stat().st_mtime))
    raise RuntimeError("Codex not found. Add it to PATH or set CODEX_EXECUTABLE to its full path.")


def call_harness(prompt, timeout=90):
    executable = find_codex()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    log_dir = BASE / "development_logs"
    log_dir.mkdir(exist_ok=True)
    log = log_dir / f"harness_{stamp}.txt"
    command = [executable, "exec", "--skip-git-repo-check", "--ephemeral",
               "--sandbox", "read-only", "--color", "never",
               "-c", "project_doc_max_bytes=0", "-"]
    # Each call gets a fresh conversation and an empty working directory.
    with log.open("x", encoding="utf-8") as capture:
        capture.write("COMMAND: " + json.dumps(command) + "\nPROMPT:\n" + prompt + "\n")
        capture.flush()
        with tempfile.TemporaryDirectory(prefix="harness_", dir=BASE) as work:
            try:
                result = subprocess.run(command, input=prompt, text=True,
                                        encoding="utf-8", errors="replace",
                                        capture_output=True, cwd=work, timeout=timeout)
            except subprocess.TimeoutExpired as error:
                capture.write(f"TIMEOUT: {timeout}s\n")
                for label, value in [("STDOUT", error.stdout), ("STDERR", error.stderr)]:
                    if isinstance(value, bytes):
                        value = value.decode("utf-8", errors="replace")
                    capture.write(f"{label}:\n{value or ''}\n")
                raise
        capture.write(f"EXIT: {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}\n")
        capture.flush()
        if result.returncode:
            raise RuntimeError(f"Harness exited {result.returncode}; see {log}")
        try:
            answer = json.loads(result.stdout)
            if not isinstance(answer, dict):
                raise ValueError("Expected a JSON object")
        except ValueError as error:
            capture.write(f"PARSE ERROR: {error}\n")
            raise
        return answer


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if not args.smoke:
        parser.error("Use --smoke for the first transport check")
    print(json.dumps(call_harness(
        'Do not use tools. Return only this JSON object: {"status":"ok"}'
    )))
