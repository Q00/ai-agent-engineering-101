"""Send a few past buyer prompts to an agy model and compare with what the logged model decided. No market contact."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["action", "price", "reason"],
          "properties": {"action": {"type": "string", "enum": ["offer", "walk_away"]}, "price": {"type": ["integer", "null"]}, "reason": {"type": "string"}}}


def executable():
    found = shutil.which("agy") or shutil.which("agy.exe")
    candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "agy/bin/agy.exe"
    return found or (str(candidate) if candidate.is_file() else None)


def past_prompts(limit):
    """Recent buyer prompts with different quotes: ones whose logged model made an offer, and ones it walked from."""
    rows = []
    for name in sorted(os.listdir(ROOT / "logs/harness"), reverse=True):
        path = ROOT / "logs/harness" / name
        try:
            prompt = (path / "request.txt").read_text(encoding="utf-8")
            result = json.loads((path / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if "Round 3 buyer" in prompt[:90] and re.search(r"[가-힣]", prompt):
            rows.append((name, prompt, result))
        if len(rows) >= limit * 8:
            break
    picked, seen = [], set()
    for row in rows:
        key = row[2]["action"]
        if sum(p[2]["action"] == key for p in picked) < (limit + 1) // 2:
            picked.append(row)
    return picked[:limit]


def call(agy, prompt, model, use_schema):
    with tempfile.TemporaryDirectory(dir=ROOT / ".harness-work") as work:
        env = dict(os.environ, TEMP=work, TMP=work)
        command = [agy, "--print", prompt, "--model", model, "--output-format", "json", "--sandbox", "--disable-slash-commands", "--print-timeout", "70s"]
        if use_schema:
            command += ["--json-schema", json.dumps(SCHEMA)]
        command += ["--settings", json.dumps({"permissions": {"deny": ["read_file(*)", "write_file(*)", "command(*)", "read_url(*)", "execute_url(*)", "mcp(*)"]}})]
        started = time.monotonic()
        done = subprocess.run(command, cwd=work, stdin=subprocess.DEVNULL, capture_output=True, timeout=100, env=env)
        return done, round(time.monotonic() - started, 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gemini-3.8-flash-low")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--no-schema", action="store_true")
    args = parser.parse_args()
    agy = executable()
    rows = []
    for name, prompt, logged in past_prompts(min(args.limit, 5)):
        context = re.search(r'"budget": (\d+), "current_ask": ([\d.a-z]+)', prompt)
        done, elapsed = call(agy, prompt, args.model, not args.no_schema)
        output = {}
        try:
            output = json.loads(done.stdout.decode("utf-8", "replace"))
        except ValueError:
            pass
        answer = output.get("structured_output") or output.get("response")
        decision = answer
        if isinstance(answer, str):
            try:
                decision = json.loads(answer.strip())
            except ValueError:
                decision = None
        rows.append({"harness_log": name, "budget": context[1] if context else None, "ask": context[2] if context else None,
                     "logged": {k: logged.get(k) for k in ("action", "price")}, "agy": {k: (decision or {}).get(k) for k in ("action", "price", "reason")} if isinstance(decision, dict) else None,
                     "raw_response_head": str(output.get("response"))[:140], "output_keys": sorted(output), "status": output.get("status"), "finished_reason": output.get("finished_reason"),
                     "returncode": done.returncode, "elapsed": elapsed, "tokens": (output.get("usage") or {}).get("total_tokens"), "denied_actions": output.get("denied_actions"),
                     "stderr_head": done.stderr.decode("utf-8", "replace")[:160], "prompt_chars": len(prompt)})
    print(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "model": args.model, "schema": not args.no_schema, "rows": rows}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
