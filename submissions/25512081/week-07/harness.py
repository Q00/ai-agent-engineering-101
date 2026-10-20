"""The harness: Claude Code, called headless, one decision per call.

    claude -p --model opus --tools "" --setting-sources "" --no-session-persistence \
           --output-format json --json-schema <schema> --system-prompt <role>

- The prompt goes on stdin; the answer must match a JSON Schema, so the CLI
  returns it already parsed in `structured_output` (no fenced-JSON parsing).
- It runs in an empty temporary directory, so it never picks up this
  repository's CLAUDE.md / AGENTS.md.
- ANTHROPIC_API_KEY is removed from its environment, so the call always uses
  the claude.ai subscription login, never metered API billing.

HARNESS_MODEL overrides the model (default: opus).
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


def ask(system, prompt, schema, log=None, timeout=150, retries=2):
    """One harness call -> dict matching `schema`. Raises after `retries` failures."""
    cmd = ["claude", "-p", "--model", MODEL, "--tools", "", "--setting-sources", "",
           "--no-session-persistence", "--output-format", "json",
           "--json-schema", json.dumps(schema), "--system-prompt", system]
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
            if p.returncode == 0 and isinstance(answer, dict):
                if log:
                    log(f"    [harness {dt:.1f}s] {json.dumps(answer)}")
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
