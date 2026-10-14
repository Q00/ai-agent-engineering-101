"""Cost and routing probe before the full suite: one free-condition lamp episode per effort.

The free condition exercises both wire formats: text for the negotiators and a strict
JSON Schema for the reader. Its rows are not part of the 72-episode comparison.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import run_luna as luna

SUITE = "luna-probe2-20260928"  # luna-probe-20260928 kept its null-temperature 404 rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    configs, scenarios = luna.load_inputs()
    scenarios = [s for s in scenarios if s["id"] == 2]
    tasks = [(e, "free", 1) for e in luna.EFFORTS]
    manifest, csv_path, done = luna.prepare_suite(SUITE, len(tasks), tasks, scenarios, configs)
    key, lock = luna.lab.read_key(args.env_file), threading.Lock()
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = [pool.submit(luna.run_task, SUITE, *task, scenarios=scenarios, config=configs[task[0]], key=key,
                               manifest=manifest, csv_path=csv_path, done=done, lock=lock) for task in tasks]
        for future in futures:
            future.result()
    print(f"Completed probe {SUITE}: {len(done)} episodes", flush=True)


if __name__ == "__main__":
    main()
