"""Push the strategist's records to a separate git repository after every run.

The records (runs, changes, reports, briefs, prompts, raw answers, and the SKILL.md as it is after
the run) are copied into a working copy at home/logrepo, committed, and pushed. That repository is
written only by this process, so the local copy is the source of truth: it is based on the remote
branch once, when it has no commits yet (and only after the remote answered), a rejected push is
retried after a rebase that keeps our side of any conflict, and lock files left by a killed git are
cleared. Whatever still fails is left for the next run. Publishing never fails a run; the outcome
goes to home/publish.jsonl, which the next commit carries.

SSH trusts GitHub only with the host keys pinned in the image (/etc/ssh/ssh_known_hosts, from
https://api.github.com/meta); another host is trusted on first use and kept in home/known_hosts.

    STRATEGIST_LOG_REPO    remote URL (e.g. git@github.com:<you>/<repo>.git); empty disables publishing
    STRATEGIST_LOG_BRANCH  branch, default main
    STRATEGIST_LOG_KEY     SSH private key for that repository (a deploy key with write access);
                           default: ssh's own default key in ~/.ssh

The records hold the strategy and the prompts, so the repository should be private.
"""
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from memory.store import append_jsonl

FILES = ("runs.jsonl", "changes.jsonl", "inventory.jsonl", "publish.jsonl")
DIRS = ("reports", "briefs", "prompts", "raw")
GIT_TIMEOUT_S = 60
STALE_LOCK_S = 600                      # well over GIT_TIMEOUT_S: no live git of ours holds it
AUTHOR = ("psyoung82-strategist", "strategist@localhost")


class GitError(Exception):
    pass


def _env(home, key):
    known = Path(home) / "known_hosts"
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
           "-o", f"UserKnownHostsFile={known}"]
    if key:
        ssh += ["-i", str(key), "-o", "IdentitiesOnly=yes"]
    return dict(os.environ, GIT_SSH_COMMAND=shlex.join(ssh), GIT_TERMINAL_PROMPT="0",
                GIT_AUTHOR_NAME=AUTHOR[0], GIT_AUTHOR_EMAIL=AUTHOR[1],
                GIT_COMMITTER_NAME=AUTHOR[0], GIT_COMMITTER_EMAIL=AUTHOR[1])


def _git(repo, env, *args, check=True):
    try:
        p = subprocess.run(["git", "-C", str(repo), *args], env=env, capture_output=True, text=True,
                           timeout=GIT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]}: timed out after {GIT_TIMEOUT_S} s") from None
    if check and p.returncode:
        raise GitError(f"git {args[0]}: {(p.stderr or p.stdout).strip()[-300:]}")
    return p


def _prepare(repo, url, branch, env):
    """A working copy whose origin is url; based on origin/branch when it has no commits yet."""
    if not (repo / ".git").is_dir():
        repo.mkdir(parents=True, exist_ok=True)
        _git(repo, env, "init", "-q")
    if _git(repo, env, "remote", "get-url", "origin", check=False).returncode:
        _git(repo, env, "remote", "add", "origin", url)
    else:
        _git(repo, env, "remote", "set-url", "origin", url)
    if _git(repo, env, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0:
        return
    _git(repo, env, "checkout", "-q", "-B", branch)
    # Start a new history only when the remote answered that the branch does not exist (exit 2);
    # an unreachable remote must not become a second root that conflicts with it forever.
    found = _git(repo, env, "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}", check=False)
    if found.returncode == 0:
        _git(repo, env, "fetch", "-q", "origin", branch)
        _git(repo, env, "reset", "-q", "--hard", "FETCH_HEAD")     # an empty working copy: nothing to lose
    elif found.returncode != 2:
        raise GitError(f"git ls-remote: {(found.stderr or found.stdout).strip()[-300:]}")


def _clear_stale_locks(repo):
    """Lock files a killed git (timeout, docker stop, power cut) left behind; nothing else writes here."""
    now = time.time()
    for lock in (repo / ".git").rglob("*.lock") if (repo / ".git").is_dir() else []:
        try:
            if now - lock.stat().st_mtime > STALE_LOCK_S:
                lock.unlink()
        except OSError:
            pass


def _push(repo, env, branch):
    if _git(repo, env, "push", "-q", "origin", f"HEAD:{branch}", check=False).returncode == 0:
        return
    # Rejected: replay our commits on the remote. "-X theirs" keeps our side of a conflict (in a rebase,
    # "theirs" is the commit being replayed). If even that stops, record the remote as merged and keep ours.
    if _git(repo, env, "pull", "-q", "--rebase", "-X", "theirs", "origin", branch, check=False).returncode:
        _abort_rebase(repo, env)
        _git(repo, env, "fetch", "-q", "origin", branch)
        _git(repo, env, "merge", "-q", "-s", "ours", "-m", "merge remote changes, keeping the strategist's records",
             "FETCH_HEAD")
    _git(repo, env, "push", "-q", "origin", f"HEAD:{branch}")


def _abort_rebase(repo, env):
    if (repo / ".git" / "rebase-merge").exists() or (repo / ".git" / "rebase-apply").exists():
        try:
            _git(repo, env, "rebase", "--abort", check=False)
        except GitError:
            pass


def _same(src, dst):
    try:
        a, b = src.stat(), dst.stat()
    except OSError:
        return False
    return a.st_size == b.st_size and int(a.st_mtime) == int(b.st_mtime)


def _mirror(home, skill, repo):
    """Copy the records into the working copy; unchanged files are skipped. Nothing is deleted."""
    for name in FILES:
        src = home / name
        if src.is_file() and not _same(src, repo / name):
            shutil.copy2(src, repo / name)
    for d in DIRS:
        for src in sorted((home / d).glob("*")) if (home / d).is_dir() else []:
            dst = repo / d / src.name
            if src.is_file() and not _same(src, dst):
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
    if Path(skill).is_file():
        shutil.copyfile(skill, repo / "SKILL.md")


def _message(rec):
    applied = ", ".join(f"{a['key']} {a['old']}->{a['new']}" for a in rec.get("applied") or [])
    head = f"run {rec.get('run_id')}: {rec.get('status')}"
    return head + (f"; {applied}" if applied else "") + f"\n\npolicy {rec.get('policy_ver_before')} -> " \
        f"{rec.get('policy_ver_after')}, mode {rec.get('mode')}, errors {len(rec.get('errors') or [])}"


def publish(cfg, rec):
    """Commit and push the records of run rec. Returns the publish.jsonl row, or None when disabled."""
    if not cfg.log_repo:
        return None
    home = Path(cfg.home)
    repo = home / "logrepo"
    env = _env(home, cfg.log_key)
    row = {"ts": time.time(), "run_id": rec.get("run_id"), "committed": False, "pushed": False, "error": None}
    try:
        _clear_stale_locks(repo)
        _abort_rebase(repo, env)                        # one a killed git left half done
        _prepare(repo, cfg.log_repo, cfg.log_branch, env)
        _mirror(home, cfg.skill, repo)
        _git(repo, env, "add", "-A")
        if _git(repo, env, "diff", "--cached", "--quiet", check=False).returncode:
            _git(repo, env, "commit", "-q", "-m", _message(rec))
            row["committed"] = True
        _push(repo, env, cfg.log_branch)
        row["pushed"] = True
    except (GitError, OSError) as e:
        row["error"] = f"{type(e).__name__}: {e}"[:400]
        _abort_rebase(repo, env)
    try:
        append_jsonl(home / "publish.jsonl", row)
    except OSError:
        pass
    return row
