"""Run 4 fixed scenarios x 3 formats x 3 repeats; resume by (run, scenario).

From the repository root:
  python submissions/26510358/week-04/run.py --env-file /private/path/.env
"""

import argparse
import csv
import hashlib
import json
import platform
from importlib.metadata import version
from pathlib import Path

from negotiation import CONDITIONS, MAX_TURNS, episode
from model import Chat, Settings


ROOT = Path(__file__).resolve().parent
HEADER = ["run", "condition", "scenario", "deal_possible", "outcome", "price",
          "correct", "violation", "turns", "format_errors", "reader_calls", "note"]


def existing_rows(path):
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != HEADER:
            raise ValueError("Existing results.csv has an unexpected header")
        return {(int(row["run"]), row["scenario"]) for row in reader}


def run_all(settings, selected=None):
    scenario_bytes = (ROOT / "scenarios.json").read_bytes()
    scenarios = json.loads(scenario_bytes)
    if len({str(s["id"]) for s in scenarios}) != len(scenarios):
        raise ValueError("Scenario ids must be unique")
    completed = existing_rows(ROOT / "results.csv")
    (ROOT / "logs").mkdir(exist_ok=True)
    chat = Chat(settings)
    csv_path = ROOT / "results.csv"
    with csv_path.open("a", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        if csv_file.tell() == 0:
            writer.writerow(HEADER)
            csv_file.flush()
        for condition_index, condition in enumerate(CONDITIONS):
            if selected and condition not in selected:
                continue
            for repeat in range(1, 4):
                run = condition_index * 3 + repeat
                path = ROOT / "logs" / f"{condition}-{run:02d}.txt"
                if all((run, str(s["id"])) in completed for s in scenarios):
                    print(f"[skip] run={run} condition={condition} already complete")
                    continue
                with path.open("a", encoding="utf-8") as logfile:
                    def log(line):
                        print(line, flush=True)
                        logfile.write(line + "\n")
                        logfile.flush()

                    log(f"[run] number={run} condition={condition} repeat={repeat} "
                        f"provider={settings.provider} model={settings.model} "
                        f"temperature={settings.temperature} max_tokens={settings.max_tokens} "
                        f"reasoning_effort={settings.reasoning_effort} turn_limit={MAX_TURNS} "
                        f"python={platform.python_version()} openai={version('openai')} "
                        f"scenarios_sha256={hashlib.sha256(scenario_bytes).hexdigest()}")
                    for scenario in scenarios:
                        key = (run, str(scenario["id"]))
                        if key in completed:
                            log(f"[skip] scenario={scenario['id']} already recorded")
                            continue
                        log(f"[scenario] id={scenario['id']} item={scenario['item']} "
                            f"reserve={scenario['reserve']} budget={scenario['budget']}")
                        start_calls = chat.calls
                        start_input = chat.input_tokens
                        start_output = chat.output_tokens
                        try:
                            result = episode(scenario, condition, chat, log)
                        except Exception as exc:
                            note = f"crash: {type(exc).__name__}: {exc}"
                            log(f"[crash] {note}")
                            writer.writerow([run, condition, scenario["id"], "", "", "", "",
                                             "", "", "", "", note])
                        else:
                            note = (f"model_calls={chat.calls - start_calls};"
                                    f"input_tokens={chat.input_tokens - start_input};"
                                    f"output_tokens={chat.output_tokens - start_output}")
                            writer.writerow([run, condition, scenario["id"],
                                             result["deal_possible"], result["outcome"],
                                             result["price"] if result["price"] is not None else "",
                                             result["correct"], result["violation"], result["turns"],
                                             result["format_errors"], result["reader_calls"], note])
                        csv_file.flush()
                        completed.add(key)
                print(f"[saved] {path}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="optional private dotenv file")
    parser.add_argument("--condition", choices=CONDITIONS, help="run one condition only")
    parser.add_argument("--check", action="store_true", help="check local setup without an API call")
    args = parser.parse_args()
    if args.env_file:
        from dotenv import load_dotenv
        if not args.env_file.is_file():
            parser.error("--env-file does not exist")
        load_dotenv(args.env_file, override=False)
    settings = Settings.from_env()
    if args.check:
        print(f"ready: {settings.provider} / {settings.model} / "
              f"temperature={settings.temperature}; no API call")
        return
    run_all(settings, {args.condition} if args.condition else None)


if __name__ == "__main__":
    main()
