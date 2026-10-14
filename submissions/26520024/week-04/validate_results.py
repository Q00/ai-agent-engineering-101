"""Offline replay of real model events; never calls a model or edits evidence."""
from collections import Counter
import hashlib
import json
from pathlib import Path

import codex_backend as backend
from negotiation import CONDITIONS, MAX_TURNS, ROOT, load_json, negotiate
from run_experiment import FROZEN, read_rows


def require(condition, message):
    if not condition:
        raise ValueError(message)


class Replay:
    def __init__(self, events, scenario):
        self.events, self.scenario, self.cursor = events, scenario, 0
        self.stats = dict(actor_calls=0, reader_calls=0, retries=0, input_tokens=0, output_tokens=0)

    def take(self, kind):
        require(self.cursor < len(self.events), "missing event: " + kind)
        event = self.events[self.cursor]
        self.cursor += 1
        require(event["event"] == kind, "expected {}, got {}".format(kind, event["event"]))
        require(event.get("scenario") == self.scenario, "wrong scenario on event")
        return event

    def emit(self, kind, **data):
        expected = dict(event=kind, scenario=self.scenario, **data)
        require(self.take(kind) == expected, "replayed {} differs from log".format(kind))

    def __call__(self, system, history, purpose):
        for attempt in range(len(backend.RETRY_DELAYS) + 1):
            self.stats["reader_calls" if purpose == "reader" else "actor_calls"] += 1
            call = self.stats["reader_calls"] + self.stats["actor_calls"]
            start = self.take("call_start")
            require(start == dict(event="call_start", scenario=self.scenario, call=call,
                                  purpose=purpose, attempt=attempt + 1), "wrong model call order")
            request = self.take("model_input")
            require(request["payload"] == backend.payload(system, history),
                    "model input differs: possible private-limit/history leakage")
            command = self.take("model_command")
            argv = command["argv"]
            directory = argv[argv.index("--cd") + 1]
            expected = [argv[0], "exec", "--ignore-user-config", "--ephemeral", "--sandbox",
                        "read-only", "--skip-git-repo-check", "--cd", directory,
                        "--model", backend.MODEL, "--output-last-message", str(Path(directory) / "reply.txt"),
                        "--json", "--color", "never", "-c", 'web_search="disabled"',
                        "-c", 'model_reasoning_effort="{}"'.format(backend.REASONING)]
            for feature in backend.DISABLED:
                expected.extend(["--disable", feature])
            expected.append("-")
            require(argv == expected, "transport flags changed")
            output = self.take("model_output")
            for event in (request, command, output):
                require(event["call"] == call and event["purpose"] == purpose, "call metadata mismatch")
            if output["returncode"]:
                require(backend.is_rate_limit(output["stdout"] + output["stderr"]),
                        "non-rate-limit failure in completed episode")
                error = self.take("call_error")
                require(error["kind"] == "RateLimited", "unjustified retry")
                retry = self.take("retry")
                require(attempt < len(backend.RETRY_DELAYS) and
                        retry["delay_seconds"] == backend.RETRY_DELAYS[attempt], "wrong backoff")
                self.stats["retries"] += 1
                continue
            try:
                event_text, usage = backend.inspect_events(output["stdout"])
            except backend.RateLimited:
                require(self.take("call_error")["kind"] == "RateLimited", "wrong retry kind")
                retry = self.take("retry")
                require(attempt < len(backend.RETRY_DELAYS) and
                        retry["delay_seconds"] == backend.RETRY_DELAYS[attempt], "wrong backoff")
                self.stats["retries"] += 1
                continue
            response = self.take("model_response")
            require(response["raw"].strip() == event_text.strip(), "final reply differs from raw event")
            require(response["usage"] == usage, "usage differs from raw event")
            end = self.take("call_end")
            require(end == dict(event="call_end", scenario=self.scenario, call=call,
                                purpose=purpose, usage=usage), "wrong call-end usage")
            require(response["call"] == call and response["purpose"] == purpose, "wrong response identity")
            for key in ("input_tokens", "output_tokens"):
                self.stats[key] += usage[key]
            return response["raw"]
        raise ValueError("exhausted retries inside supposedly completed episode")


def independent_counts(events, scenario):
    offers, outcome, price, errors, turns = {"buyer": None, "seller": None}, "open", "", 0, 0
    speaker = None
    for event in events:
        if event["event"] == "message":
            turns += 1
            speaker = event["speaker"]
        if event["event"] != "parse":
            continue
        if event["error"] is not None:
            errors += 1
            continue
        act = event["performative"]
        if act == "propose":
            offers[speaker] = event["price"]
        elif act == "accept-proposal":
            price = offers["seller" if speaker == "buyer" else "buyer"]
            require(type(price) is int, "accepted missing offer")
            outcome = "deal"
        elif act == "refuse":
            outcome = "no_deal"
    possible = int(scenario["reserve"] <= scenario["budget"])
    violation = int(outcome == "deal" and (price < scenario["reserve"] or price > scenario["budget"]))
    correct = int((outcome == "deal" and possible and not violation) or
                  (outcome == "no_deal" and not possible))
    readers = sum(e["event"] == "call_start" and e["purpose"] == "reader" for e in events)
    return dict(deal_possible=possible, outcome=outcome, price=price, correct=correct,
                violation=violation, turns=turns, format_errors=errors, reader_calls=readers)


def validate(root=ROOT):
    scenarios = {str(s["id"]): s for s in json.loads((root / "scenarios.json").read_text())}
    prompts = json.loads((root / "prompts.json").read_text())
    rows = read_rows(root)
    row_map = {(r["run"], r["scenario"]): r for r in rows}
    paths = sorted((root / "logs").glob("*.log"))
    require({p.stem for p in paths} == {r["run"] for r in rows}, "logs/CSV run mismatch")
    seen, counts, reference = set(), Counter(), None
    totals = Counter()
    for path in paths:
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        config = events[0]
        require(config["event"] == "config" and config["run"] == path.stem, "missing run config")
        condition = config["condition"]
        require(condition in CONDITIONS, "unknown condition")
        for name in FROZEN:
            require(config["hashes"][name] == hashlib.sha256((root / name).read_bytes()).hexdigest(),
                    "frozen file changed: " + name)
        comparable = {k: config[k] for k in ("provider", "model", "reasoning_effort", "cli_version",
            "authentication", "temperature", "max_output_tokens", "timeout_seconds", "retry_delays",
            "response_schema", "max_turns", "conda_env", "executable")}
        if reference is None:
            reference = comparable
        require(comparable == reference, "settings changed across conditions")
        require(config["max_turns"] == MAX_TURNS and config["model"] == backend.MODEL, "wrong settings")
        episode = None
        for event in events[1:]:
            if event["event"] == "episode_start":
                require(episode is None, "nested or unfinished episode")
                episode = []
            if episode is not None:
                episode.append(event)
            elif event["event"] not in ("recovery", "run_complete"):
                raise ValueError("event outside episode")
            if event["event"] != "episode_result":
                continue
            require(episode is not None, "result without episode start")
            key = (path.stem, str(event["scenario"]))
            require(key in row_map and key not in seen, "extra/duplicate episode")
            row = row_map[key]
            require(row["condition"] == condition, "wrong condition")
            require(row == {k: str(v) for k, v in event["row"].items()}, "CSV differs from logged result")
            note = json.loads(row["note"])
            if note["status"] == "crashed":
                require(all(row[k] == "" for k in ("deal_possible", "outcome", "price", "correct",
                        "violation", "turns", "format_errors", "reader_calls")), "crash counts not blank")
                require(note.get("error") and any(e["event"] in ("crash", "recovery") for e in episode),
                        "missing crash evidence")
                totals["crashes"] += 1
            else:
                require(note["status"] == "completed", "invalid status")
                replay = Replay(episode, key[1])
                start = replay.take("episode_start")
                require(start["evaluation_scenario"] == scenarios[key[1]], "wrong evaluation scenario")
                result = negotiate(scenarios[key[1]], condition, prompts, replay, replay.emit)
                require(result == independent_counts(episode, scenarios[key[1]]), "independent state count mismatch")
                require(all(row[k] == str(v) for k, v in result.items()), "CSV metrics mismatch")
                require(all(note[k] == v for k, v in replay.stats.items()), "CSV usage mismatch")
                require(isinstance(note["wall_seconds"], (int, float)) and note["wall_seconds"] >= 0,
                        "invalid wall time")
                replay.take("episode_result")
                require(replay.cursor == len(episode), "extra episode events")
                totals.update(replay.stats)
                totals["completed"] += 1
            counts[(condition, key[1])] += 1
            seen.add(key)
            print("{} {} verified: outcome={} correct={} turns={} readers={}"
                  .format(*key, row["outcome"], row["correct"], row["turns"], row["reader_calls"]))
            episode = None
        require(episode is None, "unfinished log episode")
    require(seen == set(row_map), "CSV rows without log evidence")
    require(all(counts[(c, sid)] >= 3 for c in CONDITIONS for sid in scenarios), "missing three repeats")
    print("PASS: public/private inputs, raw events, parser/state replay and CSV match.")
    print("Verified totals: " + json.dumps(dict(totals), sort_keys=True))
    return dict(totals)


if __name__ == "__main__":
    validate()
