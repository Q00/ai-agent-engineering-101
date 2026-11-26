#!/usr/bin/env python3
"""pre-receive hook of the server hub (~/git/ai-agent-engineering-101.git), installed as a copy by ops/install_deploy.sh.

Local agents (Grok, Codex, ...) push here; the server deploys week-08 from here (ops/deploy_pull.py). A push is
refused when a new commit touches a path outside submissions/26510358/ (AGENTS.md invariant 1), adds a key or env
file (invariant 3), or adds a blob over 50 MB (keeps clones small; GitHub refuses over 100 MB). Force pushes and
deletions are refused by the hub's own config (receive.denyNonFastForwards, receive.denyDeletes): invariant 4.
"""
import re
import subprocess
import sys

SCOPE = "submissions/26510358/"
MAX_BLOB = 50_000_000
SECRET = re.compile(r"(\.pem|\.env|api-key[^/]*|owner\.env|\.key)$", re.I)
ZERO = "0" * 40


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def problems(old, new):
    rng = [new, "--not", "--all"] if old == ZERO else [f"{old}..{new}"]
    out = []
    for sha in git("rev-list", *rng).split():
        rows = git("diff-tree", "--no-commit-id", "-r", "--root", sha).splitlines()
        for row in rows:
            meta, path = row.split("\t", 1)
            _, mode, _, blob, status = meta[1:].split()
            if not path.startswith(SCOPE):
                out.append(f"{sha[:8]} touches {path} (outside {SCOPE})")
            if status != "D" and SECRET.search(path):
                out.append(f"{sha[:8]} adds {path} (keys and env files are never committed)")
            if status != "D" and mode != "160000":
                size = int(git("cat-file", "-s", blob))
                if size > MAX_BLOB:
                    out.append(f"{sha[:8]} adds {path} ({size / 1e6:.0f} MB > {MAX_BLOB / 1e6:.0f} MB)")
    return out


def main():
    bad = []
    for line in sys.stdin:
        old, new, ref = line.split()
        if new != ZERO:
            bad += [f"{ref}: {p}" for p in problems(old, new)]
    if bad:
        print("hub refused the push:\n  " + "\n  ".join(bad[:20]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
