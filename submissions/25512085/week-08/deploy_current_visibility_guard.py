"""Linux-only, explicit current-runtime handoff; no boot/key configuration writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

BASE = Path(__file__).resolve().parent
SESSION = "market"
ARTIFACTS = ("guarded_market_kit.py", "visibility_guard.py", "run_guarded_market.sh",
             "restock.py", "agent.json", "deploy_current_visibility_guard.py")


def command(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True)


def processes():
    table = {}
    for folder in Path("/proc").iterdir():
        if not folder.name.isdigit():
            continue
        try:
            fields = (folder / "stat").read_text().rsplit(")", 1)[1].split()
            args = (folder / "cmdline").read_bytes().decode(errors="replace").split("\0")
            table[int(folder.name)] = {"state": fields[0], "parent": int(fields[1]),
                                      "start": int(fields[19]), "args": args}
        except (OSError, ValueError, IndexError):
            pass
    return table


def descendants(table, root):
    found = {root} if root in table else set()
    while True:
        more = {p for p, row in table.items() if row["parent"] in found}
        if more <= found:
            return found
        found |= more


def role(row):
    return next((Path(a).name for a in row["args"] if a and Path(a).name in
                 ("market_kit.py", "guarded_market_kit.py", "seller.py")), None)


def alive(pid, start):
    row = processes().get(pid)
    return bool(row and row["start"] == start and row["state"] not in ("Z", "X"))


def verified_signal(pid, start, sig):
    if pid <= 1:
        raise RuntimeError("Unsafe PID")
    if alive(pid, start):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def protected_digests():
    cron = command("crontab", "-l")  # Existing crontab is required; read errors are not absence.
    return {"launcher_sha256": hashlib.sha256((Path.home() / "run_kit.sh").read_bytes()).hexdigest(),
            "crontab_sha256": hashlib.sha256(cron.stdout.encode()).hexdigest()}


def single_pane():
    windows = command("tmux", "list-windows", "-t", SESSION, "-F", "#{window_id}").stdout.splitlines()
    panes = command("tmux", "list-panes", "-s", "-t", SESSION, "-F", "#{pane_pid}").stdout.splitlines()
    if len(windows) != 1 or len(panes) != 1:
        raise RuntimeError("Authorized session must contain exactly one window and pane")
    return int(panes[0])


def scoped_kit(table, root):
    scope = descendants(table, root)
    kits = [p for p in scope if role(table[p]) in ("market_kit.py", "guarded_market_kit.py")]
    if len(kits) != 1:
        raise RuntimeError("Expected exactly one attributed kit in the market pane")
    if not any("25512085/week-08" in a.replace("\\", "/") for a in table[kits[0]]["args"]):
        raise RuntimeError("Kit is not attributed to the authorized submission")
    return kits[0], scope


def terminate_scope(root, expected_kit, expected_start, transition=None):
    """Freeze/re-snapshot the pane tree; signals are protected against PID reuse."""
    frozen = {}
    completed = False
    try:
        for _ in range(8):
            table = processes()
            if not alive(expected_kit, expected_start):
                raise RuntimeError("Kit changed before scoped shutdown")
            scope = descendants(table, root)
            if expected_kit not in scope:
                raise RuntimeError("Kit no longer belongs to the authorized pane")
            new = scope - frozen.keys()
            if not new:
                break
            # Parents first prevent the old loop from respawning the kit.
            for pid in sorted(new):
                frozen[pid] = table[pid]["start"]
                if transition is not None:
                    transition["touched"] = True
                verified_signal(pid, frozen[pid], signal.SIGSTOP)
        else:
            raise RuntimeError("Process tree did not stabilize")
        verified_signal(expected_kit, expected_start, signal.SIGINT)
        verified_signal(expected_kit, expected_start, signal.SIGCONT)
        deadline = time.monotonic() + 35
        while alive(expected_kit, expected_start) and time.monotonic() < deadline:
            # Capture and freeze any new child made by a daemon during cleanup.
            table = processes()
            for pid in descendants(table, expected_kit) - frozen.keys():
                frozen[pid] = table[pid]["start"]
                verified_signal(pid, frozen[pid], signal.SIGSTOP)
            time.sleep(.1)
        graceful = not alive(expected_kit, expected_start)
        # Remaining jobs belong to the verified, frozen old pane, never global pkill.
        for pid, start in frozen.items():
            verified_signal(pid, start, signal.SIGKILL)
        command("tmux", "kill-session", "-t", SESSION, check=False)
        if any(alive(pid, start) for pid, start in frozen.items()):
            raise RuntimeError("Old scoped processes remain alive")
        completed = True
        return {"graceful_kit_exit": graceful, "scoped_processes": len(frozen)}
    finally:
        if not completed:
            for pid, start in frozen.items():
                verified_signal(pid, start, signal.SIGCONT)


def start_session(guarded):
    path = BASE / "run_guarded_market.sh" if guarded else Path.home() / "run_kit.sh"
    command("tmux", "new-session", "-d", "-s", SESSION, f"exec /bin/bash '{path}'")


def runtime_ready(guarded, started, seconds=180):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            root = single_pane()
            table = processes()
            kit, scope = scoped_kit(table, root)
            required_role = "guarded_market_kit.py" if guarded else "market_kit.py"
            seller_n = sum(role(table[p]) == "seller.py" for p in scope)
            inventory_fresh = (BASE / ".market/inventory.json").stat().st_mtime >= started
            if role(table[kit]) == required_role and seller_n == 1 and inventory_fresh:
                if not guarded:
                    return {"kit_pid": kit, "seller_count": seller_n}
                state = json.loads((BASE / ".market/visibility_guard.json").read_text())
                stamp = state.get("checked_at", state.get("ts", 0))
                pid = state.get("pid", state.get("runtime_pid"))
                if (pid == kit and isinstance(stamp, (int, float)) and started <= stamp <= time.time()
                        and time.time() - stamp <= 180):
                    return {"kit_pid": kit, "seller_count": seller_n, "guard_state": state}
        except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError):
            pass
        time.sleep(.5)
    raise RuntimeError("New runtime readiness not established")


def rollback():
    info = {"attempted": True}
    if command("tmux", "has-session", "-t", SESSION, check=False).returncode == 0:
        root = single_pane()
        table = processes()
        try:
            kit, _ = scoped_kit(table, root)
        except RuntimeError:
            # Only this handoff-created, single-pane session may be empty/at a shell.
            if len(descendants(table, root)) > 1:
                raise RuntimeError("Cannot attribute incomplete new runtime during rollback")
            command("tmux", "kill-session", "-t", SESSION)
        else:
            info["shutdown"] = terminate_scope(root, kit, table[kit]["start"])
    started = time.time()
    start_session(False)
    info["original_runtime"] = runtime_ready(False, started)
    info["verified"] = True
    return info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()
    if os.name != "posix" or not Path("/proc").is_dir():
        raise SystemExit("Linux VM only")
    actual = command("git", "-C", str(BASE), "rev-parse", "--short", "HEAD").stdout.strip()
    if actual != args.expected_commit:
        raise SystemExit("Unexpected deployment commit")
    if command("git", "-C", str(BASE), "status", "--porcelain", "--", *ARTIFACTS).stdout.strip():
        raise SystemExit("Runtime artifacts are not clean")
    tracked = command("git", "-C", str(BASE), "ls-files", "--", *ARTIFACTS).stdout.splitlines()
    if len(tracked) != len(ARTIFACTS):
        raise SystemExit("Expected tracked runtime artifacts missing")
    for name in ("shop_key.pem", "buyer_key.pem"):
        if not (BASE / name).is_file():
            raise SystemExit("Existing signing key missing")
    command("bash", "-n", str(BASE / "run_guarded_market.sh"))
    before = protected_digests()
    expected_boot = json.loads((BASE / "logs/visibility-preflight-20261105-174751.json").read_text())["boot_configuration"]
    if before != expected_boot:
        raise SystemExit("Boot configuration differs from approved preflight; no process changed")
    root = single_pane()
    table = processes()
    kit, scope = scoped_kit(table, root)
    receipt = {"started_at": time.time(), "execute": args.execute, "commit": actual,
               "boot_settings_before": before, "old_kit_pid": kit, "scope_count": len(scope),
               "artifact_sha256": {n: hashlib.sha256((BASE/n).read_bytes()).hexdigest() for n in ARTIFACTS}}
    if not args.execute:
        print(json.dumps(receipt))
        return
    # Confirm a writable log before stopping any process.
    log = BASE / "logs" / ("visibility-handoff-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + ".jsonl")
    with log.open("x", encoding="utf-8") as f:
        f.write(json.dumps({"started_at": receipt["started_at"], "status": "handoff-started"}))
    transition = {}
    error = None
    try:
        receipt["shutdown"] = terminate_scope(root, kit, table[kit]["start"], transition)
        start_time = time.time()
        start_session(True)
        receipt["new_runtime"] = runtime_ready(True, start_time)
        receipt["boot_settings_after"] = protected_digests()
        if receipt["boot_settings_after"] != before:
            raise RuntimeError("Boot configuration changed unexpectedly")
        receipt["boot_settings_unchanged"] = True
        receipt["success"] = True
    except Exception as exc:
        error = type(exc).__name__  # Never log potentially secret command/response values.
        receipt["error_type"] = error
        receipt["success"] = False
        if transition.get("touched"):
            try:
                receipt["rollback"] = rollback()
            except Exception as rollback_error:
                receipt["rollback"] = {"verified": False, "error_type": type(rollback_error).__name__}
    receipt["finished_at"] = time.time()
    # Append, never replace the initial execution record.
    try:
        with log.open("a", encoding="utf-8") as f:
            f.write("\n" + json.dumps(receipt) + "\n")
    except OSError:
        if receipt.get("success"):
            receipt["success"] = False
            receipt["error_type"] = "ReceiptWriteError"
            try:
                receipt["rollback"] = rollback()
            except Exception as rollback_error:
                receipt["rollback"] = {"verified": False, "error_type": type(rollback_error).__name__}
    print(json.dumps(receipt))
    if not receipt.get("success"):
        raise SystemExit("Runtime handoff failed; inspect the sanitized receipt")


if __name__ == "__main__":
    main()
