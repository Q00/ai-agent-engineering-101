"""Week 06 — the harness both agents call.

`claude -p` with the flags the assignment lists, run in an empty scratch
directory so it cannot pick up a project's CLAUDE.md or settings. Every
decision in the seller and the buyer comes from here; the A2A code around it
only moves messages.

The harness is asked for one JSON object. It is a language model, so the reply
is treated as untrusted text: fences are stripped, the first object is taken,
and the caller clamps whatever number comes back against the limit it holds in
code. A harness that answers nothing usable falls back to a rule the caller
supplies -- a negotiation that stops because the model stuttered would be a
harness failure reported as a protocol failure.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

CLAUDE = shutil.which("claude")
MODEL = os.environ.get("W6_MODEL", "haiku")
TIMEOUT = int(os.environ.get("W6_TIMEOUT", "120"))
RETRIES = 2

CMD = [CLAUDE, "-p", "--model", MODEL, "--tools", "", "--setting-sources", "",
       "--no-session-persistence"]

_scratch = None


def _dir() -> str:
    """An empty directory, so the harness sees no project instructions."""
    global _scratch
    if _scratch is None:
        _scratch = tempfile.mkdtemp(prefix="w6-harness-")
    return _scratch


def log(msg: str):
    print(msg, file=sys.stderr, flush=True)


def strip_fences(text: str) -> str:
    return re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()


def first_json(text: str):
    m = re.search(r"\{.*\}", strip_fences(text), flags=re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None


def as_int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        m = re.search(r"-?\d[\d,]*", v)
        if m:
            try:
                return int(m.group(0).replace(",", ""))
            except ValueError:
                return None
    return None


def ask(prompt: str, want: tuple = (), tag: str = "") -> dict:
    """One harness call. Returns the parsed object, or {} if nothing usable.

    `want` names keys the caller needs; a reply missing them is retried once
    before giving up, because a retry is cheaper than a failed negotiation.
    """
    if CLAUDE is None:
        log("[harness] claude CLI not found on PATH")
        return {}
    for attempt in range(RETRIES):
        try:
            p = subprocess.run(CMD + [prompt], capture_output=True, text=True,
                               encoding="utf-8", cwd=_dir(), timeout=TIMEOUT)
            out = (p.stdout or "").strip()
            if p.returncode != 0:
                log(f"[harness{tag}] exit {p.returncode}: {(p.stderr or '')[:160]}")
                continue
            d = first_json(out)
            log(f"[harness{tag}] <- {out[:200]!r}")
            if d is None:
                continue
            if want and not all(k in d for k in want):
                log(f"[harness{tag}] missing {want}, retrying")
                continue
            return d
        except subprocess.TimeoutExpired:
            log(f"[harness{tag}] timed out after {TIMEOUT}s")
        except Exception as e:                     # noqa: BLE001 - any CLI failure
            log(f"[harness{tag}] {type(e).__name__}: {e}")
    return {}
