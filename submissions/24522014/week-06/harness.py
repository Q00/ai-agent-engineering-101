"""STEP 2: call a harness and read a decision-shaped JSON object, without trading."""

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4


ROOT = Path(__file__).resolve().parent
MODELS = {"claude": "haiku", "codex": "gpt-6-luna"}


class HarnessError(RuntimeError):
    """A failed invocation, with its original output preserved in logs."""


def select_harness(mode, role, seller="claude", buyer="codex"):
    """The mixed mode also supports the reverse pairing through its arguments."""
    if mode not in ("claude", "codex", "mixed") or role not in ("seller", "buyer"):
        raise ValueError("Unknown mode or role")
    provider = mode if mode != "mixed" else {"seller": seller, "buyer": buyer}[role]
    if provider not in MODELS:
        raise ValueError("Unknown harness")
    return provider


def parse_response(text):
    """Check the common shape; role, reserve and budget checks come in later steps."""
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {"action", "price", "reason"}:
        raise ValueError("Expected exactly action, price, reason in one JSON object")
    action = data["action"]
    if action not in ("ask", "accept", "reject", "offer", "walk_away"):
        raise ValueError("Unknown action")
    if not isinstance(data["reason"], str) or not data["reason"].strip():
        raise ValueError("reason must be a nonempty string")
    price = data["price"]
    if action in ("reject", "walk_away"):
        if price is not None:
            raise ValueError("reject/walk_away must have a null price")
    elif type(price) is not int or price <= 0:
        raise ValueError("ask/accept/offer must have a positive integer price")
    return data


def build_command(provider):
    if provider == "claude":
        executable = shutil.which("claude")
        if not executable:
            raise FileNotFoundError("Claude Code not found in PATH")
        return [executable, "-p", "--model", "haiku", "--tools", "",
                "--setting-sources", "", "--no-session-persistence"]
    if provider != "codex":
        raise ValueError("Unknown harness")
    # Use the installed native binary on Windows; do not interpolate a shell command.
    executable = shutil.which("codex.exe" if os.name == "nt" else "codex")
    if not executable and os.name == "nt":
        shim = shutil.which("codex.cmd")
        if shim:
            candidate = (Path(shim).parent / "node_modules/@openai/codex/node_modules"
                         / "@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe")
            if candidate.is_file():
                executable = str(candidate)
    if not executable:
        raise FileNotFoundError("Native Codex CLI not found; check the Codex installation")
    return [executable, "exec", "--skip-git-repo-check", "--model", "gpt-6-luna",
            "--sandbox", "read-only", "--ephemeral", "--ignore-user-config",
            "-c", "project_doc_max_bytes=0", "-"]


def run_harness(provider, prompt, timeout=90):
    """Send UTF-8 stdin, save unedited output, return parsed JSON or raise HarnessError."""
    if provider not in MODELS or timeout <= 0:
        raise ValueError("Unknown harness or nonpositive timeout")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    log_dir = ROOT / "logs/harness" / (run_id + "-" + provider)
    log_dir.mkdir(parents=True)
    work_dir = ROOT / ".harness-work" / run_id
    work_dir.mkdir(parents=True)
    (log_dir / "request.txt").write_text(prompt, encoding="utf-8")
    metadata = {"provider": provider, "model": MODELS[provider], "cwd": str(work_dir),
                "timeout_seconds": timeout, "status": "running"}
    started = time.monotonic()
    process = None
    try:
        command = build_command(provider)
        metadata["command"] = command
        with (log_dir / "stdout.txt").open("wb") as stdout, (log_dir / "stderr.txt").open("wb") as stderr:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                       cwd=work_dir, start_new_session=(os.name != "nt"))
            try:
                process.communicate(prompt.encode("utf-8"), timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                if process.poll() is None:
                    process.kill()
                process.communicate()
                raise TimeoutError(f"Harness exceeded {timeout} seconds")
        metadata["returncode"] = process.returncode
        if process.returncode != 0:
            raise RuntimeError(f"Harness exited with code {process.returncode}; see stderr.txt")
        response = parse_response((log_dir / "stdout.txt").read_text(encoding="utf-8"))
        (log_dir / "result.json").write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
        metadata["status"] = "ok"
        return response
    except (OSError, ValueError, RuntimeError) as error:
        metadata["status"] = "failed"
        metadata["error"] = str(error)
        raise HarnessError(f"{error}\nLogs: {log_dir}") from error
    finally:
        metadata["elapsed_seconds"] = round(time.monotonic() - started, 3)
        if process is not None:
            metadata["returncode"] = process.returncode
        (log_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Harness logs: {log_dir}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["claude", "codex", "mixed"], required=True)
    parser.add_argument("--role", choices=["seller", "buyer"], default="seller")
    parser.add_argument("--seller-harness", choices=list(MODELS), default="claude")
    parser.add_argument("--buyer-harness", choices=list(MODELS), default="codex")
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()
    provider = select_harness(args.mode, args.role, args.seller_harness, args.buyer_harness)
    # This verifies transport and parsing only. No negotiation strategy is supplied here.
    expected = {"action": "ask" if args.role == "seller" else "offer", "price": 110,
                "reason": "transport smoke test"}
    prompt = ("Do not use any tools or read files. This is a transport smoke test, not a trade. "
              "Return exactly this single JSON object, without Markdown or other text:\n"
              + json.dumps(expected))
    try:
        response = run_harness(provider, prompt, timeout=args.timeout)
    except HarnessError as error:
        parser.exit(1, str(error) + "\n")
    if response != expected:
        parser.exit(1, "Smoke test failed: JSON was valid but differed from the requested object.\n")
    print(json.dumps({"mode": args.mode, "role": args.role, "harness": provider,
                      "model": MODELS[provider], "response": response}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
