#!/usr/bin/env python3
"""Run the three final checks, preserving fresh evidence without replacing attempts.

Preparing/importing this file does not run models. An explicit CLI invocation does:
official trade test -> both challenges -> structural check. Failed checks remain
failed even if a later check passes. Source/policy changes stop subsequent stages.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
PYTHON = Path("/home/myeonggi/a2a-lab/.venv/bin/python")
REQUIRED = {"seller_card": 1, "seller_opens": 1, "seller_deal": 3,
            "seller_terminal": 1, "seller_holds_reserve": 3, "buyer_deal": 3,
            "buyer_protocol_deal": 3, "buyer_holds_budget": 3,
            "buyer_protocol_holds_budget": 3}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path, *, normalize=False):
    value = Path(path).read_bytes()
    return hashlib.sha256(value.replace(b"\r\n", b"\n") if normalize else value).hexdigest()


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def file_record(path):
    if not path.is_file():
        return {"path": str(path), "exists": False}
    stat = path.stat()
    return {"path": str(path), "exists": True, "sha256": digest(path),
            "mtime_ns": stat.st_mtime_ns, "size": stat.st_size}


def defaults():
    return [ROOT / "test-report.json", ROOT / "logs/trade-test.log",
            ROOT / "logs/seller-under-test.log",
            *(ROOT / "logs" / f"buyer-under-test-{kind}-{run}.log"
              for kind in ("buyer_deal", "buyer_holds_budget") for run in (1, 2, 3))]


def preserve_previous(attempt):
    """Reuse a complete, verified backup; never overwrite an earlier backup."""
    directory = attempt / "previous-defaults"
    manifest = attempt / "preserved-default-artifacts.json"
    if directory.exists() or manifest.exists():
        if not directory.is_dir() or not manifest.is_file():
            raise ValueError("Previous-default backup is incomplete; choose a new attempt")
        records = json.loads(manifest.read_text())
        if not isinstance(records, list):
            raise ValueError("Previous-default manifest must be a list")
        for record in records:
            backup = (ROOT / record["backup"]).resolve()
            if not backup.is_relative_to(directory.resolve()) or not backup.is_file():
                raise ValueError("Previous-default manifest has a missing/outside backup")
            if digest(backup) != record["sha256"]:
                raise ValueError("Previous-default backup hash differs from its manifest")
        saved = {row["source"]: row["sha256"] for row in records}
        for source in defaults():
            if source.is_file() and saved.get(str(source.relative_to(ROOT))) != digest(source):
                raise ValueError("Live defaults changed since this backup; choose a new attempt")
        return {"reused": True, "manifest": str(manifest), "manifest_sha256": digest(manifest)}
    directory.mkdir(parents=True)
    records = []
    for source in defaults():
        if source.is_file():
            target = directory / source.name
            with target.open("xb") as stream:
                stream.write(source.read_bytes())
            shutil.copystat(source, target)
            records.append({"source": str(source.relative_to(ROOT)),
                            "backup": str(target.relative_to(ROOT)), "sha256": digest(target)})
    write_new(manifest, records)
    return {"reused": False, "manifest": str(manifest), "manifest_sha256": digest(manifest)}


def policy_record(path=None):
    from policy import StrategyPolicy
    path = Path(path) if path is not None else ROOT / "policies/active.json"
    loaded = StrategyPolicy(path)
    record = {**file_record(path), "version": loaded.version}
    if record["sha256"] != loaded.sha256:
        raise ValueError("Policy changed while its loader/hash were checked")
    return record


def run_stage(name, command, cwd, env, timeout, paths, stop):
    from benchmark_a2a import _owned_process_snapshot, _same_live_process, _signal_owned
    started_ns, started = time.time_ns(), time.monotonic()
    result = {"stage": name, "command": command, "cwd": str(cwd),
              "started": now(), "started_ns": started_ns, "timeout_seconds": timeout,
              "console": str(paths["console"]), "runtime": str(paths["runtime"]),
              "queue": str(paths["queue"]), "pid": None, "start_new_session": True,
              "timed_out": False, "interrupted": False, "returncode": None}
    paths["runtime"].mkdir(parents=True)
    child_env = {**env, "A2A_TRACE_DIR": str(paths["runtime"]),
                 "A2A_QUEUE_TRACE": str(paths["queue"])}
    child, owned = None, {}
    try:
        with paths["console"].open("x", encoding="utf-8") as console:
            child = subprocess.Popen(command, cwd=cwd, env=child_env,
                stdout=console, stderr=subprocess.STDOUT, start_new_session=True)
            result["pid"] = child.pid
            print(json.dumps({"event": "stage_started", "stage": name,
                              "pid": child.pid, "time": now()}), flush=True)
            try:
                deadline = started + timeout
                while True:
                    # Cache identities while ancestry still exists. A detached
                    # CLI may outlive/reparent after the stage parent exits.
                    owned.update(_owned_process_snapshot(child.pid))
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        result["timed_out"] = True
                        stop(child, grace_seconds=0.5)
                        break
                    try:
                        child.wait(timeout=min(0.25, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            except KeyboardInterrupt:
                result["interrupted"] = True
                stop(child, grace_seconds=0.5)
            result["returncode"] = child.poll()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if child is not None and child.poll() is None:
            owned.update(_owned_process_snapshot(child.pid))
            stop(child, grace_seconds=0.5)
        survivors = {pid: birth for pid, birth in owned.items() if _same_live_process(pid, birth)}
        term_targets = dict(survivors)
        _signal_owned(term_targets, signal.SIGTERM)
        grace = time.monotonic() + 0.5
        while any(_same_live_process(pid, birth) for pid, birth in survivors.items()) and time.monotonic() < grace:
            time.sleep(0.02)
        kill_targets = {pid: birth for pid, birth in survivors.items() if _same_live_process(pid, birth)}
        _signal_owned(kill_targets, signal.SIGKILL)
        reap_deadline = time.monotonic() + 0.5
        while any(_same_live_process(pid, birth) for pid, birth in kill_targets.items()) and time.monotonic() < reap_deadline:
            time.sleep(0.02)
        remaining_owned = [pid for pid, birth in owned.items() if _same_live_process(pid, birth)]
        result["owned_cleanup"] = {"observed_pid_start_ticks": owned,
            "term_pid_start_ticks": term_targets, "kill_pid_start_ticks": kill_targets,
            "remaining_live_owned_pids": remaining_owned, "complete": not remaining_owned,
            "scope": "Only observed descendants with the same /proc starttime identity"}
        result.update(finished=now(), finished_ns=time.time_ns(),
                      elapsed_seconds=round(time.monotonic() - started, 3))
    result["console_evidence"] = file_record(paths["console"])
    result["queue_evidence"] = file_record(paths["queue"])
    return result


def preserve_official(attempt, started_ns, *, report_verified_fresh):
    """Old default files are attested as stale, never copied as new results."""
    records = []
    for source in defaults():
        record = file_record(source)
        record["mtime_fresh"] = record["exists"] and record["mtime_ns"] >= started_ns
        record["fresh"] = record["mtime_fresh"] and (
            source.name != "test-report.json" or report_verified_fresh)
        record["copied_to"] = None
        if record["fresh"]:
            target = attempt / source.name
            with target.open("xb") as stream:
                stream.write(source.read_bytes())
            shutil.copystat(source, target)
            record["copied_to"] = str(target)
        records.append(record)
    return records


def official_checks(stage, kit, expected, runtime):
    report_path = ROOT / "test-report.json"
    fresh = report_path.is_file() and report_path.stat().st_mtime_ns >= stage["started_ns"]
    checks = {"report_fresh": fresh, "process_exit_pass": stage["returncode"] == 0,
              "not_timed_out": not stage["timed_out"], "not_interrupted": not stage["interrupted"]}
    if not fresh:
        return checks
    try:
        report = json.loads(report_path.read_text())
        cfg = json.loads((ROOT / "agent.json").read_text())
        results = report.get("results", [])
        counts = Counter(row.get("test") for row in results)
        checks.update(report_passed=report.get("passed") is True,
            exactly_21=len(results) == 21,
            every_result_passed=bool(results) and all(row.get("pass") is True for row in results),
            required_test_counts=counts == Counter(REQUIRED),
            current_kit=report.get("kit_sha256") == digest(kit, normalize=True),
            current_commands=report.get("seller_cmd") == cfg["seller"]["cmd"]
                and report.get("buyer_cmd") == cfg["buyer"]["cmd"],
            started_fresh=stage["started_ns"] // 1_000_000_000
                <= datetime.fromisoformat(report["started"]).timestamp()
                <= stage["finished_ns"] / 1_000_000_000 + 1)
        loaded = []
        for path in runtime.glob("*.jsonl"):
            for line in path.read_text().splitlines():
                event = json.loads(line)
                if event.get("event") == "policy_loaded":
                    loaded.append(event)
        checks["runtime_policy_matches"] = len(loaded) >= 7 and all(
            row.get("policy_sha256") == expected for row in loaded)
        stage["policy_loaded_events"] = [{k: row.get(k) for k in
            ("role", "run_id", "policy_sha256", "policy_version")} for row in loaded]
        stage["report_evidence"] = file_record(report_path)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        checks["valid_report_and_runtime"] = False
        stage["attestation_error"] = f"{type(exc).__name__}: {exc}"
    return checks


def challenge_checks(stage, expected_policy_sha, expected_policy_version):
    checks = {"process_exit_pass": stage["returncode"] == 0,
              "not_timed_out": not stage["timed_out"], "not_interrupted": not stage["interrupted"]}
    try:
        final = None
        for line in Path(stage["console"]).read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("run_id"), str) and isinstance(row.get("results"), dict):
                final = row
        if final is None or re.fullmatch(r"[A-Za-z0-9_-]+", final["run_id"]) is None:
            raise ValueError("No completed challenge run ID in the actual console")
        checks["both_challenges_present"] = set(final["results"]) == {"challenge_1", "challenge_2"}
        stage["challenge_run_id"], stage["summaries"] = final["run_id"], []
        for number in (1, 2):
            path = ROOT / f"logs/challenge-{number}" / final["run_id"] / "summary.json"
            fresh = path.is_file() and path.stat().st_mtime_ns >= stage["started_ns"]
            checks[f"challenge_{number}_fresh"] = fresh
            summary = json.loads(path.read_text()) if fresh else {}
            checks[f"challenge_{number}_passed"] = summary.get("passed") is True
            checks[f"challenge_{number}_matches_console"] = summary == final["results"].get(f"challenge_{number}")
            if number == 2:
                checks["challenge_2_summary_policy_matches"] = summary.get("policy_sha256") == expected_policy_sha
            loaded = []
            if fresh:
                for trace in path.parent.rglob("*.jsonl"):
                    for line in trace.read_text().splitlines():
                        event = json.loads(line)
                        if event.get("event") == "policy_loaded":
                            loaded.append(event)
            checks[f"challenge_{number}_runtime_policy_matches"] = bool(loaded) and all(
                event.get("policy_sha256") == expected_policy_sha
                and event.get("policy_version") == expected_policy_version for event in loaded)
            stage["summaries"].append(file_record(path))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        checks["completed_summary_evidence"] = False
        stage["attestation_error"] = f"{type(exc).__name__}: {exc}"
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="Unique evidence filename prefix")
    parser.add_argument("--attempt", required=True, help="Attempt name, for example 06")
    parser.add_argument("--expected-policy-sha", required=True)
    parser.add_argument("--policy", type=Path,
                        help="Explicit candidate to evaluate; never promotes or replaces active.json")
    args = parser.parse_args()
    for value in (args.label, args.attempt):
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) is None or value in (".", ".."):
            parser.error("label and attempt must be safe single path components")
    if re.fullmatch(r"[0-9a-f]{64}", args.expected_policy_sha) is None:
        parser.error("expected policy SHA must be 64 lowercase hex digits")
    if not PYTHON.is_file():
        parser.error(f"Required Python is missing: {PYTHON}")
    repo = next((p for p in ROOT.parents if (p / "scripts/trade_test_week06.py").is_file()), None)
    if repo is None:
        parser.error("Cannot infer the repository containing the official scripts")
    sys.path.insert(0, str(ROOT))
    from benchmark_a2a import source_hashes, stop

    def hashes():
        return {key: value for key, value in source_hashes(ROOT).items() if key != "test-report.json"}

    selected_path = args.policy.expanduser().resolve() if args.policy is not None else ROOT / "policies/active.json"
    policy = policy_record(selected_path)
    active_before = policy_record()
    if policy["sha256"] != args.expected_policy_sha:
        parser.error("Selected policy does not match --expected-policy-sha; no checks started")
    attempt = ROOT / "logs/attempts" / args.attempt
    if attempt.is_symlink():
        parser.error("Attempt directory cannot be a symlink")
    plans = [("official", [str(PYTHON), str(repo / "scripts/trade_test_week06.py"), str(ROOT)], repo, 1800),
             ("challenges", [str(PYTHON), str(ROOT / "run_challenges.py"), "--challenge", "all"], ROOT, 1200),
             ("structure", [str(PYTHON), str(repo / "scripts/check_week06.py"), str(ROOT)], repo, 120)]
    paths = {name: {"console": EVIDENCE / f"{args.label}-{name}.console.txt",
                    "runtime": attempt / f"{args.label}-{name}-runtime",
                    "queue": attempt / f"{args.label}-{name}-queue.jsonl",
                    "status": EVIDENCE / f"{args.label}-{name}-status.json"}
             for name, *_ in plans}
    final_path = EVIDENCE / f"{args.label}-final-status.json"
    manifest_path = EVIDENCE / f"{args.label}-validation-manifest.json"
    copied_manifest = attempt / f"{args.label}-official-artifacts.json"
    source_before = attempt / f"{args.label}-source-before.json"
    source_after = attempt / f"{args.label}-source-after.json"
    expected_outputs = [final_path, manifest_path, copied_manifest, source_before, source_after,
                        attempt / "agent.json", *(attempt / source.name for source in defaults()),
                        *(path for group in paths.values() for path in group.values())]
    existing = [str(path) for path in expected_outputs if path.exists() or path.is_symlink()]
    if existing:
        parser.error("Expected output already exists; choose a new label/attempt: " + ", ".join(existing))
    attempt.mkdir(parents=True, exist_ok=True)
    backup = preserve_previous(attempt)
    with (attempt / "agent.json").open("xb") as stream:
        stream.write((ROOT / "agent.json").read_bytes())
    before = hashes()
    script_sha256 = digest(Path(__file__))
    checker_sources = {str(path): digest(path) for path in (
        repo / "scripts/trade_test_week06.py", repo / "scripts/check_week06.py")}
    write_new(source_before, before)
    env = dict(os.environ)
    for key in ("A2A_POLICY_PATH", "A2A_MODEL", "A2A_HARNESS_COMMAND"):
        env.pop(key, None)
    if args.policy is not None:
        env["A2A_POLICY_PATH"] = str(selected_path)
    env.update(PATH=str(PYTHON.parent) + os.pathsep + env.get("PATH", ""),
               A2A_INFERENCE_GAP_SECONDS="5", PYTHONUNBUFFERED="1")
    manifest = {"created": now(), "label": args.label, "attempt": str(attempt),
        "root": str(ROOT), "repo": str(repo), "python": str(PYTHON),
        "script": file_record(Path(__file__)), "policy": policy, "backup": backup,
        "candidate_evaluation": args.policy is not None,
        "active_policy_before": active_before, "automatic_promotion": False,
        "source_before": file_record(source_before),
        "checker_sources": checker_sources,
        "official_kit": {**file_record(repo / "scripts/trade_test_week06.py"),
                         "normalized_sha256": digest(repo / "scripts/trade_test_week06.py", normalize=True)},
        "environment": {"removed": ["A2A_POLICY_PATH", "A2A_MODEL", "A2A_HARNESS_COMMAND"],
                        "explicit_policy_path": str(selected_path) if args.policy is not None else None,
                        "path_prepend": str(PYTHON.parent), "A2A_INFERENCE_GAP_SECONDS": "5"},
        "plans": [{"stage": name, "command": command, "cwd": str(cwd), "timeout": timeout,
                   "paths": {k: str(v) for k, v in paths[name].items()}}
                  for name, command, cwd, timeout in plans]}
    write_new(manifest_path, manifest)
    final = {"started": now(), "label": args.label, "attempt": str(attempt),
             "manifest": str(manifest_path), "policy": policy, "active_policy_before": active_before,
             "candidate_evaluation": args.policy is not None, "automatic_promotion": False,
             "stages": [], "passed": False}
    try:
        for name, command, cwd, timeout in plans:
            current = hashes()
            current_checkers = {path: digest(path) for path in checker_sources}
            if (current != before or policy_record(selected_path)["sha256"] != args.expected_policy_sha
                    or policy_record()["sha256"] != active_before["sha256"]
                    or digest(Path(__file__)) != script_sha256
                    or current_checkers != checker_sources):
                final["stopped_reason"] = "Source, checker or active policy changed; later checks not started"
                break
            stage = run_stage(name, command, cwd, env, timeout, paths[name], stop)
            if name == "official":
                stage["checks"] = official_checks(stage, repo / "scripts/trade_test_week06.py",
                                                    args.expected_policy_sha, paths[name]["runtime"])
                artifacts = preserve_official(attempt, stage["started_ns"], report_verified_fresh=(
                    stage["checks"].get("report_fresh") is True
                    and stage["checks"].get("started_fresh") is True))
                stage["checks"]["default_artifacts_fresh"] = all(record["fresh"] for record in artifacts)
                write_new(copied_manifest, {"started_ns": stage["started_ns"], "artifacts": artifacts,
                    "stale_default_report_is_not_a_new_result": True})
                stage["artifacts_manifest"] = str(copied_manifest)
            elif name == "challenges":
                stage["checks"] = challenge_checks(stage, args.expected_policy_sha, policy["version"])
            else:
                stage["checks"] = {"process_exit_pass": stage["returncode"] == 0,
                    "not_timed_out": not stage["timed_out"], "not_interrupted": not stage["interrupted"]}
            stage["checks"].update(source_unchanged=hashes() == before,
                policy_unchanged=policy_record(selected_path)["sha256"] == args.expected_policy_sha,
                active_policy_unchanged=policy_record()["sha256"] == active_before["sha256"],
                validation_script_unchanged=digest(Path(__file__)) == script_sha256,
                owned_cleanup_complete=stage["owned_cleanup"]["complete"],
                checker_sources_unchanged={path: digest(path) for path in checker_sources} == checker_sources)
            stage["passed"] = all(stage["checks"].values())
            write_new(paths[name]["status"], stage)
            final["stages"].append(stage)
            print(json.dumps({"event": "stage_finished", "stage": name,
                              "passed": stage["passed"], "returncode": stage["returncode"]}), flush=True)
            if stage["interrupted"]:
                final["stopped_reason"] = "Interrupted; owned child processes were stopped"
                break
            if not stage["owned_cleanup"]["complete"]:
                final["stopped_reason"] = "Owned child cleanup incomplete; later stages not started"
                break
    except BaseException as exc:
        final["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            after = hashes()
        except Exception as exc:
            after = {"attestation_error": f"{type(exc).__name__}: {exc}"}
        write_new(source_after, after)
        try:
            policy_after = policy_record(selected_path)
        except Exception as exc:
            policy_after = {"sha256": None, "error": f"{type(exc).__name__}: {exc}"}
        try:
            active_after = policy_record()
        except Exception as exc:
            active_after = {"sha256": None, "error": f"{type(exc).__name__}: {exc}"}
        try:
            checkers_unchanged = {path: digest(path) for path in checker_sources} == checker_sources
        except Exception:
            checkers_unchanged = False
        final.update(finished=now(), source_unchanged=after == before,
                     policy_after=policy_after, source_after=file_record(source_after),
                     active_policy_after=active_after,
                     active_policy_unchanged=active_after["sha256"] == active_before["sha256"],
                     validation_script_unchanged=digest(Path(__file__)) == script_sha256,
                     checker_sources_unchanged=checkers_unchanged)
        final["passed"] = (len(final["stages"]) == len(plans)
            and all(stage["passed"] for stage in final["stages"])
            and final["source_unchanged"] and final["policy_after"]["sha256"] == args.expected_policy_sha
            and final["active_policy_unchanged"]
            and final["validation_script_unchanged"]
            and final["checker_sources_unchanged"]
            and "error" not in final)
        write_new(final_path, final)
        print(json.dumps({"event": "validation_finished", "passed": final["passed"],
                          "status": str(final_path)}, ensure_ascii=False), flush=True)
    return 0 if final["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
