"""Investigation questions and findings (human code): the queue the investigator (kernel.analyst) works from and the
findings it writes for the role operators.

a2/questions.json: [{id, ts, from: shop|buyer|human|standing, q, for: [roles], status: open|done|failed,
answered_ts, to (only on a question for a created agent, kernel.agent_registry; without it the investigator's)}]. Role operators ask through done.ask (ask), standing questions come from ANALYST_JOB.md
(standing, enqueue_standing). Three processes write it, so every change is a read-modify-write under a file lock with
an atomic replace.

a2/findings.jsonl: one row per finished question {id, ts, from, q, finding, evidence, suggest, for, status}, and per
answer of a created agent (by: its name; id None when no question woke it). The role
operators see the newest ones addressed to them (prompt_lines); the findings tool lists them in full.
"""

import contextlib
import fcntl
import json
import os
import re

from kernel import common as c

QUESTIONS = os.path.join("a2", "questions.json")
FINDINGS = os.path.join("a2", "findings.jsonl")
ROLES = ("shop", "buyer")
FROM = ("shop", "buyer", "human", "standing", "auditor")
# Silent technical caps (the agents are not told limits): text beyond them is cut, not refused. Only
# OPEN_PER_ROLE refuses, a runaway backstop far above normal use. The prompt shows findings shortened (prompt_lines).
Q_CHARS = 4_000  # an operator's question
OPEN_PER_ROLE = 20  # open questions one role operator may have
CLOSED_KEEP = 200  # closed questions kept in the file (open ones are always kept)
FINDING_CHARS = 8_000
EVIDENCE_MAX = 40
EVIDENCE_CHARS = 500
SUGGEST_CHARS = 4_000
PROMPT_N = 3  # findings shown in a role operator's prompt
PROMPT_HOURS = 12
FINDINGS_BYTES = 5_000_000
_STANDING = re.compile(r"^- \[standing (\d+(?:\.\d+)?)h for=([a-z,]+)\] (.+)$")


@contextlib.contextmanager
def _lock(root):
    path = os.path.join(root, QUESTIONS + ".lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def load(root):
    """Every question in the file, oldest first; [] when the file is missing or broken."""
    rows = c.load_json(os.path.join(root, QUESTIONS), [])
    return (
        [r for r in rows if isinstance(r, dict) and isinstance(r.get("id"), int)]
        if isinstance(rows, list)
        else []
    )


def _save(root, rows):
    closed = [r for r in rows if r.get("status") != "open"]
    drop = (
        {id(r) for r in closed[:-CLOSED_KEEP]} if len(closed) > CLOSED_KEEP else set()
    )
    path = os.path.join(root, QUESTIONS)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            [r for r in rows if id(r) not in drop], f, ensure_ascii=False, indent=1
        )
    os.replace(tmp, path)


def _same(a, b):
    return " ".join(str(a).split()) == " ".join(str(b).split())


def _for(v, default):
    """A valid addressee list (subset of ROLES, in ROLES order) or default."""
    if isinstance(v, str):
        v = [v]
    if isinstance(v, list) and v and all(x in ROLES for x in v):
        return [r for r in ROLES if r in v]
    return default


def add(root, frm, q, now, for_=None, to=None):
    """Append an open question (to: a created agent's name, else the investigator's). -> {"ok": True, "id"} or
    {"ok": False, "error"} (bad input, or the same question is already open for the same addressee)."""
    if frm not in FROM or not isinstance(q, str) or not q.strip():
        return {"ok": False, "error": "질문은 빈 글이 아니어야 함"}
    q = q.strip()
    with _lock(root):
        rows = load(root)
        dup = next(
            (r for r in rows if r.get("status") == "open" and _same(r.get("q"), q) and r.get("to") == to),
            None,
        )
        if dup is not None:
            return {"ok": False, "error": f"같은 질문이 이미 열려 있음 (#{dup['id']})"}
        qid = max([r["id"] for r in rows] + [0]) + 1
        rows.append(
            {
                "id": qid,
                "ts": now,
                "from": frm,
                "q": q,
                "for": _for(for_, [frm] if frm in ROLES else list(ROLES)),
                "status": "open",
                "answered_ts": None,
                **({"to": to} if to is not None else {}),
            }
        )
        _save(root, rows)
    return {"ok": True, "id": qid}


def ask(root, role, value, now, secrets=()):
    """One question of a role operator's done.ask (the operator sends each item of a list here): a string or
    {"q": "...", "to": "<agent name>"} (to: a live created agent that wakes on questions; without it the
    investigator), cut to Q_CHARS, refused while that role has OPEN_PER_ROLE open questions or the same question is
    open. -> add's result."""
    to = None
    if isinstance(value, dict):
        to = value.get("to")
        value = value.get("q")
    if not isinstance(value, str) or not value.strip():
        return {"ok": False, "error": '형식: 글 또는 {"q": "..."}'}
    value = value.strip()[:Q_CHARS]
    n = sum(
        1 for r in load(root) if r.get("status") == "open" and r.get("from") == role
    )
    if n >= OPEN_PER_ROLE:
        return {"ok": False, "error": f"열린 질문이 이미 {n}개: 답이 나온 뒤 다시 물음"}
    if to is not None:
        from kernel import agent_registry

        err = agent_registry.accepts_questions(root, to)
        if err:
            return {"ok": False, "error": err}
    return add(root, role, c.mask(value, secrets), now, to=to)


def next_open(root, to=None):
    """The question to work next: operators' and the human's before standing ones, oldest first; None when none.
    to: a created agent's questions; without it the investigator's (the rows with no "to")."""
    rows = [r for r in load(root) if r.get("status") == "open" and r.get("to") == to]
    rows.sort(key=lambda r: (r.get("from") == "standing", r.get("ts") or 0, r["id"]))
    return rows[0] if rows else None


def close(root, qid, status, now):
    """Mark a question done or failed. -> the closed row or None when it is not there."""
    with _lock(root):
        rows = load(root)
        row = next((r for r in rows if r["id"] == qid), None)
        if row is None:
            return None
        row.update(status=status, answered_ts=now)
        _save(root, rows)
    return row


def standing(text):
    """Standing questions of a job text: [(hours, for, q)] from lines `- [standing 3h for=shop] <question>`."""
    out = []
    for line in str(text).splitlines():
        m = _STANDING.match(line.strip())
        if m:
            roles = _for(m.group(2).split(","), None)
            if roles and float(m.group(1)) > 0:
                out.append((float(m.group(1)), roles, m.group(3).strip()))
    return out


def enqueue_standing(root, items, now):
    """Open each standing question whose last answer is older than its interval and that has no open copy.
    -> ids added."""
    added = []
    for hours, roles, q in items:
        rows = [
            r
            for r in load(root)
            if r.get("from") == "standing" and _same(r.get("q"), q)
        ]
        if any(r.get("status") == "open" for r in rows):
            continue
        last = max([r.get("answered_ts") or r.get("ts") or 0 for r in rows] + [0])
        if now - last >= hours * 3600:
            r = add(root, "standing", q, now, for_=roles)
            if r.get("ok"):
                added.append(r["id"])
    return added


# ------------------------------------------------------------ findings
def add_finding(root, question, finding, evidence, suggest, for_, now, status="done", by=None):
    """Append the finding row of a question (masked by the caller), fields cut to their limits; by: the created agent
    that wrote it. -> the row."""
    row = {
        "id": question.get("id"),
        "ts": now,
        "from": question.get("from"),
        "q": str(question.get("q") or ""),
        "finding": str(finding or "")[:FINDING_CHARS],
        "evidence": [
            str(e)[:EVIDENCE_CHARS]
            for e in (evidence if isinstance(evidence, list) else [])
            if str(e).strip()
        ][:EVIDENCE_MAX],
        "suggest": str(suggest or "")[:SUGGEST_CHARS],
        "for": _for(for_, question.get("for") or list(ROLES)),
        "status": status,
        **({"by": by} if by else {}),
    }
    path = os.path.join(root, FINDINGS)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        fcntl.flock(f, fcntl.LOCK_UN)
    return row


def findings(root, since=None, role=None):
    """Finding rows oldest first, from ts >= since, addressed to role when given."""
    return [
        r
        for r in c.tail_json(os.path.join(root, FINDINGS), FINDINGS_BYTES)
        if (since is None or (r.get("ts") or 0) >= since)
        and (role is None or role in (r.get("for") or []))
    ]


def _who(r):
    if r.get("by"):
        return f"에이전트 {r['by']}" + (" 답" if r.get("id") is not None else "")
    return {
        "shop": "가게 운영자 질문",
        "buyer": "구매 운영자 질문",
        "human": "사람 질문",
        "standing": "정기 질문",
        "auditor": "감사자 질문",
    }.get(r.get("from"), "질문")


def prompt_lines(root, role, now):
    """A role operator's prompt section body: the newest PROMPT_N findings for it from the last PROMPT_HOURS hours,
    newest first, a few lines each; [] when there are none."""
    rows = findings(root, since=now - PROMPT_HOURS * 3600, role=role)
    aud = [i for i, r in enumerate(rows) if r.get("from") == "auditor"]
    rows = [r for i, r in enumerate(rows) if r.get("from") != "auditor" or i == aud[-1]][-PROMPT_N:]  # one auditor slot
    out = []
    for r in reversed(rows):
        mark = " (조사 미완)" if r.get("status") == "failed" else ""
        out.append(
            f"- {c.kst(r.get('ts') or now, '%H:%M')}{'' if r.get('id') is None else ' #' + str(r.get('id'))} {_who(r)}{mark}: "
            + " ".join(str(r.get("q")).split())[:200]
        )
        out.append("  결과: " + " ".join(str(r.get("finding")).split())[:600])
        if r.get("suggest"):
            out.append("  제안: " + " ".join(str(r["suggest"]).split())[:300])
    if out:
        out.append("(근거와 전문은 findings 도구)")
    return out
