"""Replay actual model text and verify all CSV/log totals without model calls."""
from collections import Counter
import hashlib
import json

import codex_backend
from contract_net import CONDITIONS, NAMES, ROOT, load_json, parse_bid, system_prompt
from run_experiment import FROZEN_FILES, read_rows


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate():
    tasks, prompts = load_json("tasks.json"), load_json("prompts.json")
    rows = read_rows(ROOT)
    require(len({r["run"] for r in rows}) == len(rows), "duplicate run ID")
    expected_files = {"{}-{}.log".format(r["run"], r["condition"]) for r in rows}
    require(expected_files == {p.name for p in (ROOT / "logs").glob("*.log")}, "log/CSV mismatch")
    successful = Counter()
    all_calls = 0
    reference_config = None
    for row in rows:
        require(row["condition"] in CONDITIONS, "unexpected condition")
        path = ROOT / "logs" / "{}-{}.log".format(row["run"], row["condition"])
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        config = events[0]
        require(config["event"] == "config", "missing first-line config")
        require(config["run"] == row["run"] and config["condition"] == row["condition"], "wrong run")
        for name in FROZEN_FILES:
            require(config["hashes"][name] == hashlib.sha256((ROOT / name).read_bytes()).hexdigest(),
                    "changed frozen file: " + name)
        comparable = {k: config[k] for k in (
            "provider", "model", "cli_version", "authentication", "reasoning_effort",
            "temperature", "max_output_tokens", "timeout_seconds", "response_schema")}
        if reference_config is None:
            reference_config = comparable
        require(comparable == reference_config, "backend settings changed across runs")
        require(events[-1]["event"] == "finished", "missing finished record")
        require({k: str(v) for k, v in events[-1]["row"].items()} == row, "CSV not equal to log")
        note = json.loads(row["note"])
        if note["status"] == "crashed":
            require(all(row[k] == "" for k in ("tasks", "correct", "messages", "unassigned", "misawards")),
                    "crashed counts must be blank")
            require(any(e["event"] == "crash" for e in events), "missing crash evidence")
            print("Retained crashed run {}: {}".format(row["run"], note["error"]))
            continue
        require(note["status"] == "completed", "unexpected status")
        cursor = 1
        counts = dict(tasks=len(tasks), correct=0, messages=0, unassigned=0, misawards=0)
        stats = dict(calls=0, parse_fails=0, declines=0, input_tokens=0, output_tokens=0)

        def take(kind):
            nonlocal cursor
            event = events[cursor]
            cursor += 1
            require(event["event"] == kind, "unexpected event at {}:{}".format(path.name, cursor))
            return event

        for task in tasks:
            announcement = prompts["announcement"].format(id=task["id"], desc=task["desc"])
            for name in NAMES:
                event = take("announcement")
                require((event["task"], event["contractor"], event["text"]) ==
                        (task["id"], name, announcement), "wrong announcement")
                counts["messages"] += 1
            positives = []
            for name in NAMES:
                expected = codex_backend.payload(system_prompt(row["condition"], name, prompts), announcement)
                require(take("model_input")["payload"] == expected, "wrong model input or gold leakage")
                command = take("model_command")["argv"]
                require(command[command.index("--model") + 1] == config["model"], "wrong model flag")
                for flag in ("--ignore-user-config", "--ephemeral", "--skip-git-repo-check", "--json"):
                    require(flag in command, "missing isolation flag")
                require(command[command.index("--sandbox") + 1] == "read-only", "unsafe sandbox")
                require("--output-schema" not in command, "response artificially constrained")
                for feature in codex_backend.DISABLED:
                    require(any(command[i:i+2] == ["--disable", feature] for i in range(len(command)-1)),
                            "native feature not disabled: " + feature)
                output = take("model_output")
                require(output["returncode"] == 0, "nonzero model exit")
                usage, event_text = codex_backend.inspect_events(output["stdout"])
                response = take("model_response")
                require(response["usage"] == usage, "usage differs from raw events")
                require(response["raw"].strip() == event_text.strip(), "raw reply mismatch")
                event = take("bid")
                parsed, error = parse_bid(response["raw"])
                require(event == dict(event="bid", task=task["id"], contractor=name,
                                      raw=response["raw"], parsed=parsed, error=error), "bid replay mismatch")
                stats["calls"] += 1
                for key in ("input_tokens", "output_tokens"):
                    stats[key] += usage[key]
                if parsed is None:
                    stats["parse_fails"] += 1
                elif parsed["bid"]:
                    positives.append((parsed["confidence"], name))
                    counts["messages"] += 1
                else:
                    stats["declines"] += 1
            # Independent stable descending sort, rather than the manager function.
            positives.sort(key=lambda pair: -pair[0])
            winner = positives[0][1] if positives else None
            if winner is None:
                require(take("unassigned")["task"] == task["id"], "wrong unassigned task")
                counts["unassigned"] += 1
                outcome = "unassigned"
            else:
                award = take("award")
                require(award == dict(event="award", task=task["id"], contractor=winner), "wrong winner")
                counts["messages"] += 1
                outcome = "correct" if winner == task["gold"] else "misawards"
                counts[outcome] += 1
            require(take("evaluation") == dict(event="evaluation", task=task["id"],
                    gold=task["gold"], winner=winner, outcome=outcome), "wrong evaluation")
        summary = take("summary")
        require(summary["counts"] == counts and summary["stats"] == stats, "summary mismatch")
        take("finished")
        require(cursor == len(events), "extra events")
        require(all(int(row[k]) == v for k, v in counts.items()), "CSV count mismatch")
        require(all(note[k] == v for k, v in stats.items()), "CSV stats mismatch")
        require(counts["correct"] + counts["misawards"] + counts["unassigned"] == len(tasks), "bad total")
        successful[row["condition"]] += 1
        all_calls += stats["calls"]
        print("{} {} verified: {}".format(row["run"], row["condition"], counts))
    require(all(successful[c] >= 3 for c in CONDITIONS), "need three completed runs per condition")
    print("PASS: {} completed runs, {} live model calls; raw events, prompts, gold-blind awards and CSV match."
          .format(sum(successful.values()), all_calls))


if __name__ == "__main__":
    validate()
