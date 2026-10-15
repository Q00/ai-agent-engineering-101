"""Explicit same-model benchmark transport, never the submission's default harness.

Peer source files stay unchanged. This adapter substitutes their inference
provider with real Codex gpt-6-sol/low and preserves original request text in
exclusive per-call artifacts. It does not validate, repair, or choose prices.
Only the benchmark runner should prepend benchmark_bin to a peer's PATH.
"""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from inference_queue import inference_gap_seconds, inference_slot


MODEL = "gpt-6-sol"
EFFORT = "low"
CALL_TIMEOUT = 40.0
MODE = "controlled_same_model_rehost_not_original_provider"
BOUNDARY = (
    "You are the inference transport for a controlled negotiation benchmark. "
    "Answer the supplied original model request directly. Do not use tools, "
    "shell, files, apps, network browsing, or subagents. Emit only the final "
    "answer requested by the original prompts, without transport commentary. "
    "The transport supplies no negotiation strategy or replacement prices."
)
_active: set[subprocess.Popen] = set()
_active_lock = threading.Lock()


class TransportError(RuntimeError):
    pass


def _write(path: Path, value: str | dict | list) -> None:
    """Never replace a prior request or failed attempt's evidence."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=3)
    except ProcessLookupError:
        pass
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)


def terminate_children() -> None:
    with _active_lock:
        processes = list(_active)
    for process in processes:
        _stop(process)


def _configuration(codex=None, trace_dir=None):
    supplied = codex or os.environ.get("A2A_BENCH_CODEX")
    if not supplied or not Path(supplied).is_absolute():
        raise TransportError("A2A_BENCH_CODEX must name the absolute native Codex binary")
    binary = Path(supplied).resolve()
    shim_dir = Path(__file__).resolve().parent / "benchmark_bin"
    if not binary.is_file() or not os.access(binary, os.X_OK) or binary.is_relative_to(shim_dir):
        raise TransportError("Configured Codex binary is missing, not executable, or points to a benchmark shim")
    supplied_trace = trace_dir or os.environ.get("A2A_BENCH_TRACE_DIR")
    if not supplied_trace or not Path(supplied_trace).is_absolute():
        raise TransportError("A2A_BENCH_TRACE_DIR must name an absolute benchmark artifact directory")
    directory = Path(supplied_trace)
    directory.mkdir(parents=True, exist_ok=True)
    return binary, directory


def render_request(messages):
    """Keep message role, order, and content intact in a recorded transport envelope."""
    if not isinstance(messages, list) or not messages:
        raise TransportError("messages must be a nonempty list")
    for message in messages:
        if (not isinstance(message, dict) or message.get("role") not in
                {"system", "developer", "user", "assistant"} or not isinstance(message.get("content"), str)):
            raise TransportError("Only text messages with explicit roles are supported by this benchmark")
    return BOUNDARY + "\n\nOriginal messages, in order (JSON preserves their exact text):\n" + json.dumps(
        messages, ensure_ascii=False)


def invoke(messages, *, source, original_request, codex=None, trace_dir=None):
    """One real inference call, with the same configured FIFO as our agent.

    There are no added retries, schemas, or semantic guards. Peer code retains
    its own retry and validation behavior. A failed call stays a failed call.
    """
    gap = inference_gap_seconds()
    binary, directory = _configuration(codex, trace_dir)
    call_id = uuid.uuid4().hex
    artifact = directory / call_id
    artifact.mkdir()
    started = time.monotonic()
    metadata = {"id": call_id, "mode": MODE, "model": MODEL, "reasoning_effort": EFFORT,
                "source": source, "participant": os.environ.get("A2A_BENCH_PARTICIPANT"),
                "time": datetime.now(timezone.utc).isoformat(), "pid": os.getpid(),
                "original_request": original_request, "boundary_instruction": BOUNDARY,
                "call_timeout_seconds": CALL_TIMEOUT, "queue_interval_seconds": gap}
    _write(artifact / "request.json", metadata)
    process = None
    stdout = stderr = ""
    final = ""
    outcome = {"id": call_id, "mode": MODE, "status": "failed"}
    try:
        prompt = render_request(messages)
        _write(artifact / "prompt.txt", prompt)
        wait_seconds = float(os.environ.get("A2A_BENCH_QUEUE_WAIT_SECONDS", "180"))
        if not 0 < wait_seconds <= 300:
            raise TransportError("Benchmark queue wait must be positive and at most 300 seconds")
        with tempfile.TemporaryDirectory(prefix="a2a-bench-codex-") as empty:
            output = Path(empty) / "final.txt"
            command = [str(binary), "exec", "--skip-git-repo-check", "--ephemeral",
                       "--ignore-user-config", "--sandbox", "read-only", "--color", "never",
                       "--model", MODEL, "-c", f'model_reasoning_effort="{EFFORT}"',
                       "--output-last-message", str(output), "-"]
            _write(artifact / "execution.json", {"command": command, "cwd": empty,
                    "environment_recorded": False, "source_cwd": os.getcwd()})
            with inference_slot(time.monotonic() + wait_seconds) as ticket:
                _write(artifact / "admission.json", {"ticket": ticket,
                        "waited_seconds": round(time.monotonic() - started, 6)})
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, text=True, cwd=empty,
                                           start_new_session=True)
                with _active_lock:
                    _active.add(process)
                _write(artifact / "process.json", {"pid": process.pid})
                try:
                    stdout, stderr = process.communicate(prompt, timeout=CALL_TIMEOUT)
                except subprocess.TimeoutExpired:
                    _stop(process)
                    stdout, stderr = process.communicate()
                    raise TransportError("Native Codex inference exceeded the 40-second call limit")
                if output.exists():
                    final = output.read_text(encoding="utf-8")
                if process.returncode:
                    raise TransportError(f"Native Codex exited with code {process.returncode}; see raw stderr")
                if not final.strip():
                    raise TransportError("Native Codex did not write a final answer")
                outcome.update(status="completed", exit_code=process.returncode)
    except BaseException as exc:
        outcome.update(error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        if process is not None:
            _stop(process)
            with _active_lock:
                _active.discard(process)
            outcome["exit_code"] = process.returncode
        for name, content in (("stdout.txt", stdout), ("stderr.txt", stderr), ("final.txt", final)):
            _write(artifact / name, content)
        outcome["elapsed_seconds"] = round(time.monotonic() - started, 6)
        _write(artifact / "result.json", outcome)
    return final, call_id


def parse_cli(kind, argv, stdin):
    """Accept only the peer CLIs' supported text flags; record every removed flag."""
    if kind not in {"claude", "codex"}:
        raise TransportError("Unknown benchmark CLI")
    if kind == "codex":
        if not argv or argv[0] != "exec":
            raise TransportError("The benchmark Codex shim supports only exec")
        args = argv[1:]
        value_flags = {"--model", "-m", "--color"}
        bool_flags = {"--skip-git-repo-check", "--ephemeral", "--ignore-user-config"}
    else:
        args = argv
        value_flags = {"--model", "-m", "--tools", "--setting-sources", "--system-prompt"}
        bool_flags = {"-p", "--print", "--no-session-persistence", "--strict-mcp-config",
                      "--disable-slash-commands"}
    removed, system, positional = [], [], []
    index = 0
    while index < len(args):
        arg = args[index]
        flag, separator, inline_value = arg.partition("=")
        if flag in value_flags:
            if separator:
                value = inline_value
            else:
                index += 1
                if index >= len(args):
                    raise TransportError(f"Missing value for {flag}")
                value = args[index]
            if flag == "--system-prompt":
                system.append(value)
            removed.append({"flag": flag, "value": value,
                            "reason": "forwarded as original system message" if flag == "--system-prompt"
                            else "source runtime flag replaced by controlled Codex execution"})
        elif arg in bool_flags:
            removed.append({"flag": arg, "reason": "source runtime flag replaced by controlled Codex execution"})
        elif arg == "-":
            removed.append({"flag": arg, "reason": "original stdin retained"})
        elif arg.startswith("-"):
            raise TransportError(f"Unsupported source CLI flag {arg}; refusing to guess its meaning")
        else:
            positional.append(arg)
        index += 1
    if len(positional) > 1:
        raise TransportError("More than one source prompt argument is not supported")
    messages = [{"role": "system", "content": value} for value in system]
    if positional:
        messages.append({"role": "user", "content": positional[0]})
    if stdin or not positional:
        messages.append({"role": "user", "content": stdin})
    return messages, {"argv": [kind, *argv], "removed_flags": removed, "stdin": stdin,
                      "positional_prompt": positional[0] if positional else None}


def cli_main(kind, argv=None):
    def interrupted(signum, _frame):
        terminate_children()
        raise SystemExit(128 + signum)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, interrupted)
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        messages, original = parse_cli(kind, argv, sys.stdin.read())
        final, _ = invoke(messages, source=f"{kind}-cli", original_request=original)
        sys.stdout.write(final)
        sys.stdout.flush()
        return 0
    except Exception as exc:
        print(f"Benchmark transport error: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


def create_app(*, codex=None, trace_dir=None):
    from starlette.applications import Starlette
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    # Fail before opening a listening socket if the benchmark configuration is wrong.
    binary, artifacts = _configuration(codex, trace_dir)

    async def health(_request):
        return JSONResponse({"status": "ok", "mode": MODE, "model": MODEL,
                             "reasoning_effort": EFFORT})

    async def completions(request):
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise TransportError("Chat request must be an object")
            messages = payload.get("messages")
            render_request(messages)
            if payload.get("stream"):
                raise TransportError("Streaming is not supported by this benchmark")
            if payload.get("tools") or payload.get("functions"):
                raise TransportError("Tool-call requests are not supported by this text-only benchmark")
        except Exception as exc:
            return JSONResponse({"error": {"type": "invalid_request_error", "message": str(exc)}}, status_code=400)
        try:
            final, call_id = await run_in_threadpool(invoke, messages, source="openai-compatible-http",
                original_request={"body": payload, "ignored_generation_fields": sorted(
                    key for key in payload if key not in {"messages", "stream"}),
                    "headers_recorded": False}, codex=binary, trace_dir=artifacts)
        except Exception as exc:
            return JSONResponse({"error": {"type": "benchmark_inference_error",
                "message": f"{type(exc).__name__}: {exc}"}}, status_code=502)
        return JSONResponse({"id": "chatcmpl-" + call_id, "object": "chat.completion",
            "created": int(time.time()), "model": MODEL,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": final},
                         "finish_reason": "stop"}]})

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            terminate_children()

    return Starlette(routes=[Route("/health", health),
        Route("/v1/chat/completions", completions, methods=["POST"])], lifespan=lifespan)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    server = commands.add_parser("serve")
    server.add_argument("--port", required=True, type=int)
    server.add_argument("--codex")
    server.add_argument("--trace-dir")
    args = parser.parse_args(argv)
    import uvicorn
    uvicorn.run(create_app(codex=args.codex, trace_dir=args.trace_dir),
                host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
