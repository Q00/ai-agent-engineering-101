"""One call to a coding harness in non-interactive mode: prompt on stdin, answer on stdout.

    HARNESS_CMD   the shell command to run (default below: Claude Code, haiku, no tools)

Checked commands (2026-10-13):
    claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence
    codex exec --skip-git-repo-check -

The harness runs in an empty temporary directory so it cannot pick up a project's
CLAUDE.md or AGENTS.md, and the buyer and seller never share a session.
"""
import json
import os
import re
import subprocess
import tempfile

DEFAULT_CMD = 'claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence'
CMD = os.environ.get("HARNESS_CMD", DEFAULT_CMD)
_CWD = tempfile.mkdtemp(prefix="harness-")


def ask(prompt: str, timeout: int = 180) -> str:
    out = subprocess.run(CMD, shell=True, input=prompt, capture_output=True, text=True,
                         cwd=_CWD, timeout=timeout, env=dict(os.environ, MAX_THINKING_TOKENS="0"))
    if out.returncode != 0:
        raise RuntimeError(f"harness exit {out.returncode}: {out.stderr.strip()[:200]}")
    return out.stdout.strip()


def ask_json(prompt: str) -> tuple[dict | None, str]:
    """The first {...} object in the answer, and the raw answer for the log."""
    raw = ask(prompt)
    m = re.search(r"\{.*?\}", raw, re.S)
    if not m:
        return None, raw
    try:
        return json.loads(m.group()), raw
    except json.JSONDecodeError:
        return None, raw


# ---------------------------------------------------------------- the same harness with MCP tools (Round 3 buyer)
TOOLS_PY = os.environ.get("TOOLS_PYTHON", "/home/jj275/miniconda3/envs/kms/bin/python")   # has the MCP SDK 2.x
TOOL_NAMES = ["mcp__market__shop_record", "mcp__market__shop_reviews", "mcp__market__sku_market",
              "mcp__market__forum_search"]


def ask_json_tools(prompt: str, env: dict, timeout: int = 240) -> tuple[dict | None, str]:
    """Like ask_json, but the model may call the tools in market_tools_mcp.py (stdio MCP) before it answers.
    Built-in tools stay off; only these four are allowed. Falls back to ask_json if the tool run fails."""
    here = os.path.dirname(os.path.abspath(__file__))
    cfg = {"mcpServers": {"market": {"command": TOOLS_PY, "args": [os.path.join(here, "market_tools_mcp.py")],
                                     "env": {k: str(v) for k, v in env.items()}}}}
    fd, path = tempfile.mkstemp(suffix=".json", prefix="mcp-")
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)
    cmd = (CMD + f" --mcp-config {path} --strict-mcp-config --allowedTools " + " ".join(TOOL_NAMES))
    try:
        out = subprocess.run(cmd, shell=True, input=prompt, capture_output=True, text=True, cwd=_CWD,
                             timeout=timeout, env=dict(os.environ, MAX_THINKING_TOKENS="0"))
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip()[:200])
        raw = out.stdout.strip()
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        print(f"[harness] tool run failed ({type(e).__name__}), asking without tools", flush=True)
        try:
            return ask_json(prompt)
        except Exception as e2:                  # noqa: BLE001
            return None, f"[harness failed] {type(e2).__name__}: {str(e2)[:150]}"
    finally:
        os.unlink(path)
    m = re.search(r"\{.*?\}", raw, re.S)
    try:
        return (json.loads(m.group()) if m else None), raw
    except json.JSONDecodeError:
        return None, raw


def safe_ask_json(prompt: str, tools_env: dict | None = None) -> tuple[dict | None, str]:
    """Never raises: a failed harness run (rate limit, login, timeout) returns (None, "[harness failed] ...") so the
    caller falls back to a code decision instead of passing our own error on to the other agent."""
    try:
        return ask_json_tools(prompt, tools_env) if tools_env is not None else ask_json(prompt)
    except Exception as e:                       # noqa: BLE001 - any failure of our own model call
        return None, f"[harness failed] {type(e).__name__}: {str(e)[:150]}"
