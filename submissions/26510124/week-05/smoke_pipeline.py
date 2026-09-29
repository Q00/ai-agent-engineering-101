#!/usr/bin/env python3
"""Offline integration: real runner/host/MCP/HTTP, scripted decisions, no model API.

The synthetic 48-episode output exists only in a temporary directory. It is
independently replayed before deletion and is never assignment result data.
"""

from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
import hashlib
import json
from pathlib import Path
import re
import tempfile

from openai.types.chat import ChatCompletionMessage

from analyze_results import analyze
from run_experiment import build_parser, run_batch


class ScriptedBackend:
    """A deterministic transport fixture; knows only the current host messages."""

    def __init__(self, **configuration):
        self.settings = configuration
        self.calls = 0

    def configuration(self):
        return {**self.settings, "backend": "scripted-offline", "external_model_calls": False}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def complete(self, messages, tools, log):
        self.calls += 1
        role = re.search(r"^You are the (buyer|seller)", messages[0]["content"]).group(1)
        limit = int(re.search(r"Your private limit: you can (?:pay at most|accept at least) (\d+)",
                              messages[0]["content"]).group(1))
        negotiation_id = re.search(r"negotiation (\S+)\.", messages[1]["content"]).group(1)
        arguments = {"negotiation_id": negotiation_id}
        if messages[-1]["role"] != "tool":
            name = "get_negotiation"
        else:
            view = json.loads(messages[-1]["content"])
            if role == "seller":
                name = "propose"
                arguments["price"] = limit
            else:
                seller_proposals = [move for move in view["moves"]
                                    if move["actor"] == "seller" and move["act"] == "propose"]
                if not seller_proposals:
                    name = "propose"
                    arguments["price"] = max(0, limit - 10)
                else:
                    name = "accept_proposal" if seller_proposals[-1]["price"] <= limit else "refuse"
        assert name in {tool["function"]["name"] for tool in tools}
        message = ChatCompletionMessage.model_validate({
            "role": "assistant", "content": "Scripted offline fixture decision.",
            "tool_calls": [{"id": f"scripted-{self.calls}", "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)}}],
        })
        log({"event": "model_call", "attempt": 1, "model": "scripted-offline", "message_count": len(messages)})
        log({"event": "model_result", "response_model": "scripted-offline", "finish_reason": "tool_calls",
             "message": message.model_dump(mode="json", exclude_none=True),
             "usage": {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}})
        return message


class ResumeMustNotEnter(ScriptedBackend):
    async def __aenter__(self):
        raise AssertionError("resuming a completed batch entered the backend")


def digests(root: Path) -> dict[str, str]:
    paths = [root / "results.csv", *sorted((root / "logs").glob("*.jsonl"))]
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


async def smoke() -> dict:
    with tempfile.TemporaryDirectory(prefix="week05-pipeline-smoke-") as directory:
        root = Path(directory)
        args = build_parser().parse_args(["--output-dir", directory, "--model", "scripted-offline"])
        with (root / "console.txt").open("w", encoding="utf-8") as console, redirect_stdout(console):
            rows = await run_batch(args, backend_factory=ScriptedBackend, require_committed=False)
        assert len(rows) == 48, f"expected 48 episodes, found {len(rows)}"
        data = analyze(root)
        assert data["audit"] == "passed"
        assert len(list((root / "logs").glob("*.jsonl"))) == 12
        assert data["response_models"] == ["scripted-offline"]
        assert data["system_prompts_checked"] == 8
        assert data["tool_schemas_checked"] == 1
        assert data["model_calls"]["physical_calls"] == 288
        for condition in data["conditions"]:
            assert condition["episodes"] == condition["completed"] == condition["correct"] == 12
            assert condition["crashed"] == condition["violation"] == condition["attempted_violations"] == condition["refused_calls"] == 0
            assert condition["mean_turns"] == 3 and condition["mean_tool_calls"] == 6
            assert condition["outcomes"] == {"deal": 9, "no_deal": 3}
            expected_injections = 12 if condition["condition"].endswith("_inject") else 0
            assert condition["injection_views"] == expected_injections
        before = digests(root)
        with (root / "resume-console.txt").open("w", encoding="utf-8") as console, redirect_stdout(console):
            resumed = await run_batch(args, backend_factory=ResumeMustNotEnter, require_committed=False)
        assert len(resumed) == 48
        assert digests(root) == before, "resume modified existing CSV or logs"
        assert analyze(root)["audit"] == "passed"
        return {"event": "pipeline_smoke", "kind": "scripted_offline_not_experiment_results",
                "audit": data["audit"], "episodes": data["episodes"], "run_logs": 12,
                "external_model_requests": 0, "scripted_model_responses": 288,
                "mcp_tool_calls": sum(condition["tool_calls"] for condition in data["conditions"]),
                "conditions": data["conditions"], "system_prompts_checked": data["system_prompts_checked"],
                "tool_schemas_checked": data["tool_schemas_checked"], "resume": "no backend entry; CSV/log hashes unchanged",
                "outputs": "temporary; removed after replay", "runtime_fingerprint": data["manifest"]["fingerprint"]}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(smoke()), ensure_ascii=False, indent=2))
