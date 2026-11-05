"""Inventory of logs/ and a rule-chosen sample of it, written to logs/LOG-MANIFEST.json. Run by hand; no model call, not a hook.

Counts bytes and times per folder and hashes the whole file list, so what stays out of git can still be described and checked.
Samples are ranked by a seeded hash of the file name inside each kind and time window, so nobody picks the good ones and failures get the same share.
Runs at background priority with pauses so the live kit is not slowed. Files stay where they are: --stage-list only writes paths for git add.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parent
MANIFEST = "logs/LOG-MANIFEST.json"
SEED = "week08-log-sample-v1"
PER_WINDOW = 5
ROUND_START = datetime(2026, 11, 3, 12, 4, 0, tzinfo=timezone.utc)  # "shop registered as kcwarni, round 3" at 21:04:00 KST in market-kit.log.1, line 120426
WINDOW = timedelta(hours=6)
PAUSE_EVERY, PAUSE_SECONDS = 2000, 0.002
BUYER_KINDS = ("rule_path_success", "model_path_success", "seller_rejected", "walked_away", "receipt_mismatch", "failed_attempt")
HELD = {"pricing": "Each record holds the unit's cost, floor and quote. Selected by the same rule but not staged while the round runs; the user decides."}
RULES = {
    "seed": SEED,
    "ranking": "sha256(seed + '|' + file name) ascending inside each kind and window; content other than the kind is never looked at",
    "per_window": PER_WINDOW,
    "windows": {"early": "first 6 hours from the round 3 registration (2026-11-03T12:04:00Z)", "recent": "6 hours before the cutoff"},
    "shortfall": "a kind with nothing in a window takes the next-ranked files from the whole round, marked window=whole_round",
    "kinds": {
        "rule_path_success": "market-buyer record, outcome purchased, no decision_trail step with source model",
        "model_path_success": "market-buyer record, outcome purchased, a decision_trail step with source model",
        "seller_rejected": "market-buyer record, negotiation_outcome seller_rejected",
        "walked_away": "market-buyer record, negotiation_outcome walked_away",
        "receipt_mismatch": "market-buyer record, outcome receipt_mismatch",
        "failed_attempt": "market-buyer record, outcome agent_error or transport_error",
        "seller_decision": "logs/seller record of any phase and status",
        "harness_codex": "logs/harness run folder from codex, whatever its result",
        "harness_agy": "logs/harness run folder from agy, whatever its result",
    },
    "linked": "each market-buyer sample also brings its buyer_log_dir folder",
}


def background():
    """Lower CPU and disk priority on Windows; elsewhere it does nothing."""
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00100000)
    except (ImportError, AttributeError, OSError):
        pass


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat() if seconds is not None else None


def inventory(logs, pause_every=PAUSE_EVERY, pause=PAUSE_SECONDS):
    """Per top-level entry of logs/: files, bytes, oldest and newest modified time, and a sha256 of its sorted (path, size) list."""
    logs = Path(logs)
    folders, seen = {}, 0
    for entry in sorted(os.scandir(logs), key=lambda e: e.name):
        rows, first, last = [], None, None
        stack = [entry]
        while stack:
            item = stack.pop()
            if item.is_dir(follow_symlinks=False):
                stack.extend(os.scandir(item.path))
                continue
            stat = item.stat(follow_symlinks=False)
            rows.append((Path(item.path).relative_to(logs).as_posix(), stat.st_size))
            first = stat.st_mtime if first is None else min(first, stat.st_mtime)
            last = stat.st_mtime if last is None else max(last, stat.st_mtime)
            seen += 1
            if pause and seen % pause_every == 0:
                time.sleep(pause)
        rows.sort()
        digest = hashlib.sha256("".join(f"{path}\t{size}\n" for path, size in rows).encode("utf-8"))
        folders[entry.name if entry.is_dir() else "(top-level files)/" + entry.name] = {"files": len(rows), "bytes": sum(size for _, size in rows), "first": iso(first), "last": iso(last),
                                                                                       "list_sha256": digest.hexdigest()}
    return folders


def merged_top_files(folders):
    """Loose files at the top of logs/ as one row, so the folder table stays readable."""
    result, loose = {}, []
    for name, row in folders.items():
        (loose.append(row) if name.startswith("(top-level files)/") else result.__setitem__(name, row))
    if loose:
        result["(top-level files)"] = {"files": sum(r["files"] for r in loose), "bytes": sum(r["bytes"] for r in loose), "first": min(r["first"] for r in loose), "last": max(r["last"] for r in loose),
                                       "list_sha256": hashlib.sha256("".join(sorted(r["list_sha256"] for r in loose)).encode()).hexdigest()}
    return result


def overall_list_sha256(folders):
    return hashlib.sha256("".join(f"{name}\t{row['list_sha256']}\n" for name, row in sorted(folders.items())).encode("utf-8")).hexdigest()


def rank(name, seed=SEED):
    return hashlib.sha256(f"{seed}|{name}".encode("utf-8")).hexdigest()


def classify_buyer(record):
    """Kinds of one market-buyer record, or [] when the sample does not cover it."""
    outcome, negotiation = record.get("outcome"), record.get("negotiation_outcome")
    kinds = []
    if outcome == "purchased":
        used_model = any(isinstance(step, dict) and step.get("source") == "model" for step in record.get("decision_trail") or [])
        kinds.append("model_path_success" if used_model else "rule_path_success")
    if negotiation == "seller_rejected":
        kinds.append("seller_rejected")
    if negotiation == "walked_away":
        kinds.append("walked_away")
    if outcome == "receipt_mismatch":
        kinds.append("receipt_mismatch")
    if outcome in ("agent_error", "transport_error"):
        kinds.append("failed_attempt")
    return kinds


def stamp_of(name):
    """The UTC time at the start of a log name, or None for names without one."""
    try:
        return datetime.strptime(name[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def window_of(moment, cutoff, start=ROUND_START):
    if moment is None or moment < start or moment > cutoff:
        return None
    if moment < start + WINDOW:
        return "early"
    return "recent" if moment > cutoff - WINDOW else "middle"


def pick(candidates, per_window=PER_WINDOW, seed=SEED):
    """candidates: {kind: [(name, window)]}. Returns [(kind, window, name)], ranked by the seeded hash alone."""
    chosen = []
    for kind, rows in candidates.items():
        taken = set()
        for window in ("early", "recent"):
            inside = sorted((name for name, where in rows if where == window), key=lambda n: rank(n, seed))[:per_window]
            if inside:
                chosen += [(kind, window, name) for name in inside]
                taken.update(inside)
            else:
                rest = sorted((name for name, _ in rows if name not in taken), key=lambda n: rank(n, seed))[:per_window]
                chosen += [(kind, "whole_round", name) for name in rest]
                taken.update(rest)
    return chosen


def collect(root, cutoff, pause_every=500, pause=PAUSE_SECONDS):
    """Candidates and what links each market-buyer record to its buyer folder."""
    root = Path(root)
    candidates = {kind: [] for kind in RULES["kinds"]}
    links = {}
    folder = root / "logs" / "market-buyer"
    names = sorted(n for n in os.listdir(folder) if stamp_of(n) and ROUND_START <= stamp_of(n) <= cutoff) if folder.is_dir() else []
    for index, name in enumerate(names, 1):
        try:
            record = json.loads((folder / name).read_text(encoding="utf-8")).get("record") or {}
        except (OSError, ValueError, AttributeError):
            continue
        where = window_of(stamp_of(name), cutoff)
        for kind in classify_buyer(record):
            candidates[kind].append((name, where))
        if record.get("buyer_log_dir"):
            links[name] = Path(str(record["buyer_log_dir"]).replace("\\", "/")).name
        if pause and index % pause_every == 0:
            time.sleep(pause)
    held = {}
    for label, sub, place in (("seller_decision", "seller", candidates), ("pricing", "pricing", held)):
        folder = root / "logs" / sub
        place.setdefault(label, [])
        for entry in (os.scandir(folder) if folder.is_dir() else ()):
            if label == "pricing":
                moment = datetime.fromtimestamp(entry.stat().st_mtime, timezone.utc)
            else:
                moment = stamp_of(entry.name)
            where = window_of(moment, cutoff)
            if where:
                place[label].append((entry.name, where))
    folder = root / "logs" / "harness"
    for name in sorted(os.listdir(folder)) if folder.is_dir() else ():
        where = window_of(stamp_of(name), cutoff)
        if where:
            provider = name.rsplit("-", 1)[-1]
            if f"harness_{provider}" in candidates:
                candidates[f"harness_{provider}"].append((name, where))
    return candidates, links, held


def sample_paths(root, chosen, links):
    """Selected rows with the files each brings, repository-relative to week-08."""
    root = Path(root)
    rows, seen = [], set()
    for kind, window, name in chosen:
        if kind in BUYER_KINDS:
            paths = [root / "logs" / "market-buyer" / name]
            linked = links.get(name)
            if linked and (root / "logs" / "buyer" / linked).is_dir():
                paths += sorted(p for p in (root / "logs" / "buyer" / linked).rglob("*") if p.is_file())
        elif kind == "seller_decision":
            paths = [root / "logs" / "seller" / name]
        else:
            paths = sorted(p for p in (root / "logs" / "harness" / name).rglob("*") if p.is_file())
        files = [p.relative_to(root).as_posix() for p in paths]
        rows.append({"kind": kind, "window": window, "name": name, "files": len(files), "bytes": sum(p.stat().st_size for p in paths)})
        seen.update(files)
    return rows, sorted(seen)


def tracked_counts(root):
    listing = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files", "logs"], cwd=root, capture_output=True, text=True, encoding="utf-8", check=True).stdout.splitlines()
    return Counter(path.split("/")[1] if path.count("/") > 1 else "(top-level files)" for path in listing)


def write_atomic(path, text):
    temp = Path(str(path) + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temp, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff", default=None, help="UTC end of the sample range, YYYYmmddTHHMMSS; rerun with the same value to get the same picks")
    parser.add_argument("--stage-list", help="write the selected file paths here, one per line, for git add")
    args = parser.parse_args()
    background()
    started = datetime.now(timezone.utc)
    cutoff = stamp_of(args.cutoff) if args.cutoff else started.replace(microsecond=0)
    folders = merged_top_files(inventory(ROOT / "logs"))
    candidates, links, held = collect(ROOT, cutoff)
    chosen = pick(candidates)
    rows, paths = sample_paths(ROOT, chosen, links)
    held_picks = pick(held)
    tracked = tracked_counts(ROOT)
    for name, row in folders.items():
        row["tracked_files"] = tracked.get(name, 0)
    sizes = sum((ROOT / p).stat().st_size for p in paths)
    manifest = {
        "note": "Inventory of logs/. Most raw logs are not in git because of their count and size; this table and the list hashes describe them. Hashes cover (path, size), not content, and the logs keep growing during the round.",
        "generated": started.isoformat(), "cutoff": cutoff.isoformat(), "files": sum(r["files"] for r in folders.values()), "bytes": sum(r["bytes"] for r in folders.values()),
        "file_list_sha256": overall_list_sha256(folders), "folders": folders,
        "sample": {"rules": RULES, "available": {kind: len(found) for kind, found in candidates.items()}, "selected": rows, "files": len(paths), "bytes": sizes},
        "held_out": {"reason": HELD, "selected_not_staged": [{"kind": k, "window": w, "name": n} for k, w, n in held_picks], "available": {k: len(v) for k, v in held.items()}},
    }
    write_atomic(ROOT / MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    if args.stage_list:
        Path(args.stage_list).write_text("\n".join(paths) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"manifest": MANIFEST, "files": manifest["files"], "bytes": manifest["bytes"], "available": manifest["sample"]["available"], "sample_files": len(paths), "sample_bytes": sizes,
                      "seconds": round((datetime.now(timezone.utc) - started).total_seconds())}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
