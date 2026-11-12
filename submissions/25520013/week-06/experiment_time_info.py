"""A/B check for one prompt change: does telling the seller about the buyer's time limit change when it closes?

    uv run python experiment_time_info.py run --label baseline --runs 6
    uv run python experiment_time_info.py run --label time_info --runs 6
    uv run python experiment_time_info.py summary

Each run is the trade test's own seller_deal scenario (its fixed buyer: lamp,
budget 120, offers 60, 80, 100, 110, 120, then 120 again, at most 8 offers after
the opening message) against `seller.py` as it is in the working tree. The
seller's output goes to logs/experiment-time-info/<label>/seller.log.
"""

import argparse
import concurrent.futures
import importlib.util
import json
import pathlib
import statistics
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
KIT_PATH = HERE.parents[2] / "scripts" / "trade_test_week06.py"
OUT = HERE / "logs" / "experiment-time-info"
ARMS = ("baseline", "time_info")


def load_kit():
    spec = importlib.util.spec_from_file_location("kit", KIT_PATH)
    kit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kit)
    return kit


def run(label: str, runs: int, parallel: int) -> None:
    kit = load_kit()
    out = OUT / label
    out.mkdir(parents=True, exist_ok=True)
    port = kit.free_port()
    with open(out / "seller.log", "w") as log:
        seller = subprocess.Popen(
            ["uv", "run", "python", "seller.py", "--port", str(port)],
            cwd=HERE,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            kit.wait_card(base, 60)
            t0 = time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(parallel) as pool:
                results = list(
                    pool.map(lambda _: kit.negotiate(base + "/", kit.DEAL), range(runs))
                )
            seconds = round(time.monotonic() - t0)
        finally:
            seller.terminate()
            seller.wait(10)
    rows = []
    for r in results:
        done = kit.state(r["task"]) == "TASK_STATE_COMPLETED"
        rows.append(
            {
                "state": kit.state(r["task"]),
                "price": kit.receipt_price(r["task"]),
                "asks": r.get("asks"),
                # each ask answers one buyer message; the offer after the last ask closed it
                "offers_to_close": len(r["asks"]) if done else None,
            }
        )
    (out / "runs.json").write_text(
        json.dumps({"label": label, "seconds": seconds, "runs": rows}, indent=1)
    )
    print(
        f"{label}: {runs} runs, {seconds} s, offers to close "
        f"{[x['offers_to_close'] for x in rows]}, prices {[x['price'] for x in rows]}"
    )


def summary() -> None:
    s = {}
    for arm in ARMS:
        rows = json.loads((OUT / arm / "runs.json").read_text())["runs"]
        closed = [
            x["offers_to_close"] for x in rows if x["offers_to_close"] is not None
        ]
        s[arm] = {
            "runs": len(rows),
            "completed": len(closed),
            "offers_to_close": [x["offers_to_close"] for x in rows],
            "median_offers_to_close": statistics.median(closed) if closed else None,
            "closed_on_last_allowed_offer": sum(1 for c in closed if c == 8),
            "prices": [x["price"] for x in rows],
        }
    (OUT / "summary.json").write_text(json.dumps(s, indent=1))
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--label", choices=ARMS, required=True)
    r.add_argument("--runs", type=int, default=6)
    r.add_argument("--parallel", type=int, default=3)
    sub.add_parser("summary")
    args = ap.parse_args()
    if args.cmd == "run":
        run(args.label, args.runs, args.parallel)
    else:
        summary()
    sys.exit(0)
