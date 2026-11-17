"""Read local or GitHub submission blobs; never check out or run student code."""

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from .storage import canonical, digest, write_new

MAX_FILE_BYTES = 8_000_000
MAX_TOTAL_BYTES = 64_000_000
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules"}
SECRET = re.compile(r"(?:sk-(?:ant-|or-v1-|proj-)[A-Za-z0-9_-]{16,})")


def command(args, cwd=None):
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        timeout=180,
        check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode:
        # Avoid echoing remote error bodies, command arguments or credentials.
        raise ValueError(f"{args[0]} failed (exit {result.returncode})")
    return result.stdout


def gh(endpoint, paginate=False):
    args = ["gh", "api", endpoint]
    if paginate:
        args.extend(["--paginate", "--slurp"])
    value = json.loads(command(args))
    return [item for page in value for item in page] if paginate else value


def document(data):
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("A file exceeds the 8 MB collection limit")
    try:
        text = data.decode("utf-8")
        if "\x00" in text:
            text = None
    except UnicodeDecodeError:
        text = None
    if text is not None and SECRET.search(text):
        raise ValueError("Possible credential detected; snapshot was not saved")
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "text": text,
    }


def finish(files, metadata, out):
    if sum(file["bytes"] for file in files.values()) > MAX_TOTAL_BYTES:
        raise ValueError("Snapshot exceeds the 64 MB collection limit")
    metadata_doc = canonical(metadata).encode()
    files["@metadata.json"] = document(metadata_doc)
    snapshot = {"format": 1, "metadata": metadata, "files": dict(sorted(files.items()))}
    snapshot["id"] = digest(snapshot)
    write_new(out, snapshot)
    return snapshot


def validate_week(week):
    if not re.fullmatch(r"week-\d{2}", week):
        raise ValueError("Expected week-NN")


def history_index(git, head, prefix):
    """Read typed object metadata without parsing untrusted commit messages."""
    commits = []
    shas = command(git + ["log", "--format=%H", head, "--", prefix]).decode().split()
    for sha in shas:
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Invalid Git history object ID")
        parents = command(git + ["show", "-s", "--format=%P", sha]).decode().split()
        changes = command(
            git
            + [
                "diff-tree",
                "--root",
                "--no-commit-id",
                "--name-status",
                "--no-renames",
                "-z",
                "-r",
                sha,
                "--",
                prefix,
            ]
        ).split(b"\0")
        if changes[-1] == b"":
            changes.pop()
        if len(changes) % 2:
            raise ValueError("Invalid Git name-status records")
        added = [
            path.decode()[len(prefix) :]
            for status, path in zip(changes[::2], changes[1::2])
            if status == b"A" and path.decode().startswith(prefix)
        ]
        commits.append({"sha": sha, "parents": parents, "added": added})
    scenarios = [e["sha"] for e in commits if "scenarios.json" in e["added"]]
    artifacts = sorted(
        {
            e["sha"]
            for e in commits
            if any(p == "results.csv" or p.startswith("logs/") for p in e["added"])
        }
    )
    pairs = [
        {
            "earlier": s,
            "later": a,
            "strict_ancestor": s != a
            and command(git + ["merge-base", s, a]).decode().strip() == s,
        }
        for s in scenarios
        for a in artifacts
    ]
    return {
        "format": 1,
        "head_sha": head,
        "prefix": prefix,
        "commits": commits,
        "ancestry_pairs": pairs,
    }


def collect_local(source, spec, out, week="week-05"):
    validate_week(week)
    source = Path(source).resolve()
    if not source.is_dir():
        raise ValueError("Submission directory does not exist")
    if Path(out).resolve().is_relative_to(source):
        raise ValueError("Write snapshots outside the student submission")
    files = {}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Symlinks are not read: {relative}")
        if not path.is_file():
            continue
        if path.name == ".env" or path.name.startswith(".env."):
            raise ValueError("Environment files must not enter an evaluation snapshot")
        if str(relative).startswith("@"):
            raise ValueError("Submission paths starting with @ are reserved")
        files[relative.as_posix()] = document(path.read_bytes())
    files["@spec.md"] = document(Path(spec).read_bytes())
    return finish(
        files,
        {"source": "local", "history_available": False, "week": week},
        out,
    )


def collect_pr(repo, number, out, spec_ref="main", week="week-05", head_sha=None):
    validate_week(week)
    if head_sha is not None and not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        raise ValueError("head_sha must be a full commit SHA")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Expected owner/repository")
    pr = gh(f"repos/{repo}/pulls/{number}")
    changes = gh(f"repos/{repo}/pulls/{number}/files?per_page=100", paginate=True)
    if len(changes) != pr["changed_files"]:
        raise ValueError("GitHub returned an incomplete changed-file list")
    owners = set()
    for change in changes:
        match = re.match(rf"submissions/(\d+)/{week}/", change["filename"])
        if match:
            owners.add(match[1])
    if len(owners) != 1:
        raise ValueError(f"PR must identify exactly one {week} submission directory")
    student = owners.pop()
    prefix = f"submissions/{student}/{week}/"
    spec_sha = gh(f"repos/{repo}/commits/{spec_ref}")["sha"]
    files = {}
    # A fresh bare repository has no student worktree, configuration or hooks.
    with tempfile.TemporaryDirectory(prefix="ax-collect-") as temp:
        command(["git", "init", "--bare", "--template=", temp])
        git = ["git", "--git-dir", temp, "-c", "core.hooksPath=/dev/null"]
        command(
            git
            + [
                "fetch",
                "--no-tags",
                f"https://github.com/{repo}.git",
                f"refs/pull/{number}/head",
                spec_sha,
            ]
        )
        head = head_sha or pr["head"]["sha"]
        command(git + ["cat-file", "-e", head])
        if head_sha:
            command(git + ["merge-base", "--is-ancestor", head, pr["head"]["sha"]])
        tree = command(git + ["ls-tree", "-r", "-z", head, "--", prefix])
        for entry in tree.split(b"\0"):
            if not entry:
                continue
            info, name = entry.split(b"\t", 1)
            mode, kind, sha = info.decode().split()
            path = name.decode()[len(prefix) :]
            if path.startswith("@"):
                raise ValueError("Submission paths starting with @ are reserved")
            if mode not in {"100644", "100755"} or kind != "blob":
                raise ValueError(f"Unsupported submission entry: {path}")
            if Path(path).name.startswith(".env"):
                raise ValueError("Environment file found in submission")
            size = int(command(git + ["cat-file", "-s", sha]))
            if size > MAX_FILE_BYTES:
                raise ValueError(f"File exceeds collection limit: {path}")
            files[path] = document(command(git + ["cat-file", "blob", sha]))
        # Only the upstream spec is trusted as assignment instructions.
        for target, path in [
            ("@spec.md", f"weeks/{week}/README.md"),
            ("@roster.md", f"roster/{student}.md"),
        ]:
            if (
                target == "@roster.md"
                and not command(git + ["ls-tree", spec_sha, "--", path]).strip()
            ):
                continue
            files[target] = document(command(git + ["show", f"{spec_sha}:{path}"]))
        checker_path = f"scripts/check_week{week[-2:]}.py"
        if command(git + ["ls-tree", spec_sha, "--", checker_path]).strip():
            files["@official-checker.py"] = document(
                command(git + ["show", f"{spec_sha}:{checker_path}"])
            )
        history = command(
            git
            + [
                "log",
                "--format=commit %H%nparents %P%nAuthorDate %aI%nCommitDate %cI%n%B",
                "--name-status",
                head,
                "--",
                prefix,
            ]
        )
        files["@history.txt"] = document(history)
        files["@history-index.json"] = document(
            json.dumps(history_index(git, head, prefix), indent=2).encode()
        )
    # GitHub exposes the current PR body, not its historical revision. Avoid
    # leaking post-review corrections into an evaluation of an older commit.
    if not head_sha or head_sha == pr["head"]["sha"]:
        files["@pr.md"] = document((pr["title"] + "\n\n" + (pr["body"] or "")).encode())
    metadata = {
        "source": "github",
        "repository": repo,
        "pr": number,
        "url": pr["html_url"],
        "author": pr["user"]["login"],
        "student_id": student,
        "head_sha": head,
        "current_pr_head_sha": pr["head"]["sha"],
        "historical": head != pr["head"]["sha"],
        "pr_body_included": "@pr.md" in files,
        "roster_included": "@roster.md" in files,
        "base_sha": pr["base"]["sha"],
        "spec_sha": spec_sha,
        "opened_at": pr["created_at"],
        "week": week,
        "history_available": True,
        "changed_paths": [item["filename"] for item in changes],
        "changed_paths_scope": "current PR, not historical commit diff",
    }
    return finish(files, metadata, out)


def verify_snapshot(snapshot):
    payload = {key: value for key, value in snapshot.items() if key != "id"}
    if snapshot.get("id") != digest(payload):
        raise ValueError("Snapshot digest mismatch")
    for doc in snapshot["files"].values():
        if doc["text"] is not None:
            data = doc["text"].encode()
            if hashlib.sha256(data).hexdigest() != doc["sha256"]:
                raise ValueError("Source text digest mismatch")
