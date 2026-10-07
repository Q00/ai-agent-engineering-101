"""Execute the unchanged upstream week-05 checker from Git, without a checkout.

Only the student's output log is written by the caller. Shared scripts remain
read-only. Fetch upstream/main before use if the pinned commit is unavailable.
"""

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path


BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
UPSTREAM_COMMIT = "48d1d0b29c41675b49a7ac0b871a58303ff63e9e"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default=UPSTREAM_COMMIT)
    args = parser.parse_args()
    revision = subprocess.check_output(
        ["git", "rev-parse", "--verify", args.revision + "^{commit}"], cwd=ROOT,
    ).decode().strip()
    source_path = "scripts/check_week05.py"
    source = subprocess.check_output(["git", "show", f"{revision}:{source_path}"], cwd=ROOT)
    print(f"[official source] Q00/ai-agent-engineering-101 commit={revision}", flush=True)
    print(f"[official source] {source_path} sha256={hashlib.sha256(source).hexdigest()}", flush=True)
    print("[execution] unchanged upstream source compiled and executed from memory", flush=True)
    original_argv = sys.argv
    sys.argv = [source_path, str(BASE)]
    try:
        exec(compile(source.decode("utf-8"), source_path, "exec"), {
            "__name__": "__main__", "__file__": str(ROOT / source_path),
        })
    except SystemExit as result:
        return result.code
    finally:
        sys.argv = original_argv
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
