"""Tool-free Claude CLI adapter for the official v5 Model.complete interface.

Uses the user's existing CLI login. It does not import the Round 3 backend.
Only the kit executes the returned action; Claude's own tools are disabled.
"""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile

from local_support import ROOT, run_id

MODEL = "claude-opus-5-5"
SYSTEM = ("You are the decision model inside an agent harness, not a coding assistant. "
          "The user input is a JSON object containing a chronological messages array. "
          "Follow its system message, consider its user/assistant history, and return ONLY "
          "the next assistant response: one JSON object with thought, action, args. "
          "Do not use Claude Code tools, access files, or execute actions yourself.")


def cli_environment():
    env = dict(os.environ)
    # Keep the CLI login; avoid silently switching to API billing or another provider.
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                 "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
        env.pop(name, None)
    return env


def parse_result(stdout, requested=MODEL):
    if len(stdout.encode()) > 1_000_000:
        raise ValueError("CLI response exceeded the size limit")
    result = json.loads(stdout)
    if (not isinstance(result, dict) or result.get("type") != "result"
            or result.get("subtype") != "success" or result.get("is_error") is not False
            or result.get("num_turns") != 1 or result.get("permission_denials") != []
            or result.get("deferred_tool_use") is not None):
        raise ValueError("CLI did not return one successful tool-free turn")
    usage = result.get("modelUsage")
    if not isinstance(usage, dict) or len(usage) != 1:
        raise ValueError("CLI used an unexpected model; no automatic fallback")
    actual = next(iter(usage))
    if actual != requested and not (requested == "haiku" and actual.startswith("claude-haiku-")):
        raise ValueError("CLI used an unexpected model; no automatic fallback")
    detail = usage[actual]
    if (not isinstance(detail, dict) or detail.get("canonicalModel", actual) != actual
            or type(detail.get("outputTokens")) is not int or detail["outputTokens"] <= 0):
        raise ValueError("CLI model usage could not be verified")
    text = result.get("result")
    if not isinstance(text, str):
        raise ValueError("CLI returned no response text")
    fenced = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text.strip(), re.DOTALL)
    if fenced:
        text = fenced.group(1)
    action = json.loads(text)
    if (not isinstance(action, dict) or not isinstance(action.get("thought"), str)
            or not isinstance(action.get("action"), str) or not action["action"]
            or not isinstance(action.get("args"), dict)):
        raise ValueError("Expected one thought/action/args object")
    return json.dumps(action, ensure_ascii=False)


class ClaudeModel:
    def __init__(self, agent, root=ROOT):
        cfg = agent.get("llm", {})
        if cfg.get("backend") != "claude-cli" or cfg.get("model") not in (MODEL, "haiku"):
            raise ValueError("Expected claude-cli with claude-opus-5-5 or the Round 3 haiku profile")
        self.model = cfg["model"]
        self.effort = cfg.get("effort", "low")
        if self.effort not in ("low", "medium", "high"):
            raise ValueError("Unsupported effort")
        self.root = Path(root).resolve()
        self.executable = shutil.which("claude")
        if not self.executable:
            raise ValueError("Claude CLI is not installed in this environment")

    def command(self):
        return [self.executable, "-p", "--model", self.model, "--effort", self.effort,
                "--tools", "", "--setting-sources", "", "--no-session-persistence",
                "--strict-mcp-config", "--disable-slash-commands", "--max-turns", "1",
                "--settings", '{"disableAllHooks":true}',
                "--output-format", "json", "--system-prompt", SYSTEM]

    def complete(self, messages, max_tokens=4000, timeout=180):
        if not messages or any(m.get("role") not in ("system", "user", "assistant")
                               or not isinstance(m.get("content"), str) for m in messages):
            raise ValueError("Invalid message history")
        prompt = json.dumps({"messages": messages, "requested_output_token_limit": max_tokens}, ensure_ascii=False)
        scratch = self.root / ".local" / "cli"
        scratch.mkdir(parents=True, exist_ok=True)
        logs = self.root / "logs" / "model"
        logs.mkdir(parents=True, exist_ok=True)
        path = logs / (run_id("claude") + ".log")
        # Whole stdout/stderr is preserved; never log environment or authentication files.
        with tempfile.TemporaryDirectory(dir=scratch) as cwd, path.open("x", encoding="utf-8") as log:
            log.write("BACKEND: claude-cli; model=" + self.model + "; tools=disabled\n")
            log.write("INPUT_MESSAGES:\n" + prompt + "\n")
            log.flush()
            process = subprocess.Popen(self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, encoding="utf-8", cwd=cwd,
                                       env=cli_environment(), start_new_session=True)
            try:
                stdout, stderr = process.communicate(prompt, timeout=timeout)
            except BaseException:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = process.communicate()
                log.write("INTERRUPTED\nSTDOUT:\n" + stdout + "\nSTDERR:\n" + stderr + "\n")
                raise
            log.write(f"EXIT: {process.returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}\n")
            log.flush()
            if process.returncode:
                raise ValueError(f"Claude CLI failed (exit {process.returncode}); see {path.relative_to(self.root)}")
            try:
                return parse_result(stdout, self.model)
            except (ValueError, TypeError, KeyError) as error:
                raise ValueError(f"Invalid CLI decision; see {path.relative_to(self.root)}") from error
