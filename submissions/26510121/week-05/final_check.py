"""Check a clean submission export; never scan the local virtual environment."""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import mkdtemp
from verify_evidence import ROOT, inspect


def main():
    inspect()
    repo = ROOT.parents[2]
    prefix = ROOT.relative_to(repo).as_posix() + "/"
    result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", prefix],
                            cwd=repo, capture_output=True, text=True, check=True)
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    export = Path(mkdtemp(prefix="submission-check-", dir=runtime)).resolve()
    assert export.is_relative_to(runtime.resolve())
    for name in set(result.stdout.splitlines()):
        source = (repo / name).resolve()
        assert source.is_relative_to(ROOT)
        relative = source.relative_to(ROOT)
        assert not any(part in {".venv", ".runtime", "__pycache__"} for part in relative.parts)
        target = export / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    command = [sys.executable, str(repo / "scripts/check_week05.py"), str(export)]
    output = subprocess.run(command, cwd=repo, capture_output=True, env={**os.environ, "PYTHONUTF8": "1"})
    capture = ROOT / "checks" / "final-structure.txt"
    if capture.exists():
        raise RuntimeError("Use a new evidence capture name; never overwrite a check log")
    capture.write_bytes(output.stdout + output.stderr)
    print(output.stdout.decode("utf-8", errors="replace"), end="")
    if output.returncode:
        raise SystemExit(output.returncode)
    print("PASS: clean export contains own tracked/nonignored submission files only")


if __name__ == "__main__":
    main()
