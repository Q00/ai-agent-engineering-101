import json
import os
import re
import subprocess
import tempfile
import urllib.request

OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "~openai/gpt-luna-latest")
CODEX_CMD = r'"%APPDATA%\npm\codex.cmd" exec --model gpt-6-luna --skip-git-repo-check --ephemeral --color never -'
CMD = f"OpenRouter:{OPENROUTER_MODEL} -> Codex fallback"
_CWD = tempfile.mkdtemp(prefix="harness-")


def ask_openrouter(prompt: str, timeout: int = 120) -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    payload = json.dumps({
        "model": OPENROUTER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))

    return data["choices"][0]["message"]["content"].strip()


def ask_codex(prompt: str, timeout: int = 180) -> str:
    out = subprocess.run(
        CODEX_CMD,
        shell=True,
        input=prompt,
        capture_output=True,
        text=True,
        cwd=_CWD,
        timeout=timeout,
        env=dict(os.environ, MAX_THINKING_TOKENS="0"),
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "Codex harness failed")
    return out.stdout.strip()


def ask(prompt: str, timeout: int = 180) -> str:
    try:
        return ask_openrouter(prompt, timeout=min(timeout, 120))
    except Exception as e:
        print(f"[harness] OpenRouter failed, using Codex fallback: {e}", flush=True)
        return ask_codex(prompt, timeout=timeout)


def ask_json(prompt: str):
    raw = ask(prompt)
    m = re.search(r"\{.*?\}", raw, re.S)
    if not m:
        return None, raw
    try:
        return json.loads(m.group()), raw
    except json.JSONDecodeError:
        return None, raw


def run_harness(prompt: str, timeout: int = 180) -> str:
    return ask(prompt, timeout=timeout)
