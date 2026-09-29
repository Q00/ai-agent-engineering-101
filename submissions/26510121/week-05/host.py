"""One isolated Codex turn. Secrets go through the environment only."""
import json
import os
from pathlib import Path
from queue import Queue, Empty
import shutil
import subprocess
from tempfile import mkdtemp
from threading import Thread
import time


def codex_executable():
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@openai/codex/node_modules"
        matches = list(base.glob("@openai/codex-win32-*/vendor/*/bin/codex.exe"))
        if matches:
            return str(matches[0])
    value = shutil.which("codex")
    if not value:
        raise RuntimeError("Codex CLI is not installed or on PATH")
    return value


def run_turn(*, config, prompt, token, mcp_url, root, emit, budget_used, timeout=180):
    runtime = root / ".runtime"
    runtime.mkdir(exist_ok=True)
    work = Path(mkdtemp(prefix="host-", dir=runtime)).resolve()
    instructions = work / "instructions.md"
    instructions.write_text(prompt + "\n", encoding="utf-8")
    settings = {
        "model_reasoning_effort": config["reasoning_effort"],
        "model_instructions_file": str(instructions),
        "project_doc_max_bytes": 0, "features.shell_tool": False,
        "web_search": "disabled", "mcp_servers.market.url": mcp_url,
        "mcp_servers.market.bearer_token_env_var": "MARKET_PARTY_TOKEN",
        "mcp_servers.market.default_tools_approval_mode": "approve",
        "mcp_servers.market.enabled_tools": ["get_negotiation", "propose", "accept_proposal", "reject_proposal", "refuse"],
    }
    command = [codex_executable(), "exec", "--json", "--ephemeral", "--ignore-user-config",
               "--sandbox", "read-only", "--skip-git-repo-check", "-C", str(work), "-m", config["model"]]
    for key, value in settings.items():
        command += ["-c", key + "=" + json.dumps(value, ensure_ascii=False)]
    command.append("-")
    env = os.environ.copy()
    for key in list(env):
        if key.upper().endswith(("API_KEY", "ADMIN_TOKEN", "PARTY_TOKEN")):
            env.pop(key)
    env["MARKET_PARTY_TOKEN"] = token
    emit({"kind": "host_start", "model": config["model"], "instructions": prompt,
          "isolation": "ephemeral, empty cwd, read-only, ignore user config, shell disabled"})
    process = subprocess.Popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    process.stdin.write("Perform your negotiation turn using the market tools.\n")
    process.stdin.close()
    queue = Queue()
    def read(stream, label):
        for line in stream:
            queue.put((label, line.rstrip("\r\n")))
        queue.put((label, None))
    for stream, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
        Thread(target=read, args=(stream, label), daemon=True).start()
    deadline, ended, stop, completed = time.monotonic() + timeout, 0, None, False
    try:
        while ended < 2:
            if process.poll() is None and (time.monotonic() >= deadline or budget_used() >= 8):
                stop = "timeout" if time.monotonic() >= deadline else "tool_budget"
                process.kill()
            try:
                stream, line = queue.get(timeout=0.1)
            except Empty:
                continue
            if line is None:
                ended += 1
                continue
            line = line.replace(token, "[REDACTED_PARTY_TOKEN]")
            if stream == "stdout":
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    event = {"raw": line}
                completed |= event.get("type") == "turn.completed"
                emit({"kind": "codex_event", "event": event})
            else:
                emit({"kind": "codex_stderr", "raw": line})
        code = process.wait(timeout=5)
        emit({"kind": "host_end", "returncode": code, "stop": stop, "turn_completed": completed})
        if stop == "timeout":
            raise RuntimeError(f"Codex turn timed out after {timeout}s")
        if stop != "tool_budget" and (code != 0 or not completed):
            raise RuntimeError(f"Codex failed (exit {code}, completed={completed}); see raw log")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        # Ignored runtime contains no token and stays available to inspect setup.
