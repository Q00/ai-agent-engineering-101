"""Cross-check committed measurements against raw CLI events; no model calls."""
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import statistics

from run_ab import HEADER, judge, read_task
from tools_shared import TOOL_SPECS

ROOT = Path(__file__).resolve().parent


def records(lines, prefix):
    return [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]


def main():
    task, expected = read_task(ROOT / "TASK.md")
    original = ROOT.parents[2] / "weeks/week-02/starter/app.log"
    assert (ROOT / "app.log").read_bytes() == original.read_bytes(), "input changed"
    assert (ROOT / "TASK.md").read_bytes() == original.with_name("TASK.md").read_bytes()
    counts = Counter(line.split()[1][:2] + ":00" for line in
                     (ROOT / "app.log").read_text().splitlines() if line.split()[2] == "ERROR")
    assert counts.most_common(1)[0][0] == expected
    with (ROOT / "results.csv").open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == HEADER
        rows = list(reader)
    assert len({r["run"] for r in rows}) == len(rows), "duplicate run numbers"
    settings = None
    totals = defaultdict(list)
    for row in rows:
        run = int(row["run"])
        lines = (ROOT / "logs" / f'{row["harness"]}-{run:02d}.txt').read_text().splitlines()
        config, = records(lines, "[config] ")
        if settings is None:
            settings = config
        assert config == settings, "settings changed between runs"
        assert config["provider"] == "codex" and config["model"] == "gpt-6-astra"
        assert config["conda_env"] == "base"
        assert config["tool_specs"] == TOOL_SPECS
        for name, digest in config["file_sha256"].items():
            assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
        info, = records(lines, "[run] ")
        assert (info["run"], info["harness"], info["task"]) == (run, row["harness"], task)
        meter, = records(lines, "[meter] ")
        answer, = records(lines, "[answer-json] ")
        assert row["success"] == ("O" if judge(answer, expected) else "X")
        prompts = records(lines, "[model-input] ")
        assert len(prompts) == int(row["iters"]) == meter["iters"]
        assert int(row["interventions"]) == meter["interventions"] == 0
        for prompt in prompts:
            assert set(prompt) == {"instructions", "system", "history", "tools_enabled", "tools"}
            assert prompt["tools"] == (TOOL_SPECS if prompt["tools_enabled"] else [])
        events = records(lines, "[codex-event] ")
        completed = [e for e in events if e.get("type") == "turn.completed"]
        for event in events:
            item = event.get("item", {})
            assert not item or item.get("type") in ("agent_message", "reasoning")
        token_sum = sum(e["usage"]["input_tokens"] + e["usage"]["output_tokens"] for e in completed)
        assert token_sum == meter["tokens"]
        if meter["tokens_complete"]:
            assert int(row["tokens"]) == token_sum
            assert len(completed) == meter["iters"]
        else:
            assert row["tokens"] == ""
        if row["success"] == "O":
            assert any(line.startswith("  [tool] ") for line in lines), "no input observations"
        totals[row["harness"]].append(row)
        print(f'{row["harness"]}-{run:02d}: {row["success"]}, tokens={row["tokens"]}, '
              f'iters={row["iters"]}, verified {len(completed)} raw usage events')
    for name in ("react", "plan_exec"):
        group = totals[name]
        assert len(group) >= 3
        known = [int(r["tokens"]) for r in group if r["tokens"]]
        print(f'{name}: successes={sum(r["success"] == "O" for r in group)}/{len(group)}, '
              f'mean_tokens={statistics.mean(known) if known else "unknown"}, '
              f'mean_iters={statistics.mean(int(r["iters"]) for r in group)}')
    print("Reference ERROR counts:", dict(sorted(counts.items())))
    print("All raw-event, input, settings, and CSV checks passed.")


if __name__ == "__main__":
    main()
