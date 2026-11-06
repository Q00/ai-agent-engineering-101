"""No turn ceiling; preserve the HTML protocol and distinguish observation censoring."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "lab"))
import experiment as lab

HEADER = lab.HEADER + ["status", "elapsed_seconds"]


class ObservationEnded(Exception):
    pass


def negotiate(scenario, condition, call, emit, result, seconds, clock=time.monotonic):
    """Same protocol as lab.negotiate; only the stopping policy differs."""
    history = {"buyer": [], "seller": []}
    transcript, last_price = [], {"buyer": None, "seller": None}
    role, other = "buyer", "seller"
    started = clock()
    result.update(outcome="", price="", correct="", violation="", turns=0,
                  format_errors=0, reader_calls=0, status="running", note="")
    turn = 0
    try:
        while True:
            if clock() - started >= seconds:
                raise ObservationEnded("Observation time elapsed before the next turn")
            turn += 1
            # Finish a started turn, including its reader, before observing the deadline.
            text = call(role, [{"role": "system", "content": lab.system_prompt(role, scenario, condition)}] + history[role],
                        lab.schema_format(nested=True) if condition == "structured" else lab.TEXT_FORMAT)
            history[role].append({"role": "assistant", "content": text})
            history[other].append({"role": "user", "content": text})
            transcript.append({"speaker": role, "text": text})
            result["turns"] = turn
            emit("message", turn=turn, speaker=role, text=text)
            try:
                act, price = lab.read_message(condition, text, transcript, call, emit, result)
                if act == "accept-proposal" and last_price[other] is None:
                    raise ValueError("Acceptance without a recorded proposal from the other party")
            except (ValueError, TypeError) as exc:
                result["format_errors"] += 1
                emit("parse_result", turn=turn, ok=False, error=str(exc))
            else:
                emit("parse_result", turn=turn, ok=True, performative=act, price=price)
                if act == "propose":
                    last_price[role] = price
                elif act == "accept-proposal":
                    result.update(outcome="deal", price=last_price[other])
                    break
                elif act == "refuse":
                    result["outcome"] = "no_deal"
                    break
            role, other = other, role
    except ObservationEnded as exc:
        result.update(status="censored", note=str(exc))
        emit("observation_censored", turns=result["turns"], reason=str(exc))
    else:
        result["status"] = "completed"
        result["violation"] = int(result["outcome"] == "deal" and not scenario["reserve"] <= result["price"] <= scenario["budget"])
        result["correct"] = int((result["outcome"] == "deal" and not result["violation"])
                                or (result["outcome"] == "no_deal" and not result["deal_possible"]))
    finally:
        result["elapsed_seconds"] = round(clock() - started, 3)
    return result


def completed_keys(path):
    if not path.exists():
        return set()
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != HEADER:
            raise ValueError("Unexpected extended CSV header")
        rows = list(reader)
    keys = {(r["run"], r["scenario"]) for r in rows}
    if len(keys) != len(rows):
        raise ValueError("Duplicate episode identity")
    return keys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--jobs", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--observation-seconds", type=int, default=180)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.suite) or args.observation_seconds < 1:
        parser.error("Need a simple suite identifier and positive observation duration")
    key = lab.read_key(args.env_file)
    config = json.loads((ROOT / "lab/config.json").read_text())
    config["max_http_requests"] = None
    scenarios = json.loads((ROOT / "scenarios.json").read_text())
    sources = [Path(__file__), ROOT / "lab/experiment.py", ROOT / "lab/config.json",
               ROOT / "scenarios.json", ROOT / "pilot/transport.py"]
    manifest = {"suite": args.suite, "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "inputs": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
                "config": config, "scenarios": scenarios, "max_turns": None, "repeats": 3, "jobs": args.jobs,
                "observation_seconds": args.observation_seconds, "deadline_checked": "before next speaker turn; finish in-flight turn",
                "format": lab.FORMAT, "roles": lab.ROLE, "common": lab.COMMON, "reader_system": lab.READER_SYSTEM}
    out = HERE / "runs" / args.suite
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        if any(old[k] != manifest[k] for k in manifest if k != "source_commit"):
            raise ValueError("Resume must use identical source and settings")
        manifest = old
    else:
        with manifest_path.open("x") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
    csv_path = out / "results.csv"
    done = completed_keys(csv_path)
    if not csv_path.exists():
        with csv_path.open("x", newline="") as f:
            csv.writer(f, lineterminator="\n").writerow(HEADER)
    lock = threading.Lock()
    def run_condition(condition, repeat):
        run = f"{args.suite}-{condition}-{repeat:02d}"
        todo = [s for s in scenarios if (run, str(s["id"])) not in done]
        if not todo:
            return
        # The unchanged transport compares a numeric budget; infinity disables that guard.
        # It is internal only: the persisted config uses null and the API payload omits it.
        client = lab.OpenRouterClient(key, {**config, "max_http_requests": math.inf})
        with (ROOT / "logs" / f"{run}.jsonl").open("a") as events, (ROOT / "logs" / f"{run}.txt").open("a") as console:
            active_scenario = None
            def emit(event, **fields):
                record = {"time": datetime.now(timezone.utc).isoformat(), "run": run,
                          "scenario": active_scenario, "event": event, **fields}
                line = lab.redact(json.dumps(record, ensure_ascii=False, allow_nan=False), key)
                events.write(line + "\n"); events.flush()
                if event not in ("request", "response", "usage"):
                    console.write(line + "\n"); console.flush()
                if event in ("episode_result", "crash", "observation_censored", "http_error"):
                    with lock:
                        print(line, flush=True)
            emit("setup", manifest=manifest, condition=condition, repeat=repeat)
            for scenario in todo:
                active_scenario = scenario["id"]
                row = dict.fromkeys(HEADER, "")
                row.update(run=run, condition=condition, scenario=scenario["id"],
                           deal_possible=int(scenario["reserve"] <= scenario["budget"]))
                emit("episode_start", systems={r: lab.system_prompt(r, scenario, condition) for r in ("buyer", "seller")})
                def call(role, messages, response_format):
                    return client.complete(messages, emit, scenario["id"], role, response_format=response_format)
                try:
                    negotiate(scenario, condition, call, emit, row, args.observation_seconds)
                except Exception as exc:
                    row.update(outcome="", price="", correct="", violation="", status="crashed",
                               note=f"crashed: {type(exc).__name__}: {lab.redact(str(exc), key)}")
                    emit("crash", error=row["note"])
                emit("episode_result", result=row)
                with lock:
                    with csv_path.open("a", newline="") as f:
                        csv.DictWriter(f, fieldnames=HEADER, lineterminator="\n").writerow(row)
                    done.add((run, str(scenario["id"])))
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_condition, c, r) for r in range(1, 4) for c in lab.CONDITIONS]
        for future in futures:
            future.result()
    print(f"Completed observation suite {args.suite}: {len(done)} episodes", flush=True)


if __name__ == "__main__":
    main()
