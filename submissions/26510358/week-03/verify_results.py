"""Recount saved raw bids independently of the manager; no model/API calls."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

from contractor import ANNOUNCEMENT, make_contractors, parse_bid
from run import HEADER, load_tasks

HERE = Path(__file__).resolve().parent


def verify(root=HERE):
    tasks = load_tasks(root / "tasks.json")
    digest = hashlib.sha256((root / "tasks.json").read_bytes()).hexdigest()
    with (root / "results.csv").open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == HEADER, "CSV header mismatch"
        rows = list(reader)
    assert len({row["run"] for row in rows}) == len(rows), "Duplicate run id"
    complete = Counter()
    referenced_logs = set()
    settings = None
    for row in rows:
        note = json.loads(row["note"])
        path = root / note["log"]
        assert path not in referenced_logs, "Reused log file"
        referenced_logs.add(path)
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            name, payload = line.split(" ", 1)
            events.append((name[1:-1], json.loads(payload)))
        assert events[0][0] == "settings" and events[-1][0] == "summary", path
        metadata = events[0][1]
        assert str(metadata["run"]) == row["run"] and metadata["condition"] == row["condition"]
        assert metadata["task_sha256"] == note["task_sha256"] == digest
        assert events[1] == ("tasks", {"tasks": tasks})
        expected_team = make_contractors(row["condition"])
        assert events[2:5] == [("system", {"contractor": c.name, "content": c.system})
                               for c in expected_team]
        summary = events[-1][1]
        assert all(str(summary[key]) == row[key] for key in HEADER), "Summary/CSV mismatch"
        if note["status"] == "crashed":
            assert all(row[key] == "" for key in HEADER[2:7]) and note.get("error")
            assert any(event == "error" for event, _ in events)
            print(f"run={row['run']} {row['condition']}: preserved crash")
            continue
        assert note["status"] == "complete"
        comparable = {key: value for key, value in metadata.items()
                      if key not in ("run", "condition", "started_utc", "git_commit")}
        settings = comparable if settings is None else settings
        assert comparable == settings, "Generation settings changed between completed runs"
        remaining = iter(events[5:-1])
        metrics = dict(tasks=len(tasks), correct=0, messages=0, unassigned=0, misawards=0)
        parse_fails = input_tokens = output_tokens = calls = 0
        for task in tasks:
            candidates = []
            for contractor in expected_team:
                assert next(remaining) == ("announce", dict(task=task["id"], contractor=contractor.name,
                    content=ANNOUNCEMENT.format(cid=task["id"], desc=task["desc"])))
                metrics["messages"] += 1
                event, model = next(remaining)
                assert event == "model" and model["input_tokens"] is not None
                input_tokens += model["input_tokens"]
                output_tokens += model["output_tokens"]
                calls += 1
                event, raw = next(remaining)
                assert event == "raw" and raw["task"] == task["id"] and raw["contractor"] == contractor.name
                parsed = parse_bid(raw["text"])
                identity = dict(task=task["id"], contractor=contractor.name)
                if parsed is None:
                    assert next(remaining) == ("parse_fail", identity)
                    parse_fails += 1
                else:
                    assert next(remaining) == ("bid", dict(identity, **parsed))
                    if parsed["bid"]:
                        metrics["messages"] += 1
                        candidates.append((contractor.name, parsed["confidence"]))
            event, outcome = next(remaining)
            if not candidates:
                metrics["unassigned"] += 1
                assert (event, outcome) == ("unassigned", dict(task=task["id"], gold=task["gold"]))
            else:
                # Independent stable selection from the logged bids.
                highest = max(confidence for _, confidence in candidates)
                winner = next(name for name, confidence in candidates if confidence == highest)
                correct = winner == task["gold"]
                metrics["messages"] += 1
                metrics["correct" if correct else "misawards"] += 1
                assert (event, outcome) == ("award", dict(task=task["id"], winner=winner,
                    confidence=highest, gold=task["gold"], correct=correct))
        assert list(remaining) == [], "Unexpected trailing events"
        assert all(int(row[key]) == value for key, value in metrics.items()), "Recount/CSV mismatch"
        assert metrics["correct"] + metrics["misawards"] + metrics["unassigned"] == len(tasks)
        usage = dict(calls=calls, replies=calls, input_tokens=input_tokens, output_tokens=output_tokens)
        assert summary["usage"] == usage
        for key, value in dict(usage, tokens=input_tokens + output_tokens,
                               parse_fails=parse_fails, completed_tasks=len(tasks)).items():
            assert note[key] == value, key
        complete[row["condition"]] += 1
        print(f"run={row['run']} {row['condition']}: correct={metrics['correct']}/{len(tasks)} "
              f"messages={metrics['messages']} misawards={metrics['misawards']} "
              f"parse_fails={parse_fails} tokens={note['tokens']} OK")
    assert referenced_logs == set((root / "logs").glob("*.txt")), "Unreferenced log file"
    assert all(complete[condition] >= 3 for condition in ("baseline", "homogeneous", "overconfident"))
    print(f"Verified {len(rows)} CSV rows and logs; completed runs: {dict(complete)}")


if __name__ == "__main__":
    verify()
