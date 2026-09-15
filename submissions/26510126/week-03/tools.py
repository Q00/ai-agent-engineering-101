"""The work tools. Four ways to interrogate one log file.

Week 03's `skill` was a string in a prompt: a contractor claimed
"arithmetic and numeric computation" and nothing could be asked of that claim.
Here a candidate holds `count_by_hour` or it does not, so capability is a fact
about its manifest rather than an assertion in its system prompt. Two things
follow that week 03 could not have:

  `capable` for a task is derived, not labelled. It is whichever candidates
  hold every tool the task needs — computed by `capable_for`, so the gold
  column in tasks_ext.json cannot drift away from the tool sets.

  A bid's `evidence` is checkable. A contractor names the tools it intends to
  use; `false_evidence` is the part of that list it does not actually hold.
  That is a way to test a claim without trusting the number attached to it,
  which matters because week 03 measured a stated 95 to mean 54% and to mean
  different things under different prompts.

One domain on purpose. Every tool reads the same `app.log` that week 02 used,
so a task is always a question about that file and a tool is always a way to
ask it. A calculator here would be a tool for work this system never does.

Two safety choices, both deliberate:

  `grep_message` matches a literal substring, not a regular expression.
  Python has no way to bound regex backtracking, and a contractor writing its
  own pattern is exactly where a pathological one would come from. Losing
  regex costs these tasks nothing.

  Every tool caps its output. An unbounded `read_log` would put the whole file
  into an agent's context and, worse, into the cached prefix of every later
  turn. `MAX_LINES` is the ceiling and a truncated result says so.
"""

import os
from collections import Counter

LOG_PATH = os.environ.get("AGENT_LOG", os.path.join(os.path.dirname(__file__), "app.log"))
MAX_LINES = 20                       # per read_log / grep_message result
LEVELS = ("DEBUG", "INFO", "WARN", "ERROR", "FATAL")

_cache = {"path": None, "rows": None}


def _rows(path=None):
    """Parsed log lines: (date, time, level, message). Cached per path.

    The file is the reference input and is never written, so parsing it once
    per process keeps tool calls from re-reading it and keeps their results
    identical within a run.
    """
    path = path or LOG_PATH
    if _cache["path"] == path and _cache["rows"] is not None:
        return _cache["rows"]
    rows = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split(None, 3)
            if len(parts) < 3:
                continue
            date, clock, level = parts[0], parts[1], parts[2]
            msg = parts[3] if len(parts) > 3 else ""
            rows.append((date, clock, level, msg, line))
    _cache["path"], _cache["rows"] = path, rows
    return rows


def _norm_level(level):
    if level is None:
        return None
    up = str(level).strip().upper()
    return up if up in LEVELS else None


# ---------------------------------------------------------------- the tools


def count_by_hour(level: str = "ERROR", path=None) -> str:
    """How many lines of one level fall in each hour."""
    lv = _norm_level(level)
    if lv is None:
        return f"error: level must be one of {', '.join(LEVELS)}"
    per = Counter(clock[:2] for _, clock, l, _, _ in _rows(path) if l == lv)
    if not per:
        return f"no {lv} lines"
    out = ", ".join(f"{h}:00={per[h]}" for h in sorted(per))
    top = max(sorted(per), key=lambda h: per[h])
    return f"{lv} by hour: {out}. highest: {top}:00 with {per[top]}"


def count_level(level: str = "ERROR", path=None) -> str:
    """How many lines of one level there are in total."""
    lv = _norm_level(level)
    if lv is None:
        return f"error: level must be one of {', '.join(LEVELS)}"
    n = sum(1 for _, _, l, _, _ in _rows(path) if l == lv)
    total = len(_rows(path))
    return f"{lv}: {n} of {total} lines"


def grep_message(text: str, path=None) -> str:
    """Lines whose message contains a literal piece of text."""
    if not text or not str(text).strip():
        return "error: text must not be empty"
    needle = str(text)
    hits = [full for _, _, _, msg, full in _rows(path) if needle in msg]
    if not hits:
        return f"no line contains {needle!r}"
    shown = hits[:MAX_LINES]
    head = f"{len(hits)} line(s) contain {needle!r}"
    if len(hits) > len(shown):
        head += f" (showing the first {len(shown)})"
    return head + ":\n" + "\n".join(shown)


def read_log(start: int = 1, end: int = MAX_LINES, path=None) -> str:
    """The raw log between two line numbers, 1-based and inclusive."""
    rows = _rows(path)
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        return "error: start and end must be integers"
    if start < 1:
        start = 1
    if end < start:
        return f"error: end ({end}) is before start ({start})"
    window = rows[start - 1:end]
    if not window:
        return f"no lines in {start}-{end}; the file has {len(rows)}"
    capped = window[:MAX_LINES]
    head = f"lines {start}-{start + len(capped) - 1} of {len(rows)}"
    if len(capped) < len(window):
        head += f" (capped at {MAX_LINES})"
    return head + ":\n" + "\n".join(full for _, _, _, _, full in capped)


IMPL = {
    "count_by_hour": count_by_hour,
    "count_level": count_level,
    "grep_message": grep_message,
    "read_log": read_log,
}

# The description is the interface — week 01's point, and the repository says
# so too. Each one states what the tool returns and what it cannot do, because
# a contractor decides whether to bid from this text alone.
SPECS = {
    "count_by_hour": {
        "name": "count_by_hour",
        "description": (
            "Count log lines of one severity level per hour of the day, and "
            "name the hour with the most. Use this for questions about when "
            "something happened most or least often. Returns counts only, "
            "never the message text."),
        "input_schema": {
            "type": "object",
            "properties": {"level": {
                "type": "string", "enum": list(LEVELS),
                "description": "Severity level to count."}},
            "required": ["level"],
            "additionalProperties": False,
        },
    },
    "count_level": {
        "name": "count_level",
        "description": (
            "Count all log lines of one severity level in the file, with the "
            "file's total line count for comparison. Use this for how-many "
            "questions with no time dimension. Returns counts only."),
        "input_schema": {
            "type": "object",
            "properties": {"level": {
                "type": "string", "enum": list(LEVELS),
                "description": "Severity level to count."}},
            "required": ["level"],
            "additionalProperties": False,
        },
    },
    "grep_message": {
        "name": "grep_message",
        "description": (
            "Find log lines whose message contains a literal piece of text, "
            "and return them in full. Matching is plain substring, case "
            f"sensitive; regular expressions are not supported. At most "
            f"{MAX_LINES} lines come back. Use this to locate a service name, "
            "an error kind, or any wording you already know."),
        "input_schema": {
            "type": "object",
            "properties": {"text": {
                "type": "string",
                "description": "Literal text to look for in the message."}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    "read_log": {
        "name": "read_log",
        "description": (
            "Return the raw log between two 1-based line numbers, inclusive, "
            f"up to {MAX_LINES} lines. Use this when you need the text exactly "
            "as written, including timestamps, rather than a count or a search "
            "hit. It cannot search; you must already know where to look."),
        "input_schema": {
            "type": "object",
            "properties": {
                "start": {"type": "integer", "description": "First line, 1-based."},
                "end": {"type": "integer", "description": "Last line, inclusive."},
            },
            "required": ["start", "end"],
            "additionalProperties": False,
        },
    },
}

TOOL_NAMES = tuple(sorted(SPECS))     # fixed order: the cached prefix depends on it

# Who holds what. One source of truth: agents.py builds identities on top of
# this, tasks_ext.json derives `capable` from it, and the offline suite reads
# it rather than keeping a second copy that could drift.
#
# Three specialists and one generalist, chosen so that `gold` is decidable.
# With two candidates holding two tools each, a task needing one of those
# tools has two equally-specialised holders and no single best answer. Here
# the capable candidate with the fewest tools is always unique.
#
# P4 deliberately lacks count_level: that is what leaves combinations nobody
# can cover alone, which is what makes decomposition necessary rather than
# optional.
MANIFESTS = {
    "P1": ("count_by_hour", "count_level"),      # aggregation
    "P2": ("grep_message",),                     # search
    "P3": ("read_log",),                         # verbatim
    "P4": ("count_by_hour", "grep_message", "read_log"),   # generalist
}
CANDIDATES = tuple(sorted(MANIFESTS))


def gold_for(required, manifests=None):
    """The most specialised candidate that can do the task, or None.

    Most specialised means holding the fewest tools among those capable. With
    the manifests above that is always unique; a tie would mean the task set
    and the manifests disagree about what specialisation means, so it raises
    rather than picking one.
    """
    manifests = manifests or MANIFESTS
    able = capable_for(required, manifests)
    if not able:
        return None
    sizes = sorted((len(manifests[w]), w) for w in able)
    if len(sizes) > 1 and sizes[0][0] == sizes[1][0]:
        raise ValueError(
            f"gold is ambiguous for {sorted(required)}: "
            f"{sizes[0][1]} and {sizes[1][1]} are equally specialised")
    return sizes[0][1]


def specs_for(names) -> list:
    """Tool declarations for one candidate, in a fixed order.

    Order is sorted rather than however the manifest was written. The prompt
    cache is a prefix match over tools -> system -> messages, so two runs that
    declared the same tools in a different order would share no cache.
    """
    want = [n for n in TOOL_NAMES if n in set(names)]
    unknown = sorted(set(names) - set(TOOL_NAMES))
    if unknown:
        raise ValueError(f"unknown tool(s): {unknown}")
    return [SPECS[n] for n in want]


def run_tool(name: str, args: dict, path=None) -> str:
    """Execute one tool call. Returns the text that goes back to the agent."""
    fn = IMPL.get(name)
    if fn is None:
        return f"error: no tool named {name!r}"
    kwargs = dict(args or {})
    if path is not None:
        kwargs["path"] = path
    try:
        return fn(**kwargs)
    except TypeError as exc:
        return f"error: bad arguments for {name}: {exc}"


# ---------------------------------------------------------------- capability


def capable_for(required, manifests: dict) -> list:
    """Which candidates hold every tool a task needs.

    This is what makes `capable` in tasks_ext.json derived rather than
    declared: if a manifest changes, the answer changes with it, and a stale
    hand-written list cannot quietly disagree with the tool sets.

    It is a LOWER BOUND on who can actually finish the task, not the exact
    set. `requires` records the intended route — the tool the task was written
    for — and a longer route may exist: a candidate holding only `read_log`
    can count mentions of a service by reading the file in windows, which is
    what `grep_message` does in one call. A smoke test caught P3 bidding on a
    grep task for exactly that reason, and it was not wrong.

    So `feasible` measures whether an award landed inside the intended
    capability set, and `solved` measures what actually happened. A solve from
    outside `capable` is a finding, not a contradiction: either `requires` was
    drawn too narrowly or the candidate found another way, and the logs say
    which.
    """
    need = set(required)
    unknown = sorted(need - set(TOOL_NAMES))
    if unknown:
        raise ValueError(f"task requires unknown tool(s): {unknown}")
    return sorted(who for who, have in manifests.items() if need <= set(have))


def false_evidence(evidence, manifest) -> list:
    """The tools a bid named that the bidder does not hold.

    A non-empty result is a claim the protocol can refuse on evidence rather
    than on suspicion — the check week 03 had no way to make.
    """
    have = set(manifest)
    return sorted(t for t in (evidence or []) if t not in have)


# ---------------------------------------------------------------- grading
#
# Grading lives beside the capability helpers because it is the same kind of
# fact: what this task domain says counts as done. The rule is week 02's,
# carried forward deliberately — judge the last line beginning with "Answer:",
# not the whole response. A substring match over everything the agent said
# scores a run correct whenever the right value appears anywhere, even in a
# passage that concludes otherwise, and the two layers do not end the same way.


def answer_line(text: str) -> str:
    """The last line beginning with 'Answer:'. Falls back to the whole text.

    The fallback matters: a report with no Answer: line is a formatting
    failure, not automatically a wrong answer, and collapsing the two would
    hide which one happened. Callers that need to tell them apart check
    `has_answer_line` as well.
    """
    lines = [l.strip() for l in str(text or "").splitlines()]
    tagged = [l for l in lines if l.lower().startswith("answer:")]
    return tagged[-1] if tagged else str(text or "")


def has_answer_line(text: str) -> bool:
    return any(l.strip().lower().startswith("answer:")
               for l in str(text or "").splitlines())


def norm_answer(s: str) -> str:
    """Whitespace removed, case folded. So "11 / 4" and "11/4" agree, and a
    quoted message survives a difference in spacing."""
    return "".join(str(s or "").split()).casefold()


def judge(report_text: str, answer: str) -> bool:
    """Did the report answer the task? Every part of a slash-separated answer
    must appear, so half a compound answer does not score as a whole one."""
    got = norm_answer(answer_line(report_text))
    parts = [p for p in str(answer).split("/") if p.strip()]
    return all(norm_answer(p) in got for p in parts) if parts else False


def coverage(manifests: dict, size: int = 2) -> dict:
    """Which tool combinations no single candidate can cover.

    Those are the combinations that force a winner to decompose: depth stops
    being optional and becomes the only way the task can be finished. Used to
    design tasks deliberately rather than hope decomposition happens.
    """
    import itertools
    out = {}
    for combo in itertools.combinations(TOOL_NAMES, size):
        out[combo] = capable_for(combo, manifests)
    return out
