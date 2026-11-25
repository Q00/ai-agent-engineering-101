"""Host-only, fail-closed full discovery in an inherited macOS sandbox.

Use /Users/noah/work/ax-test-venv/bin/python -I -B isolated_suite.py.
No receipts, operational data, Git metadata or inherited environment are copied.
The command inside the sandbox is exactly PYTHON -m unittest discover -q.
Network is denied INCLUDING loopback: the existing Round3 HTTP tests may fail.
Such failures are evidence, never skips or a reason to relax the sandbox.
This candidate cannot attest detached-descendant quiescence. It returns nonzero
even for suite_rc=0 until a host supervisor can supply that missing verification.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading


PYTHON = Path("/Users/noah/work/ax-test-venv/bin/python")
SANDBOX = "/usr/bin/sandbox-exec"
SOURCE = Path(__file__).resolve().parent
REPO = SOURCE.parents[2]
LIVE = Path("/Users/noah/work/ai-agent-engineering-101/submissions/26622007/week-08")
BLOCKED = ("codex", "claude", "claude-b", "curl", "ssh", "scp", "sftp",
           "openclaw", "market", "market-kit", "market_kit", "sendmail", "mail",
           "gog", "slack", "telegram", "osascript", "launchctl", "pkill", "pgrep")
DATA = ("pricing.json", "catalog.json", "agent.json", "rank_paid_fixture.json")


class SentinelMonitor:
    """Parent-owned counts; children may only write to a non-readable FIFO.

    The protected directory and sentinel binaries cannot be removed/replaced by
    the sandbox. Unlike a regular log, truncating the FIFO cannot erase evidence.
    No command arguments, environment, source or other sensitive data is logged.
    """
    def __init__(self, root):
        directory = root / "evidence"
        directory.mkdir(mode=0o700)
        self.path = directory / "sentinel-hits"
        os.mkfifo(self.path, 0o600)
        self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)
        self.hits = 0
        self.error = False
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.collect, daemon=True)
        self.thread.start()

    def drain(self):
        # Bound each batch so a continuous writer cannot prevent shutdown.
        for _ in range(64):
            try:
                data = os.read(self.fd, 4096)
            except BlockingIOError:
                return
            if not data:
                return
            self.hits += max(1, data.count(b"\n"))

    def collect(self):
        try:
            while not self.stop.wait(0.01):
                self.drain()
            self.drain()
        except OSError:
            self.error = True

    def close(self):
        if self.fd is None:
            return
        self.stop.set()
        self.thread.join()
        os.close(self.fd)
        self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


def regular(path):
    """Reject symlinks in every component, special files and missing inputs."""
    if path.absolute() != path.resolve() or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("input must be a canonical regular file")
    return path


def inputs():
    # Explicit static inputs only. In particular: no .market/logs/memory/meta/.git.
    paths = list(SOURCE.glob("*.py")) + [SOURCE / name for name in DATA]
    for directory in (SOURCE / "tools", SOURCE / "testdata"):
        for base, dirs, names in os.walk(directory, followlinks=False):
            if any((Path(base) / name).is_symlink() for name in dirs):
                raise ValueError("source directory symlink")
            for name in names:
                path = Path(base) / name
                if path.suffix in (".py", ".json"):
                    paths.append(path)
    paths.append(REPO / "scripts/market_kit.py")
    if set(SOURCE.rglob("test_*.py")) - set(paths):
        raise ValueError("test files outside static copy policy; partial discovery refused")
    return sorted(regular(path) for path in paths)


def fingerprint(paths):
    """Hash/line counts only; never display protected file contents."""
    digest, files, lines = hashlib.sha256(), 0, 0
    for path in sorted(set(paths)):
        digest.update(os.fsencode(path))
        if path.is_symlink():
            digest.update(b"symlink:" + os.fsencode(os.readlink(path)))
        elif not path.exists():
            digest.update(b"absent")
        else:
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError("protected input is not a regular file")
            file_hash = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(65536), b""):
                    file_hash.update(block)
                    lines += block.count(b"\n")
            digest.update(file_hash.digest())
            files += 1
    return dict(sha256=digest.hexdigest(), files=files, lines=lines)


def protected_groups(paths):
    home = Path.home()
    # Static controls only. Live ledgers, databases and moving state are NOT read
    # or compared: their natural changes cannot be attributed to this run.
    controls = ("pricing.json", "buyer_quote_alignment_policy.py",
                "tools/quote_owner_executor.py", ".market/seller_concession_trial.json",
                ".market/seller_reader_trial.json", ".market/quote_hold/config.json")
    return {
        "source_inputs": paths,
        "source_controls": [SOURCE / name for name in controls],
        "live_static_controls": [LIVE / name for name in controls],
        "user_settings": [home / name for name in (
            ".codex/config.toml", ".claude/settings.json", ".claude-b/settings.json",
            ".gitconfig", ".config/git/config")],
    }


def profile(root):
    quote = lambda p: json.dumps(str(Path(p).resolve()))
    reads = (root, Path(sys.prefix), Path(sys.base_prefix), Path("/System"),
             Path("/usr"), Path("/bin"), Path("/sbin"),
             Path("/Library/Developer/CommandLineTools"), Path("/Library/Apple"),
             Path("/private/var/db/dyld"))
    executables = (PYTHON, PYTHON.resolve(), Path("/bin/sh"), Path("/bin/sleep"),
                   Path("/usr/bin/env"), Path("/usr/bin/git"), Path(SANDBOX),
                   Path("/Library/Developer/CommandLineTools/usr/bin/git"))
    return "\n".join((
        "(version 1)", "(allow default)", "(deny network*)",
        "(deny file-read*)", "(allow file-read-metadata)",
        "(allow file-read* " + " ".join(f"(subpath {quote(p)})" for p in reads) + ")",
        '(allow file-read* (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random"))',
        "(deny file-write*)", f'(allow file-write* (subpath {quote(root)}) (literal "/dev/null"))',
        # Evidence is parent-owned, immutable from the sandbox. A child can send
        # a FIFO event but cannot consume, truncate, unlink or replace a record.
        f'(deny file-write* (subpath {quote(root / "bin")}) '
        f'(subpath {quote(root / "bootstrap")}) (subpath {quote(root / "evidence")}) '
        f'(literal {quote(root / "sandbox.sb")}))',
        f'(allow file-write-data (literal {quote(root / "evidence/sentinel-hits")}))',
        f'(deny file-read-data (subpath {quote(root / "evidence")}))',
        f'(deny file-write-create file-write-unlink (literal {quote(root)}))',
        "(deny process-exec*)",
        "(allow process-exec* " + " ".join(f"(literal {quote(p)})" for p in executables)
        + f" (subpath {quote(root)}) (subpath \"/Library/Developer/CommandLineTools/usr/libexec/git-core\"))",
        "(deny process-info*)", "(allow process-info* (target self) (target same-sandbox))",
        "(deny signal)", "(allow signal (target self) (target same-sandbox))",
        # Explicit denies also prevent metadata reads of sensitive namespaces.
        "(deny file-read* " + " ".join(f"(subpath {quote(p)})" for p in (
            SOURCE, LIVE, Path.home() / ".codex", Path.home() / ".claude",
            Path.home() / ".claude-b", Path.home() / ".openclaw",
            Path.home() / ".ssh", Path.home() / ".config",
            Path.home() / ".gitconfig", REPO / ".git")) + ")",
    )) + "\n"


def environment(root, copy):
    # All values are runner-owned constants or freshly created paths. HOME and
    # CODEX_HOME are omitted, never redirected. No parent environment is copied.
    env = dict(PATH=f"{root / 'bin'}:/usr/bin:/bin", LANG="en_US.UTF-8",
               LC_ALL="en_US.UTF-8", TZ="Asia/Seoul", TMPDIR=str(root / "tmp"),
               PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
               PYTHONPATH=str(root / "bootstrap"), HARNESS="offline-sentinel",
               MARKET_DIR=str(root / "state"), META_DIR=str(copy), META_PUSH="0",
               AGENT_MEMORY_DIR=str(root / "memory"), AGENT_TOOLS=str(copy / "tools"),
               CODEX_CWD_ROOT=str(root / "model-cwd"), SHOP_PRICING=str(copy / "pricing.json"),
               SHOP_MEMORY=str(root / "state/memory.json"),
               XDG_CONFIG_HOME=str(root / "settings"), XDG_CACHE_HOME=str(root / "cache"),
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM="/dev/null",
               GIT_CONFIG_GLOBAL="/dev/null", GIT_TERMINAL_PROMPT="0",
               GIT_OPTIONAL_LOCKS="0", GIT_CEILING_DIRECTORIES=str(root))
    configs = {"core.hooksPath": str(root / "empty-hooks"),
               "init.templateDir": str(root / "empty-template"),
               "core.fsmonitor": "false", "commit.gpgsign": "false", "tag.gpgsign": "false"}
    env["GIT_CONFIG_COUNT"] = str(len(configs))
    for index, (key, value) in enumerate(configs.items()):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


def prepare(root, paths, hits):
    copy = root / "repo/submissions/26622007/week-08"
    for path in paths:
        target = root / "repo" / path.relative_to(REPO)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    for name in ("tmp", "state", "memory", "model-cwd", "settings", "cache", "bin",
                 "empty-hooks", "empty-template", "bootstrap"):
        (root / name).mkdir()
    for name in BLOCKED:
        path = root / "bin" / name
        path.write_text(f"#!/bin/sh\nprintf '%s\\n' {name} >> '{hits}'\nexit 97\n")
        path.chmod(0o700)
    # Fixture shebangs use /usr/bin/env python3. They use the same pinned venv.
    for name in ("python", "python3"):
        (root / "bin" / name).symlink_to(PYTHON)
    # Bootstrap does not import/patch tests, products, unittest, HTTP or sockets.
    # It only fixes tempfile for Python children (even ones replacing their env).
    # The OS profile remains the security boundary if a child omits PYTHONPATH.
    (root / "bootstrap/sitecustomize.py").write_text(
        "import tempfile\n" + f"tempfile.tempdir = {str(root / 'tmp')!r}\n")
    sandbox_profile = root / "sandbox.sb"
    sandbox_profile.write_text(profile(root))
    return copy, sandbox_profile


def execute(command, cwd, env, timeout):
    # Kill only the newly created process group on timeout; never global pkill.
    # A file, not a pipe: detached fixture children cannot hold communicate open.
    with tempfile.TemporaryFile(dir=env["TMPDIR"]) as stream:
        with subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                              stdout=stream, stderr=subprocess.STDOUT,
                              start_new_session=True, close_fds=True) as child:
            expired = False
            try:
                child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                expired = True
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            stream.seek(0)
            return child.returncode, stream.read(), expired


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 3600:
        parser.error("timeout must be between 1 and 3600 seconds")
    if Path(sys.executable) != PYTHON or sys.platform != "darwin" or not sys.flags.isolated:
        parser.error(f"requires macOS and {PYTHON} -I -B")
    paths = inputs()
    groups = protected_groups(paths)
    before = {name: fingerprint(group) for name, group in groups.items()}
    suite_rc, timed_out, safe = None, False, False
    with tempfile.TemporaryDirectory(prefix="ax-suite-", dir="/private/tmp") as directory, \
            SentinelMonitor(Path(directory).resolve()) as monitor:
        root = Path(directory).resolve()
        copy, sandbox_profile = prepare(root, paths, monitor.path)
        env = environment(root, copy)
        command = [SANDBOX, "-f", str(sandbox_profile), str(PYTHON)]
        print(f"RUNTIME python={sys.version.split()[0]} executable={PYTHON}", flush=True)
        print(f"INPUTS static_files={len(paths)} test_modules={len(list(copy.glob('test_*.py')))} "
              "test_edits=0 controller_injections=0 live_ledger_copies=0", flush=True)
        print("BOUNDARY network=deny(including-loopback) writes=temp-only "
              "reads=static-copy+system+python git=fixture-only settings=denied "
              "environment=fresh-allowlist HOME/CODEX_HOME=omitted", flush=True)
        # Harmless interpreter-only probe. A refused nested sandbox is returned
        # once; there is no unsandboxed fallback, profile relaxation or retry.
        rc, output, expired = execute(command + ["-I", "-B", "-c", "print('AX_SANDBOX_READY')"],
                                      copy, env, 20)
        sys.stdout.flush()
        sys.stdout.buffer.write(output)
        sys.stdout.buffer.flush()
        if rc or expired or output.strip() != b"AX_SANDBOX_READY":
            print(f"SANDBOX_REFUSED command={SANDBOX} -f <temp>/sandbox.sb {PYTHON} "
                  f"probe_rc={rc} timeout={expired}; full_suite=NOT_RUN", flush=True)
            result_rc = rc if rc > 0 else 78
        else:
            raw = command + ["-m", "unittest", "discover", "-q"]
            print(f"COMMAND {PYTHON} -m unittest discover -q", flush=True)
            suite_rc, output, timed_out = execute(raw, copy, env, args.timeout)
            sys.stdout.buffer.write(output)
            sys.stdout.buffer.flush()
            result_rc = suite_rc if suite_rc >= 0 else 128 - suite_rc
        after = {name: fingerprint(group) for name, group in groups.items()}
        monitor.close()
        hit_count = monitor.hits
        safe = before == after and hit_count == 0 and not monitor.error
        print("PROTECTED_STATIC " + json.dumps(dict(before=before, after=after), sort_keys=True), flush=True)
        print(f"RESULT suite_rc={suite_rc} sentinel_hits={hit_count} timeout={timed_out} "
              f"static_unchanged={before == after} static_guard={'PASS' if safe else 'FAIL'} "
              f"boundary={'NOT_EXECUTED' if suite_rc is None else 'UNVERIFIED'} "
              "live_moving_state=NOT_COMPARED", flush=True)
        # Never turn a failing suite into success, including expected RED rc=1.
        # A safety failure overrides only a zero rc. Always disclose the raw rc.
        if result_rc == 0 and (not safe or timed_out or suite_rc is None
                              or not re.search(rb"Ran [1-9][0-9]* tests? in ", output)):
            result_rc = 3
        # Python fixtures intentionally create separate sessions. killpg alone
        # cannot attest all descendants have stopped; do not mint a green result
        # from a top-level exit or a raceable zero sentinel count. Raw suite_rc
        # and stdout remain available to the host's final supervised verification.
        if result_rc == 0:
            result_rc = 3
        print("ASSURANCE descendant_quiescence=UNVERIFIED success_promotion=BLOCKED; "
              "timeout_cleanup=original-process-group-only", flush=True)
        print(f"EXIT rc={result_rc}; receipt=NONE; host-final-commit-verification=REQUIRED", flush=True)
        return result_rc


if __name__ == "__main__":
    sys.exit(main())
