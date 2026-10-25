"""STEP 6 buyer transport exercise against the official rule-based shop; no model calls."""

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import uvicorn


def load_kit(root):
    spec = importlib.util.spec_from_file_location("week06_transport_kit", root.parents[2] / "scripts/trade_test_week06.py")
    kit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kit)
    return kit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="Run the rule-based shop for manual practice")
    parser.add_argument("--port", type=int, default=11099)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    kit = load_kit(root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    evidence = root / "logs/step6" / (stamp + ("-serve" if args.serve else "-transport"))
    evidence.mkdir(parents=True)
    record = {"messages": 0, "continued": 0, "offers": [], "deals": [], "canceled": 0,
              "posts": 0, "bad_version": 0}
    if args.serve:
        try:
            uvicorn.run(kit.kit_seller_app(args.port, record), host="127.0.0.1", port=args.port)
        finally:
            (evidence / "shop-record.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        return

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(kit.kit_seller_app(port, record), host="127.0.0.1",
                                           port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    report = {"scope": "manual buyer transport only, no harness decisions", "passed": False, "cases": []}
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("Rule shop did not start")
            time.sleep(0.05)

        def run_case(name, item, budget, offers, cancel=False, expected_exit=0):
            command = [sys.executable, str(root / "buyer.py"), "--manual", "--url",
                       f"http://127.0.0.1:{port}", "--item", item, "--budget", str(budget)]
            for offer in offers:
                command += ["--offer", str(offer)]
            if cancel:
                command += ["--cancel"]
            with (evidence / f"{name}.stdout.txt").open("wb") as stdout, (evidence / f"{name}.stderr.txt").open("wb") as stderr:
                process = subprocess.run(command, cwd=root, stdout=stdout, stderr=stderr, timeout=30)
            tasks = [json.loads(line) for line in (evidence / f"{name}.stdout.txt").read_text(encoding="utf-8").splitlines()]
            assert process.returncode == expected_exit, name
            assert tasks, name
            assert len({task["id"] for task in tasks}) == 1, name
            entry = {"case": name, "returncode": process.returncode, "task_id": tasks[0]["id"],
                     "states": [task["status"]["state"] for task in tasks]}
            report["cases"].append(entry)
            print(json.dumps(entry), flush=True)
            return tasks[-1]

        completed = run_case("lamp-completed", "lamp", 120, [80, 110, 115])
        assert completed["status"]["state"] == "TASK_STATE_COMPLETED"
        assert record["offers"] == [80, 110], record
        assert record["deals"] == [110], record
        before = list(record["offers"])
        run_case("over-budget-blocked", "lamp", 120, [121], expected_exit=1)
        assert record["offers"] == before, record
        canceled = run_case("bookshelf-canceled", "bookshelf", 230, [230], cancel=True)
        assert canceled["status"]["state"] == "TASK_STATE_CANCELED"
        rejected = run_case("chair-rejected", "chair", 120, [100] * 5)
        assert rejected["status"]["state"] == "TASK_STATE_REJECTED"
        assert record["deals"] == [110], record
        assert record["canceled"] == 1 and record["continued"] == 8, record
        assert record["bad_version"] == 0, record
        report["passed"] = True
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        report["shop_record"] = record
        (evidence / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"STEP 6 evidence: {evidence}", flush=True)


if __name__ == "__main__":
    main()
