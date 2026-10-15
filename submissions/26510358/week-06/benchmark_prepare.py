"""Prepare pinned peer sources for comparison without executing any agent.

Downloads only the exact commit archives in the historical review manifest.
The output must be a new directory outside this repository. The runner's
native/controlled inference mode is a separate, explicit execution decision.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "logs/cross/20261015-comparison-w0mX68/manifest.json"
EXCLUDED_NAMES = {"logs", ".venv", "venv", ".git", "__pycache__", "cache", "caches",
                  ".pytest_cache", ".mypy_cache", ".ruff_cache", ".DS_Store"}
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
MAX_EXTRACT_BYTES = 512 * 1024 * 1024
MAX_MEMBERS = 50000


class PreparationError(ValueError):
    pass


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def excluded(name):
    return name in EXCLUDED_NAMES or name == ".env" or name.startswith(".env.") or name.endswith((".pyc", ".pyo"))


def validate_entries(raw):
    if not isinstance(raw, list) or not raw:
        raise PreparationError("The source manifest must contain a nonempty participant list")
    entries, identities = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise PreparationError("Source participant must be an object")
        repo, commit, number = item.get("head_repo_full_name"), item.get("head_sha"), item.get("number")
        if (not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", repo)
                or repo.split("/")[1] in {".", ".."}):
            raise PreparationError("Repository must be a public GitHub owner/name without URL or path escapes")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
            raise PreparationError("Every peer needs a full 40-hex commit; branch names are forbidden")
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0 or number in identities:
            raise PreparationError("Pull request numbers must be positive and unique")
        student = item.get("student_id")
        if student is None:
            title = item.get("title")
            match = re.fullmatch(r"\[week-06\]\s+(\d{8})", title.strip()) if isinstance(title, str) else None
            student = match.group(1) if match else None
        if not isinstance(student, str) or not re.fullmatch(r"\d{8}", student):
            raise PreparationError("Student ID must be exactly eight digits")
        identities.add(number)
        commit = commit.lower()
        entries.append({"id": f"pr{number}", "pr": number, "repo": repo, "head_sha": commit,
                        "student_id": student,
                        "archive_url": f"https://codeload.github.com/{repo}/tar.gz/{commit}"})
    return entries


def download_archive(url, destination):
    """No API key, credentials, HEAD resolution, retries, or branch fallback."""
    request = urllib.request.Request(url, headers={"User-Agent": "week06-pinned-benchmark-prepare"})
    with urllib.request.urlopen(request, timeout=90) as response:
        final = urllib.parse.urlsplit(response.geturl())
        if final.scheme != "https" or final.hostname != "codeload.github.com":
            raise PreparationError("Archive download redirected outside HTTPS codeload.github.com")
        downloaded = 0
        with Path(destination).open("xb") as handle:
            while chunk := response.read(1024 * 1024):
                downloaded += len(chunk)
                if downloaded > MAX_DOWNLOAD_BYTES:
                    raise PreparationError("Archive exceeded the 256 MiB download limit")
                handle.write(chunk)


def extract_submission(archive_path, directory, entry):
    """Reject links/traversal/duplicates and extract only the chosen submission."""
    top = entry["repo"].split("/")[1] + "-" + entry["head_sha"]
    prefix = PurePosixPath(top, "submissions", entry["student_id"], "week-06")
    selected, seen, total = [], set(), 0
    with tarfile.open(archive_path, "r:gz") as archive:
        for count, member in enumerate(archive, start=1):
            if count > MAX_MEMBERS:
                raise PreparationError("Archive exceeded the member-count limit")
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or "\\" in member.name
                    or not path.parts or path.parts[0] != top):
                raise PreparationError("Unsafe or unexpected archive member path")
            if not (member.isfile() or member.isdir()) or member.linkname:
                raise PreparationError("Archive links and special files are not permitted")
            normalized = str(path)
            if normalized in seen:
                raise PreparationError("Duplicate archive members are not permitted")
            seen.add(normalized)
            if path == prefix or prefix in path.parents:
                relative = path.relative_to(prefix)
                if any(excluded(part) for part in relative.parts):
                    continue
                total += member.size
                if member.size < 0 or total > MAX_EXTRACT_BYTES:
                    raise PreparationError("Selected submission exceeded the extraction-size limit")
                selected.append(member)
        if not selected:
            raise PreparationError("Pinned archive does not contain the requested week-06 submission")
        archive.extractall(path=directory, members=selected, filter="data")
    source = Path(directory).joinpath(*prefix.parts)
    if not source.is_dir():
        raise PreparationError("Requested submission is not a directory")
    return source


def source_files(source):
    source = Path(source)
    for directory, directories, filenames in os.walk(source, followlinks=False):
        for name in list(directories):
            path = Path(directory) / name
            if excluded(name):
                directories.remove(name)
            elif path.is_symlink():
                raise PreparationError("Source directory contains an unsupported symlink")
        for name in sorted(filenames):
            if excluded(name):
                continue
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                raise PreparationError("Source contains a link or special file")
            yield path.relative_to(source), path


def file_hashes(source):
    return {relative.as_posix(): sha256(path) for relative, path in source_files(source)}


def copy_submission(source, target):
    """Copy bytes independently; never patch commands, prompts, or action logic."""
    before = file_hashes(source)
    target.mkdir()
    for relative, path in source_files(source):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with path.open("rb") as input_file, destination.open("xb") as output_file:
            shutil.copyfileobj(input_file, output_file)
        shutil.copymode(path, destination)
    after, copied = file_hashes(source), file_hashes(target)
    if before != after or copied != before:
        raise PreparationError("Source changed during snapshot copy or copied bytes differ")
    if "agent.json" not in copied:
        raise PreparationError("Submission has no agent.json")
    config = json.loads((target / "agent.json").read_text(encoding="utf-8"))
    for role in ("buyer", "seller"):
        if not isinstance(config.get(role), dict) or not isinstance(config[role].get("cmd"), str):
            raise PreparationError("agent.json must expose unchanged buyer and seller commands")
    return copied, config


def git_head(directory):
    # rev-parse reads metadata; avoid status hooks or executing any peer content.
    try:
        result = subprocess.run(["git", "--no-optional-locks", "-C", str(directory), "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", commit) else None


def prepare(out, *, ours=ROOT, source_manifest=DEFAULT_MANIFEST, downloader=download_archive):
    if Path(out).exists() or Path(out).is_symlink():
        raise FileExistsError("Preparation output already exists; choose a new --out directory")
    out, ours, source_manifest = Path(out).resolve(), Path(ours).resolve(), Path(source_manifest).resolve()
    repository = ROOT.parents[2]
    if out.is_relative_to(repository) or out.is_relative_to(ours):
        raise PreparationError("--out must be outside the repository and the source directory")
    entries = validate_entries(json.loads(source_manifest.read_text(encoding="utf-8")))
    if not ours.is_dir():
        raise PreparationError("--ours must point to an existing submission directory")
    out.mkdir(parents=True, exist_ok=False)
    (out / "archives").mkdir()
    (out / "participants").mkdir()
    report = {"status": "failed", "time": datetime.now(timezone.utc).isoformat(),
        "source_manifest": str(source_manifest), "source_manifest_sha256": sha256(source_manifest),
        "mode": "source_preparation_only_no_agent_or_provider_execution", "peer_count": len(entries),
        "excluded_names": sorted(EXCLUDED_NAMES), "excluded_patterns": [".env", ".env.*", "*.pyc", "*.pyo"],
        "branch_fallback": False, "action_or_prompt_changes": False, "participants": [], "failures": []}
    write_new(out / "prepare-request.json", {key: value for key, value in report.items()
                                             if key not in {"participants", "failures", "status"}})
    participants = []
    own_target = out / "participants/ours"
    try:
        hashes, agent = copy_submission(ours, own_target)
        participant = {"id": "ours", "path": str(own_target), "source_path": str(ours),
                       "head_sha": git_head(ours), "git_head_is_provenance_only_worktree_hashed": True,
                       "source_hashes": hashes, "agent": agent, "source_bytes_unchanged": True}
        participants.append(participant)
        report["participants"].append(participant)
    except Exception as exc:
        report["failures"].append({"id": "ours", "error": f"{type(exc).__name__}: {exc}"})
    for entry in entries:
        archive = out / "archives" / (entry["id"] + ".tar.gz")
        artifact = {**entry, "archive_path": str(archive), "archive_complete": False}
        try:
            downloader(entry["archive_url"], archive)
            artifact.update(archive_complete=True, archive_sha256=sha256(archive),
                            archive_bytes=archive.stat().st_size)
            with tempfile.TemporaryDirectory(prefix="a2a-pinned-extract-") as temporary:
                source = extract_submission(archive, temporary, entry)
                target = out / "participants" / entry["id"]
                hashes, agent = copy_submission(source, target)
            participant = {**entry, "path": str(target), "source_hashes": hashes,
                           "agent": agent, "source_bytes_unchanged": True,
                           "archive_sha256": artifact["archive_sha256"]}
            participants.append(participant)
            artifact.update(status="ready", participant=participant)
            report["participants"].append(participant)
        except Exception as exc:
            artifact.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            if archive.exists():
                artifact.update(archive_sha256=sha256(archive), archive_bytes=archive.stat().st_size)
            report["failures"].append({"id": entry["id"], "error": artifact["error"]})
        write_new(out / (entry["id"] + "-preparation.json"), artifact)
    if not report["failures"]:
        write_new(out / "participants.json", participants)
        report["status"] = "ready"
    report["agents_executed"] = False
    write_new(out / "prepare-report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="New external work directory; existing paths are refused")
    parser.add_argument("--ours", default=str(ROOT), help="Current or frozen local submission source")
    parser.add_argument("--source-manifest", default=str(DEFAULT_MANIFEST), help="Pinned historical PR metadata JSON")
    args = parser.parse_args()
    try:
        report = prepare(args.out, ours=args.ours, source_manifest=args.source_manifest)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Preparation failed: {type(exc).__name__}: {exc}\n")
    print(json.dumps({"status": report["status"], "out": str(Path(args.out).resolve()),
                      "failures": report["failures"], "agents_executed": False}))
    raise SystemExit(0 if report["status"] == "ready" else 1)


if __name__ == "__main__":
    main()
