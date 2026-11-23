"""Validate stage-1 inputs offline; this is not a server or model test."""

import ast
import csv
import json
import string
from importlib.metadata import version
from pathlib import Path


ROOT = Path(__file__).resolve().parent
HEADER = "run,condition,scenario,deal_possible,outcome,price,correct,violation,attempted_violations,refused_calls,turns,tool_calls,note".split(",")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    scenarios = json.loads((ROOT / "scenarios.json").read_text(encoding="utf-8"))
    require(isinstance(scenarios, list) and len(scenarios) >= 4, "At least four scenarios required")
    ids = []
    for scenario in scenarios:
        require(isinstance(scenario, dict), "Scenario must be an object")
        require(set(scenario) == {"id", "item", "reserve", "budget"}, "Scenario fields mismatch")
        for field in ("id", "item"):
            require(isinstance(scenario[field], str) and scenario[field].strip(), f"Invalid {field}")
        for field in ("reserve", "budget"):
            require(type(scenario[field]) is int and scenario[field] >= 0, f"Invalid integer {field}")
        ids.append(scenario["id"])
    require(len(ids) == len(set(ids)), "Duplicate scenario IDs")
    feasible = sum(s["reserve"] <= s["budget"] for s in scenarios)
    require(0 < feasible < len(scenarios), "Need feasible and infeasible scenarios")
    require(any(s["reserve"] == s["budget"] for s in scenarios), "Boundary scenario missing")
    print(f"PASS scenarios: {len(scenarios)}, feasible={feasible}, infeasible={len(scenarios)-feasible}")

    config = json.loads((ROOT / "experiment.json").read_text(encoding="utf-8"))
    require(config["host"] == "codex exec", "Unexpected host")
    require(config["conditions"] == ["prompt_inject", "server_inject"], "Required conditions mismatch")
    require(config["repeats"] >= 3 and type(config["repeats"]) is int, "Invalid repeat count")
    require(config["max_host_turns"] == 8, "Host turn cap must be 8")
    require(config["max_tool_calls_per_host_turn"] == 8, "Tool call cap mismatch")
    require(config["first_role"] == "buyer", "Buyer must open")
    require(config["model"] == "gpt-6-luna" and config["reasoning_effort"] == "low", "Small-model setting mismatch")
    require(config["temperature"] is None, "CLI temperature must be recorded as not set")
    require(config["authentication"] == "chatgpt", "Subscription login selected")
    require(config["scenario_file"] == "scenarios.json" and config["prompt_file"] == "prompts.json", "Input paths mismatch")
    print(f"PASS fixed experiment inputs: {len(scenarios)*config['repeats']*len(config['conditions'])} planned episodes")

    prompts = json.loads((ROOT / "prompts.json").read_text(encoding="utf-8"))
    require(set(prompts) == {"buyer", "seller"}, "Exactly one template per role required")
    for role, template in prompts.items():
        fields = {field for _, field, _, _ in string.Formatter().parse(template) if field}
        require(fields == {"item", "limit", "negotiation_id"}, "Prompt fields must exclude condition and opposite limit")
        require(not any(condition in template for condition in config["conditions"]), "Condition leaked into prompt")
        for scenario in scenarios:
            limit = scenario["budget"] if role == "buyer" else scenario["reserve"]
            rendered = [template.format(item=scenario["item"], limit=limit, negotiation_id="validation-id") for _ in config["conditions"]]
            require(rendered[0] == rendered[1], "Role instructions differ across conditions")
    print("PASS prompts: one template per role, condition-independent rendering")

    with (ROOT / "results.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    require(rows and rows[0] == HEADER, "CSV header mismatch")
    print(f"PASS CSV header; episode rows currently present={len(rows)-1}")

    # Scan project source only. A local virtual environment is not submission source.
    sources = [p for p in ROOT.rglob("*.py") if not any(part in {".venv", "__pycache__"} for part in p.relative_to(ROOT).parts)]
    for source in sources:
        ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    print(f"PASS source syntax: {len(sources)} Python files")

    from mcp import Client
    from mcp.server import MCPServer

    require(version("mcp") == "2.2.0", "SDK version differs from requirements.txt")
    require(Client is not None and MCPServer is not None, "SDK import failed")
    print("PASS SDK: mcp==2.2.0, Client and MCPServer import")
    print("Design inputs validated. Use HTTP tests and verify_evidence.py for behavioral and experiment evidence.")


if __name__ == "__main__":
    main()
