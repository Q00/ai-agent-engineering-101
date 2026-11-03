"""ReAct loop with a scripted model (no network, no Codex)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from r3lib import config, react


def scripted(*replies):
    seen = []

    def ask(task, data, timeout, purpose):
        seen.append(data)
        return replies[len(seen) - 1] if len(seen) <= len(replies) else None
    return ask, seen


def test_tool_then_final(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    tools = [react.Tool("double", "twice x", lambda x: {"y": 2 * x}, {"x": "int"})]
    ask, seen = scripted({"thought": "need y", "action": "double", "args": {"x": 4}},
                         {"thought": "done", "final": {"y": 8}})
    final, trace = react.run("double 4", tools, '{"y": int}', purpose="t", ask=ask)
    assert final == {"y": 8}
    assert json.loads(trace[0]["observation"]) == {"y": 8}
    assert seen[1]["transcript"][0]["action"] == "double"          # the observation goes back to the model
    assert (tmp_path / "react.jsonl").exists()


def test_bad_tool_and_errors_are_observations(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    boom = react.Tool("boom", "fails", lambda: 1 / 0)
    ask, _ = scripted({"action": "nope"}, {"action": "boom"}, {"action": "boom"}, {"action": "boom"}, None)
    final, trace = react.run("g", [boom], "{}", purpose="t", ask=ask)
    assert final is None
    assert "unknown tool" in trace[0]["observation"]
    assert "ZeroDivisionError" in trace[1]["observation"]
    assert "already called twice" in trace[3]["observation"]
    assert trace[-1]["stop"] == "no valid reply"


def test_step_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    t = react.Tool("a", "a", lambda: {})
    ask, seen = scripted(*[{"action": "a"}] * 10)
    final, trace = react.run("g", [t], "{}", purpose="t", ask=ask, max_steps=3)
    assert final is None and len(seen) == 3
