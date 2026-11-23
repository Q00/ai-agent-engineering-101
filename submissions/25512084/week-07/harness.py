import subprocess
import tempfile
import os


def run_harness(prompt: str, timeout: int = 120) -> str:
    with tempfile.TemporaryDirectory() as workdir:
        result = subprocess.run(
            [
                os.path.expandvars(r"%APPDATA%\npm\codex.cmd") if os.name == "nt" else "codex",
                "exec",
                "--model",
                "gpt-6-luna",
                "--skip-git-repo-check",
                "--ephemeral",
                "--color",
                "never",
                "-",
            ],
            input=prompt,
            text=True,
            capture_output=True,
            cwd=workdir,
            timeout=timeout,
        )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Codex harness failed")

    return result.stdout.strip()


