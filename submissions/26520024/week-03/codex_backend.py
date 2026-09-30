"""Fresh tool-free Codex invocation per bid; preserve unmodified model output."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

MODEL = "gpt-6-astra"
REASONING = "low"
TIMEOUT = 180
DISABLED = ("shell_tool", "unified_exec", "multi_agent", "apps", "plugins", "hooks",
            "browser_use", "computer_use", "image_generation", "view_image", "sleep_tool")
INSTRUCTION = (
    "Act as the language model for the enclosed system and user messages. "
    "Produce exactly one final assistant reply following the enclosed system "
    "instruction. Do not use native Codex tools, inspect files, or execute code. "
    "Return only the requested reply, with no wrapper or markdown."
)


def executable():
    binary = shutil.which(os.environ.get("CODEX_BIN", "codex"))
    if binary is None:
        raise RuntimeError("Codex CLI missing; set CODEX_BIN")
    return binary


def backend_info():
    version = subprocess.run([executable(), "--version"], text=True,
                             capture_output=True, timeout=10, check=True)
    status = subprocess.run([executable(), "login", "status"], text=True,
                            capture_output=True, timeout=10)
    if status.returncode:
        raise RuntimeError("Authenticate Codex in your terminal first")
    return dict(provider="OpenAI via Codex CLI", model=MODEL,
                cli_version=version.stdout.strip(),
                authentication="ChatGPT" if "ChatGPT" in status.stdout + status.stderr
                else "configured", reasoning_effort=REASONING,
                temperature="not directly configurable; internal value unknown",
                max_output_tokens="not directly configurable; internal value unknown",
                timeout_seconds=TIMEOUT, response_schema=None)


def payload(system, user):
    return dict(instructions=INSTRUCTION, system=system, user=user)


def inspect_events(stdout):
    completed = []
    replies = []
    for line in stdout.splitlines():
        event = json.loads(line)
        if event.get("type") in ("error", "turn.failed"):
            raise RuntimeError("Codex reported error or failed turn; see raw events")
        item = event.get("item", {})
        if item and item.get("type") not in ("agent_message", "reasoning"):
            raise RuntimeError("native tool action detected; run invalid")
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            replies.append(item.get("text"))
        if event.get("type") == "turn.completed":
            completed.append(event.get("usage", {}))
    if len(completed) != 1 or not replies or not isinstance(replies[-1], str):
        raise RuntimeError("expected one completed turn with final text")
    usage = completed[0]
    for key in ("input_tokens", "output_tokens"):
        if type(usage.get(key)) is not int or usage[key] < 0:
            raise RuntimeError("missing or invalid token usage")
    return usage, replies[-1]


def complete(system, user, emit):
    request = payload(system, user)
    emit("model_input", payload=request)
    with tempfile.TemporaryDirectory(prefix="week03-codex-") as directory:
        output = Path(directory) / "reply.txt"
        command = [executable(), "exec", "--ignore-user-config", "--ephemeral",
                   "--sandbox", "read-only", "--skip-git-repo-check", "--cd", directory,
                   "--model", MODEL, "--output-last-message", str(output),
                   "--json", "--color", "never", "-c", 'web_search="disabled"',
                   "-c", 'model_reasoning_effort="{}"'.format(REASONING)]
        for feature in DISABLED:
            command.extend(["--disable", feature])
        command.append("-")
        emit("model_command", argv=command)
        try:
            result = subprocess.run(command, input=json.dumps(request), text=True,
                                    capture_output=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            for stream, data in (("stdout", exc.stdout), ("stderr", exc.stderr)):
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")
                emit("model_timeout", stream=stream, raw=data or "")
            raise
        emit("model_output", stdout=result.stdout, stderr=result.stderr,
             returncode=result.returncode)
        if result.returncode:
            raise RuntimeError("Codex exit status {}".format(result.returncode))
        usage, event_text = inspect_events(result.stdout)
        raw = output.read_text(encoding="utf-8")
        emit("model_response", raw=raw, usage=usage)
        if raw.strip() != event_text.strip():
            raise RuntimeError("last-message file differs from raw event text")
        return raw, usage
