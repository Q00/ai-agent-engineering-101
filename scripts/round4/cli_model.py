"""cli_model: your own subscription CLI as the agent's model.

The kit calls an OpenAI-compatible /chat/completions. This serves one on your own computer and answers each call by
running a CLI you are logged in to (Claude Code, Codex, or any command), so the agent thinks on your subscription and
no API key is involved.

    python cli_model.py --cli claude                 # runs `claude -p` (add --model haiku to pick a model)
    python cli_model.py --cli codex                  # runs `codex exec`
    python cli_model.py --cmd "my-llm --flag"        # any command: the prompt on stdin, the answer on stdout

then, in another terminal:

    AGENT_LLM_BASE=http://127.0.0.1:9000/v1 AGENT_LLM_MODEL=cli AGENT_LLM_KEY=local python agent_kit.py run ...

It listens on 127.0.0.1 only. Each call starts the CLI once, with its own tools off and in an empty directory: the
CLI is the agent's mind here, and the agent acts only through the kit. Expect a call to take longer than an API call.
"""
import argparse
import http.server
import json
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

TIMEOUT = 170                                          # the kit gives a call 180 s
GATE = threading.Semaphore(2)
ARGS = None


def flatten(messages):
    """(system text, the rest as one prompt). The CLI gets the conversation so far and writes the next message."""
    system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
    turns = [m for m in messages if m.get("role") != "system"]
    if len(turns) == 1:
        return system, turns[0]["content"]
    body = "\n\n".join(f"<{m['role']}>\n{m['content']}\n</{m['role']}>" for m in turns)
    return system, ("The conversation so far, oldest first:\n\n" + body
                    + "\n\nWrite the assistant's next message only, in the format the instructions ask for.")


def run_cli(system, prompt):
    work = Path(tempfile.mkdtemp(prefix="cli-model-"))
    try:
        if ARGS.cli == "claude":
            cmd = ["claude", "-p", "--output-format", "text", "--no-session-persistence", "--disable-slash-commands",
                   "--strict-mcp-config", "--setting-sources", "", "--system-prompt", system or "Answer the user."]
            if ARGS.model:
                cmd += ["--model", ARGS.model]
            cmd += ["--tools", ""]                      # last: it takes every argument after it
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=TIMEOUT, cwd=work)
            out = r.stdout
        elif ARGS.cli == "codex":
            last = work / "last.txt"
            cmd = ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "--color", "never",
                   "-C", str(work), "-o", str(last)]
            if ARGS.model:
                cmd += ["-m", ARGS.model]
            if ARGS.effort:
                cmd += ["-c", f"model_reasoning_effort={ARGS.effort}"]
            text = (f"<instructions>\n{system}\n</instructions>\n\n" if system else "") + prompt + \
                "\n\nAnswer with the message only. Do not run commands or read files."
            r = subprocess.run(cmd + ["-"], input=text, capture_output=True, text=True, timeout=TIMEOUT, cwd=work)
            out = last.read_text(encoding="utf-8") if last.exists() else r.stdout
        else:
            text = (system + "\n\n" if system else "") + prompt
            r = subprocess.run(shlex.split(ARGS.cmd), input=text, capture_output=True, text=True, timeout=TIMEOUT, cwd=work)
            out = r.stdout
        if r.returncode != 0 and not out.strip():
            raise RuntimeError(f"the CLI exited {r.returncode}: {(r.stderr or '').strip()[-400:]}")
        return out.strip()
    finally:
        shutil.rmtree(work, ignore_errors=True)


class Handler(http.server.BaseHTTPRequestHandler):
    def send(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self.send(404, {"error": {"message": "only /v1/chat/completions"}})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)))
            system, prompt = flatten(body["messages"])
        except Exception:
            return self.send(400, {"error": {"message": "send {messages: [{role, content}]}"}})
        t0 = time.time()
        try:
            with GATE:
                text = run_cli(system, prompt)
        except subprocess.TimeoutExpired:
            return self.send(504, {"error": {"message": f"the CLI did not answer within {TIMEOUT} s"}})
        except (RuntimeError, OSError) as e:
            print(f"[cli_model] failed: {e}", flush=True)
            return self.send(502, {"error": {"message": str(e)[:500]}})
        print(f"[cli_model] {len(prompt) + len(system)} chars in, {len(text)} out, {time.time() - t0:.1f} s", flush=True)
        self.send(200, {"id": "cli", "object": "chat.completion", "created": int(time.time()), "model": ARGS.cli or "cmd",
                        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
                        "usage": {}})

    def log_message(self, *a):
        pass


def main():
    global ARGS
    ap = argparse.ArgumentParser(description="Serve a subscription CLI as an OpenAI-compatible model on 127.0.0.1.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--cli", choices=["claude", "codex"])
    g.add_argument("--cmd", help="any command: the prompt arrives on stdin, the answer is its stdout")
    ap.add_argument("--model", help="passed to the CLI (claude --model, codex -m)")
    ap.add_argument("--effort", help="codex only: model_reasoning_effort (low, medium, high, ...)")
    ap.add_argument("--port", type=int, default=9000)
    ARGS = ap.parse_args()
    if ARGS.cli and not shutil.which(ARGS.cli):
        raise SystemExit(f"[cli_model] `{ARGS.cli}` is not on PATH; install it and log in first")
    print(f"[cli_model] {ARGS.cli or ARGS.cmd} at http://127.0.0.1:{ARGS.port}/v1\n"
          f"[cli_model] run the kit with AGENT_LLM_BASE=http://127.0.0.1:{ARGS.port}/v1 AGENT_LLM_MODEL=cli AGENT_LLM_KEY=local",
          flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
