"""Contractor bidding and Week 02-style task execution harnesses."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from agent_tools import TOOL_SPECS, ToolRuntime
from model_client import Backend, Conversation, ModelReply, Usage


@dataclass(frozen=True)
class Contractor:
    contractor_id: str
    skill: str
    bid_instruction: str = ""


BASE_SKILLS = {
    "A": "You specialize in arithmetic, quantitative reasoning, and calculations.",
    "B": "You specialize in writing, editing, summarization, and clear communication.",
    "C": "You specialize in Python programming, debugging, and code review.",
}
GENERALIST_SKILL = (
    "You are a generalist problem solver with equal competence in calculation, "
    "writing, and Python programming."
)
OVERCONFIDENT = (
    "For this bidding experiment, bid on every task and report confidence from "
    "95 to 100 even when the task is outside your specialty."
)


def make_team(condition: str) -> list[Contractor]:
    if condition not in {"baseline", "homogeneous", "overconfident"}:
        raise ValueError(f"unknown condition: {condition}")
    if condition == "homogeneous":
        return [Contractor(name, GENERALIST_SKILL) for name in ("A", "B", "C")]
    return [
        Contractor(name, skill, OVERCONFIDENT if condition == "overconfident" and name == "C" else "")
        for name, skill in BASE_SKILLS.items()
    ]


BID_FORMAT = """Return exactly one JSON object and no Markdown or prose:
{"bid": true, "confidence": 0, "reason": "one short sentence"}
`bid` must be a JSON boolean, `confidence` a number from 0 through 100, and
`reason` a non-empty sentence. Judge only whether you should perform the task.
Do not attempt the task and do not invent IDs, history, or token counts."""


@dataclass(frozen=True)
class BidDecision:
    bid: bool
    confidence: float
    reason: str


@dataclass
class BidAttempt:
    status: str
    raw: str
    usage: Usage = field(default_factory=Usage)
    decision: BidDecision | None = None
    error: str = ""


def parse_bid(text: str) -> BidDecision:
    """Strictly validate the three fields the contractor LLM may generate."""
    try:
        value = json.loads(text.strip())
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict) or set(value) != {"bid", "confidence", "reason"}:
        raise ValueError("expected exactly bid, confidence, and reason")
    if type(value["bid"]) is not bool:
        raise ValueError("bid must be a JSON boolean")
    confidence = value["confidence"]
    if type(confidence) not in (int, float) or not math.isfinite(confidence):
        raise ValueError("confidence must be a finite number")
    if not 0 <= confidence <= 100:
        raise ValueError("confidence must be between 0 and 100")
    reason = value["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must be a non-empty string")
    return BidDecision(value["bid"], float(confidence), reason.strip())


def request_bid(backend: Backend, contractor: Contractor, task: dict[str, Any]) -> BidAttempt:
    system = (
        f"You are contractor {contractor.contractor_id}. {contractor.skill} "
        f"{contractor.bid_instruction}\n\n{BID_FORMAT}"
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Task announcement: {task['desc']}"},
    ]
    try:
        reply = backend.complete(messages, tools=None)
    except Exception as exc:
        return BidAttempt("request_error", "", error=f"{type(exc).__name__}: {exc}")
    try:
        decision = parse_bid(reply.text)
    except ValueError as exc:
        return BidAttempt("parse_fail", reply.text, reply.usage, error=str(exc))
    return BidAttempt("valid", reply.text, reply.usage, decision)


@dataclass
class AgentRun:
    answer: str
    usage: Usage
    iterations: int
    interventions: int
    status: str
    tool_events: list[dict[str, Any]]


def _run_tool_calls(
    conversation: Conversation,
    reply: ModelReply,
    runtime: ToolRuntime,
    log: Callable[[dict[str, Any]], None],
) -> None:
    for call in reply.tool_calls:
        output = runtime.execute(call.name, call.arguments)
        log(
            {
                "event": "tool",
                "tool": call.name,
                "arguments": call.arguments,
                "output": output[:500],
            }
        )
        conversation.add_tool_result(call, output)


REACT_SYSTEM = """{identity}
Solve the awarded task with the tools you are given. Before every tool call,
write one line starting with `Thought:` describing what you will check. When
complete, reply with a line starting with `Answer:` and make no tool call.
Use tools when they provide evidence; never claim a tool result you did not see."""

VERIFY = """Check the proposed Answer against the tool observations and task.
If every claim is supported, reply with exactly VERIFIED. If not, call tools as
needed and then provide a corrected line starting with Answer:."""


def run_react(
    backend: Backend,
    contractor: Contractor,
    task: dict[str, Any],
    *,
    max_steps: int = 8,
    max_verify: int = 1,
    allow_write: bool = False,
    log: Callable[[dict[str, Any]], None] = lambda event: None,
) -> AgentRun:
    runtime = ToolRuntime(allow_write=allow_write)
    chat = Conversation(
        backend,
        REACT_SYSTEM.format(
            identity=f"You are contractor {contractor.contractor_id}. {contractor.skill}"
        ),
        TOOL_SPECS,
    )
    chat.add_user(task["desc"])
    verifies = 0
    last_answer = ""
    status = "max_steps"

    for step in range(1, max_steps + 1):
        reply = chat.send()
        log({"event": "agent_turn", "harness": "react", "step": step, "text": reply.text})
        if "Answer:" in reply.text:
            last_answer = reply.text
        if reply.tool_calls:
            _run_tool_calls(chat, reply, runtime, log)
            continue

        if verifies >= max_verify:
            answer = last_answer if reply.text.strip().upper() == "VERIFIED" and last_answer else reply.text
            status = "verify_budget_accepted"
            return AgentRun(answer, chat.usage, chat.iterations, runtime.interventions, status, runtime.events)

        verifies += 1
        proposed = reply.text
        chat.add_user(VERIFY)
        check = chat.send()
        log({"event": "verification", "text": check.text, "tool_calls": len(check.tool_calls)})
        if check.tool_calls:
            _run_tool_calls(chat, check, runtime, log)
            last_answer = proposed if "Answer:" in proposed else last_answer
            continue
        if check.text.strip().upper().startswith("VERIFIED"):
            status = "verified"
            return AgentRun(proposed, chat.usage, chat.iterations, runtime.interventions, status, runtime.events)
        status = "revised"
        return AgentRun(check.text, chat.usage, chat.iterations, runtime.interventions, status, runtime.events)

    return AgentRun(
        last_answer or "MAX_STEPS reached: incomplete",
        chat.usage,
        chat.iterations,
        runtime.interventions,
        status,
        runtime.events,
    )


PLAN_SYSTEM = """{identity}
Plan how to solve the awarded task. Reply with a JSON list of short step
strings and nothing else. The plan may name only the tools listed by the user."""

EXECUTE_SYSTEM = """{identity}
Execute one supplied plan step at a time with the tools you are given. If a
step cannot be completed, start your response with OFF_PLAN:. When asked for
the final result, start it with Answer:."""

_TOOL_ARITY = {tool["name"]: len(tool["parameters"]["properties"]) for tool in TOOL_SPECS}
_TOOL_SIGNATURES = "\n".join(
    f"- {tool['name']}({', '.join(tool['parameters']['properties'])}): {tool['description']}"
    for tool in TOOL_SPECS
)
_CALL_PATTERN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*\.?\s*$")


def parse_plan(text: str) -> list[str] | None:
    try:
        value = json.loads(text.strip())
    except json.JSONDecodeError:
        return None
    if isinstance(value, list) and value and all(isinstance(step, str) for step in value):
        return value
    return None


def _split_args(text: str) -> list[str]:
    return [part for part in re.findall(r"'[^']*'|\"[^\"]*\"|[^,]+", text) if part.strip()]


def invalid_plan_steps(plan: list[str]) -> list[str]:
    invalid: list[str] = []
    for step in plan:
        match = _CALL_PATTERN.match(step)
        if not match:
            continue
        name, arguments = match.group(1), _split_args(match.group(2))
        if name not in _TOOL_ARITY:
            invalid.append(f"{step!r}: unknown tool {name}")
        elif len(arguments) != _TOOL_ARITY[name]:
            invalid.append(
                f"{step!r}: {name} takes {_TOOL_ARITY[name]} argument(s), got {len(arguments)}"
            )
    return invalid


def run_plan_execute(
    backend: Backend,
    contractor: Contractor,
    task: dict[str, Any],
    *,
    max_replan: int = 1,
    max_tool_rounds: int = 3,
    max_plan_fixes: int = 1,
    allow_write: bool = False,
    log: Callable[[dict[str, Any]], None] = lambda event: None,
) -> AgentRun:
    identity = f"You are contractor {contractor.contractor_id}. {contractor.skill}"
    runtime = ToolRuntime(allow_write=allow_write)
    planner = Conversation(backend, PLAN_SYSTEM.format(identity=identity))
    planner.add_user(f"Task: {task['desc']}\nAvailable tools:\n{_TOOL_SIGNATURES}")
    raw = planner.send().text
    plan = parse_plan(raw)
    log({"event": "plan", "raw": raw})
    if plan is None:
        return AgentRun("plan parse failed", planner.usage, planner.iterations, 0, "plan_parse_fail", [])

    fixes = 0
    bad = invalid_plan_steps(plan)
    if bad and max_plan_fixes:
        planner.add_user(
            "These steps are invalid:\n"
            + "\n".join(bad)
            + f"\nThe available tools are:\n{_TOOL_SIGNATURES}\nReturn a corrected JSON list only."
        )
        fixed = parse_plan(planner.send().text)
        fixes += 1
        if fixed is not None:
            plan = fixed
            bad = invalid_plan_steps(plan)
        log({"event": "plan_check", "fixes": fixes, "invalid_left": bad, "plan": plan})

    executor = Conversation(backend, EXECUTE_SYSTEM.format(identity=identity), TOOL_SPECS)
    executor.add_user(f"Task: {task['desc']}\nPlan: {json.dumps(plan, ensure_ascii=False)}")
    replans = 0
    index = 0
    while index < len(plan):
        executor.add_user(f"Execute step {index + 1}: {plan[index]}")
        reply = executor.send()
        rounds = 0
        while reply.tool_calls and rounds < max_tool_rounds:
            _run_tool_calls(executor, reply, runtime, log)
            reply = executor.send()
            rounds += 1
        if reply.tool_calls:
            _run_tool_calls(executor, reply, runtime, log)
            reply = ModelReply("OFF_PLAN: tool-call budget exceeded")
        log({"event": "agent_turn", "harness": "plan_execute", "step": index + 1, "text": reply.text})

        if reply.text.strip().startswith("OFF_PLAN:") and replans < max_replan:
            replans += 1
            planner.add_user(
                f"Step {index + 1} failed: {reply.text}\nReturn a JSON list of remaining steps only."
            )
            tail = parse_plan(planner.send().text)
            if tail is None:
                break
            plan = plan[:index] + tail
            log({"event": "replan", "replans": replans, "plan": plan})
            continue
        index += 1

    executor.add_user("Give the final result now, starting with Answer:.")
    final = executor.send()
    if final.tool_calls:
        _run_tool_calls(executor, final, runtime, log)
        final = executor.send()
    usage = planner.usage + executor.usage
    status = f"completed;replans={replans};planfixes={fixes};invalid_left={len(bad)}"
    return AgentRun(final.text, usage, planner.iterations + executor.iterations,
                    runtime.interventions, status, runtime.events)


def execute_task(
    harness: str,
    backend: Backend,
    contractor: Contractor,
    task: dict[str, Any],
    *,
    allow_write: bool = False,
    log: Callable[[dict[str, Any]], None] = lambda event: None,
) -> AgentRun:
    if harness == "react":
        return run_react(backend, contractor, task, allow_write=allow_write, log=log)
    if harness == "plan_execute":
        return run_plan_execute(backend, contractor, task, allow_write=allow_write, log=log)
    raise ValueError(f"unknown harness: {harness}")
