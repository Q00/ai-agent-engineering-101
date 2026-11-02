"""Server-only staged v4 install with source/service backup and startup rollback.

Keeps the existing week07-market.service name, EnvironmentFile, PATH and identity.
Writes only this student's week-08 tree and one user-systemd drop-in.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

UNIT = "week07-market.service"
RELATIVE = "agent-market-week07/repo/submissions/26510124/week-08"


def say(text):
    print(text, flush=True)


def allowed_source(name):
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or str(p) != name or len(p.parts) > 2:
        return False
    if p.name.startswith("."):
        return name == ".gitignore"
    if len(p.parts) == 1:
        return p.suffix == ".py" or name in (
            "README.md", "requirements.txt", "requirements-lock.txt", "agent.json", "buyer-policy.json",
            "feedback-config.json", "shop-adaptive.json", "shop-round2.json", "release-manifest.json")
    return ((p.parts[0] == "prompts" and p.suffix == ".txt")
            or (p.parts[0] == "deploy" and p.suffix == ".py")
            or name in ("vendor/market_kit_v4.py", "vendor/SOURCE.json"))


def unpack(archive, expected, stage):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise ValueError("Archive checksum differs from the Mac's manifest")
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        if len(members) > 200 or len({m.name for m in members}) != len(members):
            raise ValueError("Duplicate or excessive archive members")
        for member in members:
            if not member.isfile() or member.size > 10_000_000 or not allowed_source(member.name):
                raise ValueError("Archive contains a non-source member: " + member.name)
        if sum(m.size for m in members) > 30_000_000:
            raise ValueError("Archive is too large")
        meta = bundle.getmember("release-manifest.json")
        manifest = json.loads(bundle.extractfile(meta).read())
        if set(manifest["files"]) != {m.name for m in members if m.name != meta.name}:
            raise ValueError("Manifest and archive file lists disagree")
        stage.mkdir(mode=0o700)
        for member in members:
            if member.name == meta.name:
                continue
            raw = bundle.extractfile(member).read()
            if hashlib.sha256(raw).hexdigest() != manifest["files"][member.name]:
                raise ValueError("Source checksum mismatch: " + member.name)
            target = stage / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
    return manifest


def checked_path(root, relative):
    if not allowed_source(relative) or relative == "release-manifest.json":
        raise ValueError("Refusing runtime/private path: " + relative)
    path = root / relative
    for part in [path, *path.parents]:
        if part == root:
            break
        if part.is_symlink():
            raise ValueError("Refusing a symlink in destination: " + relative)
    if path.exists() and not path.is_file():
        raise ValueError("Destination is not a source file: " + relative)
    return path


def dropin_text(root, python):
    # Concrete absolute server paths contain no user-provided shell fragments.
    if not root.is_absolute() or not python.is_absolute():
        raise ValueError("Systemd paths must be absolute")
    if any(c in str(root) + str(python) for c in ('"', '\n', '\r', '\0', '\\', '%', '$')):
        raise ValueError("Unexpected systemd path characters")
    # WorkingDirectory is a path value, not the shell-word syntax of ExecStart.
    # systemd's config_parse_working_directory does not strip surrounding quotes.
    return ('[Unit]\nDescription=Week 08 dyishappy seller and buyer (kit v4)\n\n[Service]\n'
            f'WorkingDirectory={root}\nExecStart=\n'
            f'ExecStart="{python}" "{root / "run_round3.py"}" --join\n')


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".deploy-", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


@contextmanager
def deployment_lock(root):
    market = root / ".market"
    if market.is_symlink():
        raise ValueError("Refusing a symlinked runtime directory")
    market.mkdir(exist_ok=True)
    path = market / "deploy.lock"
    if path.is_symlink():
        raise ValueError("Refusing a symlinked deployment lock")
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another deployment is running; current service was not changed")
        yield


def save_state(backup, state):
    atomic_write(backup / "state.json", (json.dumps(state, indent=2) + "\n").encode())


def install_sources(root, stage, manifest, backup, state):
    for name in sorted(manifest["files"]):
        dst = checked_path(root, name)
        if dst.is_file() and hashlib.sha256(dst.read_bytes()).hexdigest() == manifest["files"][name]:
            continue
        existed = dst.exists()
        if existed:
            saved = backup / "source" / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, saved)
        state["files"].append({"name": name, "existed": existed})
        save_state(backup, state)
        atomic_write(dst, (stage / name).read_bytes())


def restore_sources(root, backup, state):
    for item in reversed(state["files"]):
        dst = checked_path(root, item["name"])
        if item["existed"]:
            atomic_write(dst, (backup / "source" / item["name"]).read_bytes())
        elif dst.exists():
            dst.unlink()


def systemctl(*args, check=True):
    return subprocess.run(["systemctl", "--user", *args], text=True, capture_output=True, check=check)


def verify_service_configuration(root, evidence):
    # Record selected non-secret fields, never Environment or raw ExecStart args.
    fields = {name: systemctl("show", UNIT, "--property=" + name, "--value").stdout.strip()
              for name in ("LoadState", "WorkingDirectory", "FragmentPath", "DropInPaths")}
    effective = systemctl("show", UNIT, "--property=ExecStart", "--value").stdout
    fields["week08_exec_matches"] = str(root / "run_round3.py") in effective
    fields["expected_working_directory"] = str(root)
    report = evidence / "service-configuration.json"
    with report.open("x") as out:
        out.write(json.dumps(fields, indent=2) + "\n")
    if (fields["LoadState"] != "loaded" or fields["WorkingDirectory"] != str(root)
            or not fields["week08_exec_matches"]):
        say("SERVICE DIAGNOSTIC: " + json.dumps(fields))
        raise ValueError("Service configuration check failed; details saved at " + str(report))


def capture(command, path, env):
    with path.open("x") as stream:
        stream.write("COMMAND: " + repr([str(x) for x in command]) + "\n")
        stream.flush()
        result = subprocess.run([str(x) for x in command], stdout=stream, stderr=subprocess.STDOUT, env=env)
        stream.write(f"EXIT: {result.returncode}\n")
    if result.returncode:
        raise RuntimeError(f"Validation failed; original output saved at {path}")


def log_start(path):
    return (path.stat().st_ino, path.stat().st_size) if path.exists() else (None, 0)


def new_log(path, before):
    if not path.exists():
        return ""
    with path.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        offset = before[1] if stat.st_ino == before[0] and stat.st_size >= before[1] else 0
        stream.seek(max(offset, stat.st_size - 2_000_000))
        return stream.read(2_000_000).decode("utf-8", errors="replace")


def registrations(text, root, name):
    # A later automatic restart invalidates registration from an earlier process.
    text = text[text.rfind("[kit] market_kit "):] if "[kit] market_kit " in text else ""
    version = f"[kit] market_kit 4, {root}" in text
    shop = f"shop registered as {name}, round " in text
    buyer = f"buyer registered as {name}, round " in text
    urls = re.findall(r"\[shop\].*?the kit at (https://[^\s]+)", text)
    return version and shop and buyer, urls[-1].rstrip("/") if urls else None


def acp_ok(card, public):
    if not isinstance(card, dict):
        return False
    caps, api = card.get("capabilities"), card.get("tools")
    if not isinstance(caps, dict) or not isinstance(api, list):
        return False
    services, methods = caps.get("services"), caps.get("payment_methods")
    return (isinstance(services, list) and isinstance(methods, list) and card.get("protocol") == "acp"
            and card.get("endpoint") == public + "/acp"
            and "checkout" in services and "ap2-mandate" in methods
            and {"create_checkout_session", "complete_checkout_session"}.issubset(
                {t.get("name") for t in api if isinstance(t, dict) and isinstance(t.get("name"), str)}))


def wait_ready(root, name, before, evidence):
    deadline = time.monotonic() + 210
    while time.monotonic() < deadline:
        text = new_log(root / "logs/market-kit.log", before)
        registered, public = registrations(text, root, name)
        if registered and public and systemctl("is-active", UNIT, check=False).stdout.strip() == "active":
            parsed = urlsplit(public)
            if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".trycloudflare.com"):
                raise ValueError("Unexpected quick-tunnel shop URL")
            try:
                with urlopen(public + "/.well-known/acp.json", timeout=10) as response:
                    raw = response.read(100_001)
                card = json.loads(raw) if len(raw) <= 100_000 else None
                if acp_ok(card, public):
                    (evidence / "acp.json").write_text(json.dumps(card, indent=2) + "\n")
                    return public
            except (OSError, ValueError):
                pass
        say("Waiting for v4 shop/buyer registration and public ACP response...")
        time.sleep(10)
    raise RuntimeError("Registration or ACP response was not confirmed within 210 seconds")


def rollback(root, backup, dropin, state):
    systemctl("stop", UNIT, check=False)
    restore_sources(root, backup, state)
    if state["dropin_existed"]:
        atomic_write(dropin, (backup / "previous-dropin.conf").read_bytes())
    elif dropin.exists():
        dropin.unlink()
    systemctl("daemon-reload")
    if state["was_active"]:
        systemctl("start", UNIT)
    state["rolled_back"] = True
    save_state(backup, state)
    say("Previous source/service configuration restored. Existing keys and logs were retained.")


def deploy(archive, digest, run):
    os.umask(0o077)
    root = Path.home() / RELATIVE
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Expected a regular week-08 deployment directory")
    if not re.fullmatch(r"[0-9TZ-]+-[a-f0-9]{8}", run):
        raise ValueError("Invalid deployment id")
    with deployment_lock(root):
        return deploy_locked(root, archive, digest, run)


def deploy_locked(root, archive, digest, run):
    incoming = root / ".market/incoming" / run
    stage = incoming / "stage"
    manifest = unpack(archive, digest, stage)
    evidence = root / "logs" / ("deployment-" + run)
    evidence.mkdir(parents=True, exist_ok=True)
    backup = root / ".market/deploy-backups" / run
    backup.mkdir(parents=True, mode=0o700)
    old = root.parent / "week-07"
    python = old / ".venv/bin/python"
    dropin = Path.home() / ".config/systemd/user" / (UNIT + ".d") / "90-week08.conf"
    if not python.is_file():
        raise ValueError("Existing week-07 virtual environment is missing")
    if systemctl("show", UNIT, "--property=LoadState", "--value").stdout.strip() != "loaded":
        raise ValueError("Existing market service is not loaded")
    deployment_root = Path.home() / "agent-market-week07"
    env_path = deployment_root / "secrets/market.env"
    if not env_path.is_file() or not re.search(r"(?m)^\s*MARKET_OWNER\s*=\s*\S+", env_path.read_text()):
        raise ValueError("Existing private market environment is unavailable; no secret was requested")
    environment_files = systemctl("show", UNIT, "--property=EnvironmentFiles", "--value").stdout
    if str(env_path) not in environment_files:
        raise ValueError("Existing service does not use the expected private EnvironmentFile")
    env = {**os.environ, "PATH": os.pathsep.join([str(python.parent), str(deployment_root / "bin"),
                                                str(Path.home() / ".local/bin"), os.environ.get("PATH", "")])}
    for executable in ("claude", "cloudflared", "systemd-analyze"):
        if shutil.which(executable, path=env["PATH"]) is None:
            raise ValueError(executable + " is not available in the deployment PATH")
    for name in manifest["files"]:
        checked_path(root, name)
    if dropin.is_symlink() or dropin.parent.is_symlink():
        raise ValueError("Refusing a symlinked systemd drop-in")
    config = dropin_text(root, python)
    candidate = incoming / "week08-preflight.service"
    candidate.write_text(config)
    # Use the server's real systemd parser before stopping any existing service.
    # verify only checks configuration; it does not start the candidate unit.
    capture(["systemd-analyze", "--user", "verify", candidate], evidence / "systemd-preflight.log", env)
    say("Checking staged source and existing identities before stopping the old service...")
    check_identity = ("import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                      "from run_round3 import identity,verify_kit; verify_kit(Path(sys.argv[1])); "
                      "identity(Path(sys.argv[2]),Path(sys.argv[3])); print('KIT v4 and both existing identities validated')")
    capture([python, "-c", check_identity, stage, old, root], evidence / "identity-preflight.log", env)
    capture([python, stage / "run_checks.py"], evidence / "offline-checks.log", env)
    say("Offline checks passed. Saving the current configuration and installing week-08...")
    state = {"files": [], "was_active": systemctl("is-active", UNIT, check=False).stdout.strip() == "active",
             "dropin_existed": dropin.is_file(), "run": run, "source_manifest": manifest}
    if dropin.is_file():
        shutil.copy2(dropin, backup / "previous-dropin.conf")
    save_state(backup, state)
    stopped = False
    try:
        stopped = True
        systemctl("stop", UNIT)
        install_sources(root, stage, manifest, backup, state)
        capture([python, root / "run_round3.py", "--prepare-identity"], evidence / "identity-prepared.log", env)
        atomic_write(dropin, config.encode())
        systemctl("daemon-reload")
        verify_service_configuration(root, evidence)
        before = log_start(root / "logs/market-kit.log")
        systemctl("start", UNIT)
        name = json.loads((root / "shop-adaptive.json").read_text())["name"]
        public = wait_ready(root, name, before, evidence)
        state.update(success=True, shop_url=public, completed_at=datetime.now(timezone.utc).isoformat())
        save_state(backup, state)
        (evidence / "result.json").write_text(json.dumps({"kit": "4", "folder": str(root), "service": UNIT,
            "identity": "matches week-07", "shop_registered": True, "buyer_registered": True,
            "acp_verified": True, "shop_url": public, "backup": str(backup)}, indent=2) + "\n")
        say("READY: market_kit 4; week-08; shop and buyer registered; ACP verified.")
        say("SHOP: " + public)
        say("SERVICE: " + UNIT + " (same service name, now running week-08)")
        say("EVIDENCE: " + str(evidence))
    except BaseException:
        if stopped:
            try:
                # Finish rollback if the SSH session closes while restoring files.
                previous = {sig: signal.signal(sig, signal.SIG_IGN)
                            for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)}
                try:
                    rollback(root, backup, dropin, state)
                finally:
                    for sig, handler in previous.items():
                        signal.signal(sig, handler)
            except Exception as exc:
                say("ROLLBACK NEEDS ATTENTION: " + str(exc))
                say("Backup retained at " + str(backup))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    def interrupted(signum, _frame):
        raise RuntimeError("Deployment interrupted by signal " + str(signum))
    for sig in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(sig, interrupted)
    try:
        deploy(args.archive.resolve(), args.sha256, args.run)
    except (Exception, KeyboardInterrupt) as exc:
        say("DEPLOYMENT NOT CONFIRMED: " + type(exc).__name__ + ": " + str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
