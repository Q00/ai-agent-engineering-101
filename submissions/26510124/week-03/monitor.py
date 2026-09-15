"""Deterministic monitor/evaluator for execution history and reputation."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DOMAINS = ("calculation", "writing", "code")


@dataclass(frozen=True)
class Profile:
    contractor_id: str
    profile_version: int
    task_type: str
    verified_attempts: int
    reputation: float
    last_task_tokens: int | None
    recent_parse_fails: int
    token_history_status: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Validation:
    success: bool
    detail: str
    cost: int = 1


def answer_body(text: str) -> str:
    """Return the final Answer body while preserving multiline code/text."""
    matches = list(re.finditer(r"(?im)^\s*Answer\s*:\s*", text or ""))
    if matches:
        return text[matches[-1].end():].strip()
    return (text or "").strip()


def extract_python(text: str) -> str:
    body = answer_body(text)
    fenced = re.search(r"```(?:python)?\s*\n(.*?)```", body, flags=re.I | re.S)
    return (fenced.group(1) if fenced else body).strip()


_PYTHON_VALIDATOR = r'''
import ast
import json
import sys

payload = json.loads(sys.stdin.read())
source = payload["source"]
function_name = payload["function"]
cases = payload["cases"]

tree = ast.parse(source)
blocked_nodes = (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)
blocked_calls = {"open", "exec", "eval", "compile", "__import__", "input", "globals", "locals"}
for node in ast.walk(tree):
    if isinstance(node, blocked_nodes):
        raise ValueError(f"blocked syntax: {type(node).__name__}")
    if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
        raise ValueError("dunder attributes are blocked")
    if isinstance(node, ast.Name) and node.id.startswith("__"):
        raise ValueError("dunder names are blocked")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in blocked_calls:
        raise ValueError(f"blocked call: {node.func.id}")

safe_builtins = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "float": float, "int": int, "isinstance": isinstance,
    "len": len, "list": list, "max": max, "min": min, "range": range,
    "reversed": reversed, "round": round, "set": set, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple, "zip": zip,
}
namespace = {"__builtins__": safe_builtins}
exec(compile(tree, "<contractor-answer>", "exec"), namespace, namespace)
function = namespace.get(function_name)
if not callable(function):
    raise ValueError(f"function {function_name!r} not found")
for index, case in enumerate(cases, 1):
    actual = function(*case["args"])
    if actual != case["expected"]:
        raise AssertionError(
            f"case {index}: expected {case['expected']!r}, got {actual!r}"
        )
print(json.dumps({"ok": True, "cases": len(cases)}))
'''


def _validate_python(task: dict[str, Any], answer: str) -> Validation:
    source = extract_python(answer)
    specification = task["validator"]
    payload = {
        "source": source,
        "function": specification["function"],
        "cases": specification["cases"],
    }
    with tempfile.TemporaryDirectory(prefix="week03-validate-") as temp_dir:
        try:
            process = subprocess.run(
                [sys.executable, "-I", "-S", "-c", _PYTHON_VALIDATOR],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                cwd=Path(temp_dir),
                timeout=3,
                check=False,
                env={"PYTHONHASHSEED": "0"},
            )
        except subprocess.TimeoutExpired:
            return Validation(False, "code validation timed out")
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).strip().splitlines()
        return Validation(False, detail[-1][:300] if detail else "code validation failed")
    return Validation(True, process.stdout.strip() or "code tests passed")


def validate_result(task: dict[str, Any], answer: str) -> Validation:
    specification = task["validator"]
    kind = specification["kind"]
    body = answer_body(answer)
    if kind == "exact":
        expected = str(specification["expected"]).strip()
        success = body == expected
        return Validation(success, f"expected={expected!r};actual={body!r}")
    if kind == "contains_all":
        missing = [value for value in specification["values"] if value not in body]
        minimum = int(specification.get("min_chars", 0))
        if len(body) < minimum:
            missing.append(f"minimum length {minimum}")
        return Validation(not missing, "ok" if not missing else f"missing={missing}")
    if kind == "python_function":
        return _validate_python(task, answer)
    return Validation(False, f"unknown validator kind: {kind}")


class Monitor:
    """Keep only verified history; never participate in winner selection."""

    def __init__(self, contractor_ids: tuple[str, ...] = ("A", "B", "C")):
        self.contractor_ids = contractor_ids
        self.version = 0
        self._attempts: dict[tuple[str, str], int] = defaultdict(int)
        self._successes: dict[tuple[str, str], int] = defaultdict(int)
        self._last_tokens: dict[str, int | None] = {name: None for name in contractor_ids}
        self._token_status: dict[str, str] = {name: "no_history" for name in contractor_ids}
        self._parse_fails: dict[str, int] = defaultdict(int)
        self.validation_cost = 0

    def note_parse_fail(self, contractor_id: str) -> None:
        self._parse_fails[contractor_id] += 1
        self.version += 1

    def snapshot(self, contractor_id: str, task_type: str) -> Profile:
        key = (contractor_id, task_type)
        attempts = self._attempts[key]
        successes = self._successes[key]
        reputation = (successes + 1) / (attempts + 2)
        return Profile(
            contractor_id=contractor_id,
            profile_version=self.version,
            task_type=task_type,
            verified_attempts=attempts,
            reputation=reputation,
            last_task_tokens=self._last_tokens[contractor_id],
            recent_parse_fails=self._parse_fails[contractor_id],
            token_history_status=self._token_status[contractor_id],
        )

    def evaluate_and_update(
        self,
        contractor_id: str,
        task: dict[str, Any],
        answer: str,
        task_tokens: int | None,
    ) -> Validation:
        validation = validate_result(task, answer)
        key = (contractor_id, task["domain"])
        self._attempts[key] += 1
        if validation.success:
            self._successes[key] += 1
        if type(task_tokens) is int and task_tokens >= 0:
            self._last_tokens[contractor_id] = task_tokens
            self._token_status[contractor_id] = "measured"
        else:
            self._last_tokens[contractor_id] = None
            self._token_status[contractor_id] = "usage_unavailable"
        self.validation_cost += validation.cost
        self.version += 1
        return validation

    def reputation_summary(self) -> dict[str, float]:
        summary: dict[str, float] = {}
        for contractor_id in self.contractor_ids:
            values = [
                self.snapshot(contractor_id, domain).reputation for domain in DOMAINS
            ]
            summary[contractor_id] = round(sum(values) / len(values), 4)
        return summary
