"""Additional Week 04 studies, kept separate from the original 36 episodes."""

import argparse
import csv
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from model import Chat, Settings  # noqa: E402
from negotiation import MAX_TURNS, episode  # noqa: E402
from run import HEADER  # noqa: E402

ORDERS = {
    "replication": (
        ("structured", "tagged", "free"),
        ("tagged", "free", "structured"),
        ("free", "structured", "tagged"),
    ),
    "termination": (
        ("tagged", "structured", "free"),
        ("free", "tagged", "structured"),
        ("structured", "free", "tagged"),
    ),
}
TERMINATION_POLICY = (
    " In this follow-up, use a deadline policy. Each side has at most four "
    "messages. If the other party's latest offered price is within your private "
    "limit, choose accept-proposal rather than another counter-offer. On your "
    "fourth message, if no offer you can accept has arrived, use refuse and leave; "
    "do not make a new offer or reject on that final turn. Never reveal either "
    "party's private limit."
)


def existing(path):
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != HEADER:
            raise ValueError(f"Unexpected CSV header: {path}")
        return {(int(row["run"]), row["scenario"]) for row in reader}


def run_study(study, settings):
    data = (BASE / "scenarios.json").read_bytes()
    scenarios = json.loads(data)
    assert len(scenarios) == len({str(s["id"]) for s in scenarios})
    csv_path = HERE / f"{study}.csv"
    completed = existing(csv_path)
    (BASE / "logs").mkdir(exist_ok=True)
    client = Chat(settings)
    policy = TERMINATION_POLICY if study == "termination" else ""
    study_index = list(ORDERS).index(study)
    with csv_path.open("a", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file, lineterminator="\n")
        if csv_file.tell() == 0:
            writer.writerow(HEADER)
            csv_file.flush()
        for block, conditions in enumerate(ORDERS[study], 1):
            for slot, condition in enumerate(conditions):
                run = 10 + study_index * 9 + (block - 1) * 3 + slot
                path = BASE / "logs" / f"{study}-{run:02d}.txt"
                if all((run, str(s["id"])) in completed for s in scenarios):
                    print(f"[skip] study={study} run={run} complete", flush=True)
                    continue
                with path.open("a", encoding="utf-8") as logfile:
                    def log(line):
                        print(line, flush=True)
                        logfile.write(line + "\n")
                        logfile.flush()

                    log(f"[run] study={study} number={run} block={block} slot={slot + 1} "
                        f"order={','.join(conditions)} condition={condition} "
                        f"provider={settings.provider} model={settings.model} "
                        f"temperature={settings.temperature} max_tokens={settings.max_tokens} "
                        f"reasoning_effort={settings.reasoning_effort} turn_limit={MAX_TURNS} "
                        f"python={platform.python_version()} openai={version('openai')} "
                        f"utc={datetime.now(timezone.utc).isoformat()} "
                        f"scenarios_sha256={hashlib.sha256(data).hexdigest()} "
                        f"policy_sha256={hashlib.sha256(policy.encode()).hexdigest()}")
                    for scenario in scenarios:
                        key = (run, str(scenario["id"]))
                        if key in completed:
                            log(f"[skip] scenario={scenario['id']} already recorded")
                            continue
                        log(f"[scenario] id={scenario['id']} item={scenario['item']} "
                            f"reserve={scenario['reserve']} budget={scenario['budget']}")
                        before = (client.calls, client.input_tokens, client.output_tokens)
                        try:
                            result = episode(scenario, condition, client, log,
                                             shared_policy=policy)
                        except Exception as exc:
                            note = f"crash: {type(exc).__name__}: {exc}"
                            log(f"[crash] {note}")
                            writer.writerow([run, condition, scenario["id"], "", "", "", "",
                                             "", "", "", "", note])
                        else:
                            note = (f"model_calls={client.calls - before[0]};"
                                    f"input_tokens={client.input_tokens - before[1]};"
                                    f"output_tokens={client.output_tokens - before[2]}")
                            writer.writerow([run, condition, scenario["id"],
                                             result["deal_possible"], result["outcome"],
                                             result["price"] if result["price"] is not None else "",
                                             result["correct"], result["violation"],
                                             result["turns"], result["format_errors"],
                                             result["reader_calls"], note])
                        csv_file.flush()
                        completed.add(key)
                print(f"[saved] {path}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", choices=tuple(ORDERS), required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.env_file:
        from dotenv import load_dotenv
        if not args.env_file.is_file():
            parser.error("--env-file does not exist")
        load_dotenv(args.env_file, override=False)
    settings = Settings.from_env()
    if args.check:
        print(f"ready: study={args.study} model={settings.model} "
              f"temperature={settings.temperature}; no API call")
        return
    run_study(args.study, settings)


if __name__ == "__main__":
    main()
