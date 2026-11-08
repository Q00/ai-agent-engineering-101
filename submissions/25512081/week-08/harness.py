"""The harness: Claude Code, called headless, one decision per call.

    claude -p --model opus --tools "" --setting-sources "" --no-session-persistence \
           --output-format json --json-schema <schema> --system-prompt <role>

- The prompt goes on stdin; the answer must match a JSON Schema, so the CLI
  returns it already parsed in `structured_output` (no fenced-JSON parsing).
- It runs in an empty temporary directory, so it never picks up this
  repository's CLAUDE.md / AGENTS.md.
- ANTHROPIC_API_KEY is removed from its environment, so the call always uses
  the claude.ai subscription login, never metered API billing.

HARNESS_MODEL overrides the model (default: opus). ask(..., model=) picks one per call: the fast model for
per-message work a buyer waits on and for reading untrusted reviews, the cheap model for short writing whose
output code checks anyway (forum posts, lessons), opus for the shop manager's planning.
Every call's cost (the API price the CLI reports; the subscription is not billed in money but its limits
follow usage) and tokens are logged and appended to logs/usage.jsonl, so the report can show what each role
costs.
ask(..., tools=) is the same call with tools: MCP servers from a config file, only the named tools allowed,
several turns, the structured answer at the end.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

MODEL = os.environ.get("HARNESS_MODEL", "opus")
_WORKDIR = tempfile.mkdtemp(prefix="harness-")
_ENV = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
CALLS = {"n": 0, "seconds": 0.0}


FAST = os.environ.get("HARNESS_FAST_MODEL", "sonnet")
CHEAP = os.environ.get("HARNESS_CHEAP_MODEL", "haiku")
USAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "usage.jsonl")


def _usage(model, role, out, dt):
    u = out.get("usage") or {}
    tokens_in = sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    row = {"ts": time.strftime("%m-%d %H:%M:%S"), "role": role, "model": model, "cost_usd": out.get("total_cost_usd"),
           "tokens_in": tokens_in, "tokens_out": u.get("output_tokens"), "turns": out.get("num_turns"),
           "seconds": round(dt, 1)}
    try:
        os.makedirs(os.path.dirname(USAGE), exist_ok=True)
        with open(USAGE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass
    return row


def ask(system, prompt, schema, log=None, timeout=150, retries=2, model=None, tools=None, role=None, effort=None):
    """One harness call -> dict matching `schema`. Raises after `retries` failures.
    tools = (mcp config path, [allowed tool names]) lets the model call those MCP tools before answering.
    effort = the CLI's --effort level (low ... max); None keeps the CLI default."""
    cmd = ["claude", "-p", "--model", model or MODEL, "--tools", "", "--setting-sources", "",
           "--no-session-persistence", "--output-format", "json",
           "--json-schema", json.dumps(schema), "--system-prompt", system]
    if effort:
        cmd += ["--effort", effort]
    if tools:
        cmd += ["--mcp-config", tools[0], "--strict-mcp-config", "--allowedTools", ",".join(tools[1])]
    err = None
    for attempt in range(retries + 1):
        t0 = time.time()
        try:
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                               cwd=_WORKDIR, env=_ENV, timeout=timeout)
            dt = time.time() - t0
            CALLS["n"] += 1
            CALLS["seconds"] += dt
            out = json.loads(p.stdout) if p.stdout.strip() else {}
            answer = out.get("structured_output")
            use = _usage(model or MODEL, role or os.path.basename(sys.argv[0]), out, dt)
            if p.returncode == 0 and isinstance(answer, dict):
                if log:
                    turns = f", {out.get('num_turns')} turns" if tools else ""
                    log(f"    [harness {dt:.1f}s{turns}, {model or MODEL} ${use['cost_usd'] or 0:.4f} "
                        f"{use['tokens_in']}+{use['tokens_out']} tok] {json.dumps(answer, ensure_ascii=False)}")
                return answer
            err = f"exit={p.returncode} is_error={out.get('is_error')} " \
                  f"{(out.get('result') or p.stderr or '')[:200]!r}"
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            err = f"{type(e).__name__}: {str(e)[:200]}"
        if log:
            log(f"    [harness attempt {attempt + 1} failed] {err}")
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"harness failed: {err}")


if __name__ == "__main__":                       # quick manual check
    schema = {"type": "object", "properties": {"reply": {"type": "string"}},
              "required": ["reply"]}
    print(ask("Answer briefly.", " ".join(sys.argv[1:]) or "Say hi.", schema, log=print))
