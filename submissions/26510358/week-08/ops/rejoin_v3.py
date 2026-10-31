"""Restart the existing market identity with the unchanged official v3 kit

The old buyer is stopped between completed orders, and its raw log is retained
No order, checkout, review or settlement is submitted by this deployment helper
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

import httpx

WEEK08 = Path(__file__).resolve().parents[1]
WEEK07 = WEEK08.parent / "week-07"
REPO = WEEK07.parents[2]
OLD_UNIT = "week07-myeonggi-buyer.service"
NEW_UNIT = "week08-myeonggi-market.service"
MARKET = "https://market.ourolabs.ai"
STAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
EVIDENCE = WEEK08 / "logs" / ("v3-rejoin-" + STAMP)
PRIVATE = WEEK08 / ".market"
KIT_LOG = WEEK07 / "logs" / "market-kit.log"


def save(name, data):
    with (EVIDENCE / name).open("x") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def unit_property(unit, name):
    return subprocess.check_output(
        ["systemctl", "--user", "show", unit, "--property=" + name, "--value"],
        text=True).strip()


def profiles(board):
    return {role: sorted((row["name"], row["joined"]) for row in board[role]
                         if (row.get("owner") or {}).get("login") == "SUNGMYEONGGI")
            for role in ("shops", "buyers")}


def key_hashes():
    return {name: hashlib.sha256((WEEK07 / name).read_bytes()).hexdigest()
            for name in ("shop_key.pem", "buyer_key.pem")}


def log_tail():
    with KIT_LOG.open("rb") as stream:
        stream.seek(max(0, KIT_LOG.stat().st_size - 1_048_576))
        return stream.read().decode("utf-8", errors="replace")


def pause_between_orders(pid, seconds=240):
    deadline = time.monotonic() + seconds
    children = Path(f"/proc/{pid}/task/{pid}/children")
    while time.monotonic() < deadline:
        if children.read_text().split():
            time.sleep(0.1)
            continue
        os.kill(pid, signal.SIGSTOP)
        lines = log_tail().splitlines()
        orders = [i for i, line in enumerate(lines)
                  if re.match(r"^\d{2}:\d{2}:\d{2} \[order ", line)]
        results = [i for i, line in enumerate(lines)
                   if re.match(r"^\d{2}:\d{2}:\d{2} \[market\] ledger #", line)]
        if orders and results and results[-1] > orders[-1] and not children.read_text().split():
            return lines[results[-1]]
        os.kill(pid, signal.SIGCONT)
        time.sleep(0.1)
    raise TimeoutError("No completed-order boundary observed; old buyer remains running")


def main():
    EVIDENCE.mkdir(parents=True)
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    report = {"started_at_utc": datetime.now(timezone.utc).isoformat(),
              "old_unit": OLD_UNIT, "new_unit": NEW_UNIT,
              "new_cli_mode": "join --tunnel", "steps": [2, 3]}
    paused = False
    old_stopped = False
    pid = int(unit_property(OLD_UNIT, "MainPID"))
    assert pid > 0
    raw_environment = dict(row.split(b"=", 1) for row in
                           Path(f"/proc/{pid}/environ").read_bytes().split(b"\0") if b"=" in row)
    environment = {key: raw_environment[key.encode()].decode() for key in
                   ("HOME", "PATH", "CODEX_HOME", "PYTHONUTF8") if key.encode() in raw_environment}
    assert not raw_environment.get(b"OPENAI_API_KEY") and not raw_environment.get(b"CODEX_API_KEY")
    del raw_environment
    environment.update(MARKET_KIT_DIR=str(WEEK08), PYTHONDONTWRITEBYTECODE="1")
    hashes = key_hashes()
    try:
        with httpx.Client(timeout=15, trust_env=False) as client:
            before = client.get(MARKET + "/api/board").raise_for_status().json()
            save("board-before.json", before)
            save("shops-before.json", client.get(MARKET + "/api/shops").raise_for_status().json())
            report["profiles_before"] = profiles(before)
            report["old_pid"] = pid
            print("Waiting for the current buyer order to finish", flush=True)
            report["last_old_result"] = pause_between_orders(pid)
            paused = True
            # systemd sends SIGCONT with termination, so a paused process can exit
            subprocess.run(["systemctl", "--user", "stop", OLD_UNIT], check=True, timeout=20)
            old_stopped = True
            paused = False
            assert not Path(f"/proc/{pid}/cmdline").exists()
            archive = PRIVATE / ("market-kit-before-v3-" + STAMP + ".log")
            os.link(KIT_LOG, archive)
            report["raw_log_preserved"] = {"path": str(archive), "bytes": archive.stat().st_size,
                                           "same_inode_before_rotation": archive.stat().st_ino == KIT_LOG.stat().st_ino}
            command = ["systemd-run", "--user", "--unit=" + NEW_UNIT,
                       "--description=Week 08 myeonggi market kit v3 with ACP",
                       "--property=Restart=always", "--property=RestartSec=10",
                       "--property=TimeoutStopSec=30", "--property=WorkingDirectory=" + str(WEEK07),
                       "--property=StandardOutput=append:" + str(EVIDENCE / "runtime-console.log"),
                       "--property=StandardError=inherit"]
            command += ["--setenv=" + key + "=" + value for key, value in environment.items()]
            command += [str(REPO / ".venv/bin/python"), "-u", str(WEEK07 / "market_runtime.py"),
                        "join", "--dir", str(WEEK07), "--market", MARKET, "--name", "myeonggi", "--tunnel"]
            started = subprocess.run(command, text=True, capture_output=True, check=True)
            (EVIDENCE / "systemd-start.log").write_text(started.stdout + started.stderr)
            report["new_pid"] = int(unit_property(NEW_UNIT, "MainPID"))
            print("Started official kit v3; waiting for registration and public ACP", flush=True)
            deadline = time.monotonic() + 210
            latest_error = "registration not yet observed"
            while time.monotonic() < deadline:
                try:
                    console = (EVIDENCE / "runtime-console.log").read_text(errors="replace")
                    assert "[kit] market_kit 3," in console
                    assert "[market] shop registered as myeonggi" in console
                    assert "[market] buyer registered as myeonggi" in console
                    shops = client.get(MARKET + "/api/shops").raise_for_status().json()
                    shop = next(row for row in shops["shops"] if row["name"] == "myeonggi")
                    public = shop["url"].rstrip("/")
                    acp_response = client.get(public + "/.well-known/acp.json").raise_for_status()
                    acp = acp_response.json()
                    assert acp["protocol"] == "acp" and acp["version"] == "2026-04-17"
                    assert acp["endpoint"] == public + "/acp"
                    assert "checkout" in acp["capabilities"]["services"]
                    assert "ap2-mandate" in acp["capabilities"]["payment_methods"]
                    card = client.get(public + "/.well-known/agent-card.json").raise_for_status().json()
                    assert all(row["url"].startswith(public + "/") for row in card["supportedInterfaces"])
                    board = client.get(MARKET + "/api/board").raise_for_status().json()
                    assert profiles(board) == profiles(before)
                    assert all(next(row for row in board[role] if row["name"] == "myeonggi")["alive"]
                               for role in ("shops", "buyers"))
                    assert key_hashes() == hashes
                    with KIT_LOG.open() as stream:
                        first_line = stream.readline().strip()
                    assert "[kit] market_kit 3," in first_line
                    save("shops-after.json", shops)
                    save("board-after.json", board)
                    save("public-acp.json", acp)
                    save("public-agent-card.json", card)
                    report.update(passed=True, kit_version="3", public_url=public,
                                  acp_url=public + "/.well-known/acp.json", acp_status=acp_response.status_code,
                                  log_first_line=first_line, profiles_unchanged=True, keys_unchanged=True,
                                  round=board["current"], profiles_after=profiles(board),
                                  service_active=unit_property(NEW_UNIT, "ActiveState"))
                    print(json.dumps({key: report[key] for key in ("passed", "kit_version", "public_url", "round")}), flush=True)
                    break
                except (AssertionError, KeyError, StopIteration, ValueError, FileNotFoundError, httpx.HTTPError) as exc:
                    latest_error = type(exc).__name__ + ": " + str(exc)
                    time.sleep(2)
            else:
                raise TimeoutError("v3 verification incomplete: " + latest_error)
    except BaseException as exc:
        report.update(passed=False, error=type(exc).__name__ + ": " + str(exc), old_stopped=old_stopped)
        raise
    finally:
        if paused and Path(f"/proc/{pid}/cmdline").exists():
            os.kill(pid, signal.SIGCONT)
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        save("deployment-result.json", report)
        print("Evidence: " + str(EVIDENCE), flush=True)


if __name__ == "__main__":
    main()
