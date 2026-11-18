import subprocess
import tempfile


def run_harness(prompt: str, timeout: int = 120) -> str:
    with tempfile.TemporaryDirectory() as workdir:
        result = subprocess.run(
            [
                "codex",
                "exec",
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
