"""Ground truth. The one place in this system that is not an LLM's opinion.

Everything else here is self-report checked by self-report: a contractor claims
it can do the job, then narrates what it did, then a model reads the narration.
A contractor that overstates its bid can just as easily overstate its trajectory,
and the manager has no way to tell.

So the trajectory is taken away from the contractor. The contractor hands over
an artifact -- a SQL statement, a Python function -- and *this module* runs it
against a fixture and writes the record. The contractor never touches that
record. For a Python task, whether `countdown(5)` terminates is a fact; for a
SQL task, whether the query returns the expected rows is a fact. Facts of that
kind are the only external signal in the whole run, and the interesting number
falls out of comparing them against what the LLM judge said.

Text tasks have no fixture. They stay judge-only, and are marked as such, so a
reader can see which verdicts rest on evidence and which rest on an opinion.

SAFETY: this executes model-generated code. It runs in a child process with a
wall-clock timeout so a non-terminating "fix" cannot hang the run, and no
fixture needs the network or the filesystem. It is not a real sandbox -- do not
point it at untrusted task definitions.
"""
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional

DEFAULT_TIMEOUT = 10
MAX_CAPTURE = 2000


@dataclass
class ExecutionLog:
    """Written by the harness, never by a contractor."""
    kind: str                  # "python" | "sql" | "none"
    ran: bool                  # the artifact was executed at all
    passed: Optional[bool]     # None when there is no fixture to pass
    returncode: Optional[int]
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    note: str = ""

    def summary(self) -> str:
        if self.kind == "none":
            return "fixture 없음 (LLM 판정만 가능)"
        if not self.ran:
            return f"실행 실패: {self.note}"
        verdict = "PASS" if self.passed else "FAIL"
        return f"{verdict} ({self.duration_ms}ms) {self.note}".strip()

    def as_evidence(self) -> str:
        """What the manager and Bias get to read."""
        lines = [f"    실행 종류: {self.kind}",
                 f"    판정: {self.summary()}"]
        if self.stdout.strip():
            lines.append(f"    stdout: {self.stdout.strip()[:500]}")
        if self.stderr.strip():
            lines.append(f"    stderr: {self.stderr.strip()[:500]}")
        return "\n".join(lines)


def strip_fence(text: str) -> str:
    """Models wrap artifacts in ``` even when told not to."""
    if not text:
        return ""
    m = re.search(r"```(?:\w+)?\s*(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip()


def _one_statement(sql: str) -> str:
    """Take the last non-empty statement; a model often prefixes a comment or
    a stray CREATE before the query it actually means."""
    parts = [p.strip() for p in sql.split(";") if p.strip()]
    return parts[-1] if parts else sql.strip()


PY_TEMPLATE = """\
import json, sys
SRC = json.loads({src})
HARNESS = json.loads({harness})
ns = {{}}
exec(compile(SRC, "<artifact>", "exec"), ns)
exec(compile(HARNESS, "<fixture>", "exec"), ns)
print("HARNESS_OK")
"""

SQL_TEMPLATE = """\
import json, sqlite3, sys
SCHEMA = json.loads({schema})
QUERY = json.loads({query})
EXPECT = json.loads({expect})
con = sqlite3.connect(":memory:")
con.executescript(SCHEMA)
rows = [tuple(r) for r in con.execute(QUERY).fetchall()]
print("ROWS", json.dumps(rows, ensure_ascii=False, default=str)[:1200])
if EXPECT:
    got = {{tuple(str(c) for c in r) for r in rows}}
    need = {{tuple(str(c) for c in r) for r in EXPECT}}
    missing = sorted(need - got)
    if missing:
        print("MISSING", json.dumps(missing, ensure_ascii=False))
        sys.exit(1)
elif not rows:
    print("EMPTY RESULT")
    sys.exit(1)
print("HARNESS_OK")
"""


def _run(script: str, timeout: int) -> ExecutionLog:
    started = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", script],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return ExecutionLog("", True, False, None, "", "",
                            int((time.time() - started) * 1000),
                            f"{timeout}초 안에 끝나지 않음 (무한 루프로 간주)")
    ms = int((time.time() - started) * 1000)
    out, err = proc.stdout[:MAX_CAPTURE], proc.stderr[:MAX_CAPTURE]
    passed = proc.returncode == 0 and "HARNESS_OK" in out
    note = "" if passed else (err.strip().splitlines() or [""])[-1][:200]
    return ExecutionLog("", True, passed, proc.returncode, out, err, ms, note)


def execute(check: dict, artifact: str, timeout: int = DEFAULT_TIMEOUT) -> ExecutionLog:
    """Run a contractor's artifact against the task's fixture."""
    kind = (check or {}).get("kind", "none")

    if kind == "none":
        return ExecutionLog("none", False, None, None,
                            note="이 업무에는 실행 가능한 fixture가 없음")

    body = strip_fence(artifact)
    if not body:
        return ExecutionLog(kind, False, False, None,
                            note="결과물이 비어 있어 실행할 수 없음")

    if kind == "python":
        script = PY_TEMPLATE.format(src=repr(json.dumps(body)),
                                    harness=repr(json.dumps(check.get("fixture", ""))))
    elif kind == "sql":
        script = SQL_TEMPLATE.format(
            schema=repr(json.dumps(check.get("schema", ""))),
            query=repr(json.dumps(_one_statement(body))),
            expect=repr(json.dumps(check.get("expect_contains") or [])))
    else:
        return ExecutionLog(kind, False, None, None, note=f"알 수 없는 fixture 종류: {kind}")

    log = _run(script, timeout)
    log.kind = kind
    return log


def artifact_spec(check: dict) -> str:
    """What form the deliverable has to take. The manager puts this in the
    announcement, because it is the manager that has to be able to run it."""
    kind = (check or {}).get("kind", "none")
    if kind == "python":
        return ("결과물(artifact)은 실행 가능한 파이썬 코드여야 합니다. "
                "원래 함수와 같은 이름의 고친 함수 정의만 넣으십시오. "
                "설명, 마크다운 펜스, 예시 호출은 넣지 마십시오.")
    if kind == "sql":
        return ("결과물(artifact)은 실행 가능한 단일 SQL SELECT 문이어야 합니다. "
                "SQLite 문법을 사용하고, 설명이나 마크다운 펜스는 넣지 마십시오.")
    return "결과물(artifact)은 완성된 텍스트여야 합니다."
