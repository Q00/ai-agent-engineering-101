"""Week 06 -- the one place that calls the harness.

Both agents ask the same way: a prompt in, one line out. Three details matter.

    The working directory is an empty temp folder. The trade test starts the
    agents inside the submission directory, which sits under the course repo,
    and Claude Code reads CLAUDE.md and AGENTS.md from there and upward. Asked
    from the repo it answers "CLAUDE.md and AGENTS.md"; asked from an empty
    folder it answers "None." Repository rules have no business in a price
    negotiation, and they would arrive as instructions.

    The timeout is 50 s, chosen by measurement rather than taste. The same
    trivial prompt came back in 6 s, 48 s and 25 s on three consecutive
    tries, so the spread is the service, not the question. At 90 s one hung
    call cost a minute and a half; at 35 s a third of the calls were thrown
    away while still alive. 50 s keeps the slow answers and bounds the
    buyer, which has 300 s for a whole run and asks at most four times.

    Nothing here is trusted. `ask` returns "" on a timeout, a crash, or an
    empty answer, and every caller has a rule to fall back on. A harness that
    answers badly must cost a worse price, never a failed run.

Override the command with HARNESS_CMD to run the same agents on another
harness (or, later, on a host where a subscription login does not work).
"""
import os
import subprocess
import tempfile
import time

# The model is pinned, not aliased. `--model sonnet` resolves to whatever is
# current, and the grader may rerun this weeks from now; half the marks are
# for someone else getting the same trend from these settings alone.
#
# Haiku was the first choice because the assignment's reference run used it,
# on the assumption that the smaller model would be the faster one. Measuring
# the same negotiation prompt three times each said otherwise: haiku 10-12 s
# answering 110, 110, 105; sonnet 4-6 s answering 108 three times; opus 5-6 s
# answering 110 three times. Sonnet is twice as fast as haiku here and does
# not wobble, which matters when every decision test runs three times.
CMD = os.environ.get(
    "HARNESS_CMD",
    'claude -p --model claude-sonnet-5 --tools "" --setting-sources "" '
    '--no-session-persistence')

TIMEOUT = int(os.environ.get("HARNESS_TIMEOUT", "50"))

_EMPTY = None
CALLS = {"n": 0, "failed": 0, "seconds": 0.0}


def _empty_dir() -> str:
    """A directory with no CLAUDE.md, no AGENTS.md and nothing above them."""
    global _EMPTY
    if _EMPTY is None:
        _EMPTY = tempfile.mkdtemp(prefix="week06-harness-")
    return _EMPTY


def ask(prompt: str) -> str:
    """One harness call. "" means the caller should use its fallback."""
    CALLS["n"] += 1
    t0 = time.time()
    try:
        r = subprocess.run(CMD, input=prompt, capture_output=True, text=True,
                           shell=True, cwd=_empty_dir(), timeout=TIMEOUT)
        return (r.stdout or "").strip()
    except Exception as e:                                   # noqa: BLE001
        CALLS["failed"] += 1
        print(f"[harness] call failed ({type(e).__name__}) after "
              f"{time.time() - t0:.0f}s; falling back", flush=True)
        return ""
    finally:
        CALLS["seconds"] += time.time() - t0


def stats() -> str:
    return (f"[harness] {CALLS['n']} call(s), {CALLS['failed']} failed, "
            f"{CALLS['seconds']:.0f}s total")
