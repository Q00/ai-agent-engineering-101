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


def _strict_mcp():
    """strict_mcp.json {"seller": true, "since"}: add --strict-mcp-config (no MCP connector tools, ~96% of the input
    tokens of a call; person's decision 11/07 ~15:25, seller from the 20:00 restart). Read once at import: the seller
    loads it only when it restarts."""
    try:
        import time
        j = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "strict_mcp.json"), encoding="utf-8"))
        return bool(j.get("seller")) and time.time() >= (j.get("since") or 0)
    except (OSError, ValueError):
        return False


# strict_mcp.json is read only when HARNESS_CMD is not set (tests set it, 11/09 ~19:30)
CMD = os.environ["HARNESS_CMD"] if "HARNESS_CMD" in os.environ else DEFAULT_CMD + (" --strict-mcp-config" if _strict_mcp() else "")
_CWD = tempfile.mkdtemp(prefix="harness-")


LIMIT_RE = re.compile(r"hit your (session|weekly|opus|sonnet) limit|usage limit|rate limit", re.I)
FAIL_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "harness_failures.jsonl")


STREAK_URGENT = 10      # 11/10 ~15:00 (person): urgent only after more than 10 failures in a row (per process)
_streak = {"n": 0}


def _ok():
    _streak["n"] = 0


def _failed(kind, code, out, err):
    """Record a failed harness call (exit code and the first lines, to learn the exact limit wording). From 11/10
    ~15:00 (person): an info alert per failure, an urgent one (at most every 10 min) once this process has had more
    than STREAK_URGENT failures in a row (the seller then haggles by code, the buyer by soft_neg / the code guard).
    Never raises."""
    try:
        import sys
        import time
        _streak["n"] += 1
        who = os.path.basename(sys.argv[0])
        with open(FAIL_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 1), "who": who, "kind": kind, "exit": code,
                                "streak": _streak["n"], "stdout": (out or "")[:300], "stderr": (err or "")[:300]},
                               ensure_ascii=False) + "\n")
        import alerts
        txt = f"harness ({who}) {kind}: exit {code}; {((err or '') + ' ' + (out or '')).strip()[:200]}"
        if _streak["n"] > STREAK_URGENT:
            alerts.send("urgent", f"harness_fail_streak:{who}", f"LLM 호출 {_streak['n']}번 연속 실패 - {txt}. seller는 "
                        "코드 흥정, buyer는 soft_neg·코드로 계속 동작", log=lambda m: None, repeat_s=600)
        else:
            alerts.send("info", "harness_fail", txt, log=lambda m: None, repeat_s=600)
    except Exception:
        pass


def ask(prompt: str, timeout: int = 180) -> str:
    try:
        out = subprocess.run(CMD, shell=True, input=prompt, capture_output=True, text=True,
                             cwd=_CWD, timeout=timeout, env=dict(os.environ, MAX_THINKING_TOKENS="0"))
    except subprocess.TimeoutExpired:
        _failed("timeout", None, "", f"no answer in {timeout} s")
        raise
    so, se = out.stdout or "", out.stderr or ""         # None when the output could not be decoded
    if out.returncode != 0:
        _failed("limit" if LIMIT_RE.search(so + se) else "error", out.returncode, so, se)
        raise RuntimeError(f"harness exit {out.returncode}: {se.strip()[:200]}")
    if LIMIT_RE.search(so) and not so.lstrip().startswith("{"):
        _failed("limit (exit 0)", 0, so, se)                 # the failure printed as the result
    else:
        _ok()
    return so.strip()


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
