"""Restart the existing market unit at an observed buyer result boundary.

The helper never submits orders, reviews, checkouts or settlements. A restart
does not preserve the seller's in-memory tasks or checkout sessions. All output
uses a new evidence directory; the running log is only read, never rewritten.
"""
from datetime import datetime, timezone
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
from urllib.request import Request, urlopen


WEEK08 = Path(__file__).resolve().parents[1]
WEEK07 = WEEK08.parent / "week-07"
UNIT = "week08-myeonggi-market.service"
MARKET = "https://market.ourolabs.ai"
NAME = "myeonggi"
OWNER = "SUNGMYEONGGI"
KIT_LOG = WEEK07 / "logs" / "market-kit.log"
BUYER_SCRIPTS = {"market_buyer.py", "buyer.py"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def save_json(directory, name, value):
    with (directory / name).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def unit_property(name):
    return subprocess.check_output(
        ["systemctl", "--user", "show", UNIT, "--property=" + name, "--value"],
        text=True, timeout=8).strip()


def process_start(pid):
    """Read the kernel start tick so a reused PID is never signalled."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        return text[text.rfind(")") + 2:].split()[19]
    except (FileNotFoundError, ProcessLookupError, IndexError):
        return None


def resume_if_same(pid, start):
    if start is not None and process_start(pid) == start:
        try:
            os.kill(pid, signal.SIGCONT)
        except ProcessLookupError:
            pass


def buyer_pids(control_group):
    """Inspect script names internally without logging argv or environments."""
    root = Path("/sys/fs/cgroup").resolve()
    group = (root / control_group.lstrip("/")).resolve()
    if not group.is_relative_to(root) or not control_group:
        raise ValueError("Invalid service control group")
    pids = []
    for raw_pid in (group / "cgroup.procs").read_text().split():
        pid = int(raw_pid)
        try:
            args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        except (FileNotFoundError, ProcessLookupError):
            continue
        scripts = {Path(arg.decode(errors="replace")).name for arg in args}
        if scripts & BUYER_SCRIPTS:
            pids.append(pid)
    return pids


def result_boundary():
    with KIT_LOG.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        stream.seek(max(0, size - 1_048_576))
        text = stream.read().decode("utf-8", errors="replace")
    # An unfinished final line is never accepted as a result boundary.
    lines = text[:text.rfind("\n") + 1].splitlines()
    last_order = last_result = -1
    for i, line in enumerate(lines):
        if re.match(r"^\d{2}:\d{2}:\d{2} \[order ", line):
            last_order = i
        elif re.match(r"^\d{2}:\d{2}:\d{2} \[market\] ledger #\d+:", line):
            last_result = i
    if last_order >= 0 and last_result > last_order:
        return lines[last_result]
    return None


def pause_at_boundary(pid, start, control_group, timeout):
    deadline = time.monotonic() + timeout
    next_progress = 0
    while time.monotonic() < deadline:
        if process_start(pid) != start:
            raise RuntimeError("The original service process changed before restart")
        if time.monotonic() >= next_progress:
            print("Waiting for an observed completed buyer order", flush=True)
            next_progress = time.monotonic() + 15
        if buyer_pids(control_group) or not result_boundary():
            time.sleep(0.1)
            continue
        os.kill(pid, signal.SIGSTOP)
        try:
            boundary = result_boundary()
            if boundary and not buyer_pids(control_group) and process_start(pid) == start:
                return boundary
        except BaseException:
            resume_if_same(pid, start)
            raise
        resume_if_same(pid, start)
        time.sleep(0.1)
    raise TimeoutError("No buyer result boundary observed; no restart was requested")


def fetch_json(url, *, timeout=8):
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError("Public response exceeded the evidence size limit")
        return json.loads(data), response.status


def own_profiles(board):
    profiles = {}
    for role in ("shops", "buyers"):
        rows = [row for row in board.get(role, []) if row.get("name") == NAME]
        if len(rows) != 1 or (rows[0].get("owner") or {}).get("login", "").lower() != OWNER.lower():
            raise ValueError("Expected market identity is not present under its GitHub owner")
        profiles[role] = {key: rows[0].get(key) for key in ("name", "joined", "alive", "deals")}
    return profiles


def key_hashes():
    # Hashes and contents stay in this process. Only equality is reported.
    return {name: hashlib.sha256((WEEK07 / name).read_bytes()).digest()
            for name in ("shop_key.pem", "buyer_key.pem")}


def snapshot_log(directory):
    digest = hashlib.sha256()
    with KIT_LOG.open("rb") as source, (directory / "market-kit-before.log.gz").open("xb") as target:
        stat = os.fstat(source.fileno())
        remaining = stat.st_size
        with gzip.GzipFile(fileobj=target, mode="wb", mtime=0) as compressed:
            while remaining:
                chunk = source.read(min(1_048_576, remaining))
                if not chunk:
                    raise RuntimeError("The source log was truncated during the bounded snapshot")
                compressed.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
    return {"file": "market-kit-before.log.gz", "bytes": stat.st_size,
            "raw_sha256": digest.hexdigest(), "device": stat.st_dev, "inode": stat.st_ino,
            "source_modified_at_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()}


def restart(evidence_dir, boundary_timeout):
    evidence_dir = Path(evidence_dir).resolve()
    if not evidence_dir.is_relative_to((WEEK08 / "logs").resolve()):
        raise ValueError("Evidence directory must be below week-08/logs")
    evidence_dir.mkdir(parents=True, exist_ok=False)
    report = {"started_at_utc": utc_now(), "unit": UNIT, "passed": False,
              "restart_requested": False, "network_methods": ["GET"],
              "api_keys_present": {name: bool(os.environ.get(name)) for name in
                                   ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY")},
              "limits": ["Seller tasks and checkout sessions are in memory and may be interrupted.",
                         "No new model test, purchase or settlement is requested by this helper.",
                         "Registration readiness does not prove a successful new trade."]}
    old_pid, old_start = None, None
    progress_done = threading.Event()
    try:
        if unit_property("ActiveState") != "active":
            raise RuntimeError("The existing market service is not active")
        old_pid = int(unit_property("MainPID"))
        old_start = process_start(old_pid)
        if old_pid <= 0 or old_start is None:
            raise RuntimeError("The existing market process could not be identified")
        control_group = unit_property("ControlGroup")
        report["old_pid"] = old_pid
        before, _ = fetch_json(MARKET + "/api/board")
        shops_before, _ = fetch_json(MARKET + "/api/shops")
        profiles_before = own_profiles(before)
        hashes_before = key_hashes()
        save_json(evidence_dir, "board-before.json", before)
        save_json(evidence_dir, "shops-before.json", shops_before)
        report["raw_log_before"] = snapshot_log(evidence_dir)
        # Do all slow I/O before SIGSTOP. Only the final local guard and the
        # same-unit restart remain after the observed boundary is frozen.
        report["last_old_result"] = pause_at_boundary(
            old_pid, old_start, control_group, boundary_timeout)
        report["restart_requested"] = True

        def readiness_progress():
            while not progress_done.wait(15):
                print("Waiting for the same market identity and public endpoints", flush=True)

        threading.Thread(target=readiness_progress, daemon=True).start()
        subprocess.run(["systemctl", "--user", "restart", UNIT], check=True,
                       text=True, capture_output=True, timeout=45)
        print("Restart requested; checking public market registration and ACP", flush=True)
        deadline = time.monotonic() + 150
        latest_error = "not checked"
        while time.monotonic() < deadline:
            try:
                new_pid = int(unit_property("MainPID"))
                if unit_property("ActiveState") != "active" or new_pid <= 0 or new_pid == old_pid:
                    raise ValueError("A new active service process is not yet observed")
                timeout = min(8, max(0.1, deadline - time.monotonic()))
                board, _ = fetch_json(MARKET + "/api/board", timeout=timeout)
                profiles_after = own_profiles(board)
                for role in ("shops", "buyers"):
                    if profiles_after[role]["joined"] != profiles_before[role]["joined"]:
                        raise ValueError("The market registration timestamp changed")
                    if profiles_after[role]["alive"] is not True:
                        raise ValueError("A market role is not yet online")
                same_round = board.get("round") == before.get("round")
                if same_round:
                    for role in ("shops", "buyers"):
                        if profiles_after[role]["deals"] < profiles_before[role]["deals"]:
                            raise ValueError("A same-round cumulative deal count decreased")
                timeout = min(8, max(0.1, deadline - time.monotonic()))
                shops, _ = fetch_json(MARKET + "/api/shops", timeout=timeout)
                registered = next(row for row in shops["shops"] if row.get("name") == NAME)
                public_url = registered["url"].rstrip("/")
                timeout = min(8, max(0.1, deadline - time.monotonic()))
                acp, acp_status = fetch_json(public_url + "/.well-known/acp.json", timeout=timeout)
                if (acp_status != 200 or acp.get("protocol") != "acp"
                        or acp.get("endpoint") != public_url + "/acp"
                        or "checkout" not in acp.get("capabilities", {}).get("services", [])
                        or "ap2-mandate" not in acp.get("capabilities", {}).get("payment_methods", [])):
                    raise ValueError("Public ACP readiness checks did not match the registered shop")
                timeout = min(8, max(0.1, deadline - time.monotonic()))
                card, card_status = fetch_json(public_url + "/.well-known/agent-card.json", timeout=timeout)
                interfaces = card.get("supportedInterfaces") or []
                if card_status != 200 or not interfaces or not all(
                        isinstance(row, dict) and str(row.get("url", "")).startswith(public_url + "/")
                        for row in interfaces):
                    raise ValueError("Public Agent Card readiness checks did not match the registered shop")
                if key_hashes() != hashes_before:
                    raise ValueError("The signing key files changed during restart")
                if int(unit_property("MainPID")) != new_pid or unit_property("ActiveState") != "active":
                    raise ValueError("The service process changed during public readiness checks")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Public readiness window expired")
                save_json(evidence_dir, "board-after.json", board)
                save_json(evidence_dir, "shops-after.json", shops)
                save_json(evidence_dir, "public-acp.json", acp)
                save_json(evidence_dir, "public-agent-card.json", card)
                report.update(passed=True, new_pid=new_pid, keys_unchanged=True,
                              profiles_unchanged=True, both_roles_alive=True,
                              public_url=public_url, acp_http_status=acp_status,
                              card_http_status=card_status, round_before=before.get("round"),
                              round_after=board.get("round"), same_round=same_round,
                              cumulative_deals_checked=same_round,
                              profiles_before=profiles_before, profiles_after=profiles_after)
                break
            except (OSError, ValueError, KeyError, StopIteration, TimeoutError,
                    subprocess.SubprocessError) as exc:
                latest_error = type(exc).__name__
                time.sleep(min(1, max(0, deadline - time.monotonic())))
        else:
            report["latest_readiness_error_type"] = latest_error
            raise TimeoutError("Public readiness was not confirmed; the same unit retains its restart policy")
    except BaseException as exc:
        if old_pid is not None:
            resume_if_same(old_pid, old_start)
        report["error_type"] = type(exc).__name__
        # Do not include arbitrary exception messages, command output or env.
        if report["restart_requested"]:
            try:
                report["recovery_observation"] = {"active_state": unit_property("ActiveState"),
                    "sub_state": unit_property("SubState"), "restart_policy": unit_property("Restart"),
                    "main_pid": int(unit_property("MainPID"))}
            except (OSError, ValueError, subprocess.SubprocessError):
                report["recovery_observation"] = {"state_unavailable": True}
        raise
    finally:
        progress_done.set()
        if old_pid is not None:
            resume_if_same(old_pid, old_start)
        report["finished_at_utc"] = utc_now()
        save_json(evidence_dir, "restart-report.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--boundary-timeout", type=float, default=180)
    args = parser.parse_args()
    if args.boundary_timeout <= 0:
        parser.error("--boundary-timeout must be positive")
    restart(args.evidence_dir, args.boundary_timeout)
