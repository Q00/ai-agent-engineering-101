"""The candidates. Identity, the cache boundary, and one turn of acting.

Four peers. A role is not part of who a candidate is — the same candidate is
manager for one task and contractor for another — and that single fact decides
the whole layout of a request.

THE CACHE BOUNDARY

Anthropic prompt caching is a prefix match, rendered in the order
`tools` -> `system` -> `messages`. A byte that changes anywhere in the prefix
invalidates everything after it. So role cannot live in either of the first
two segments, and the layout is:

    tools     4 protocol tools (identical for every candidate, fixed order)
              + this candidate's work tools (sorted)          <- invariant
    system    identity, work-tool manifest, protocol rules     <- invariant
    ========================= cache boundary =========================
    messages  [ROLE for this turn] [task] [inputs so far] ...  <- append-only

Two consequences worth stating because they are trades, not free wins.

  Tools are declared, never withdrawn. Handing a contractor only the
  contractor tools would change the `tools` array between turns, which is the
  earliest and worst place to break the prefix. So all four are always
  declared and the guard refuses an out-of-role call. A contractor can
  therefore *try* to award, and that attempt is recorded rather than made
  impossible — "did the model respect a role it was told about only after the
  cache boundary" is a measurement this design buys.

  Each candidate has its own cached prefix, because each has a different
  manifest and identity. Four prefixes, not one. That is expected; sharing one
  would mean the candidates were interchangeable, which is the thing the
  extension exists to avoid.

Whether the conversation persists across tasks is a condition, not a detail.
Persisting it makes the prefix grow and the cache pay off, and it also gives
the candidate memory of its own earlier bids — which is the lecture's
`context management` axis, and which confounds get_trajectory. So both are
available and the runner picks: `ext_persistent` and `ext_fresh`.

This layer is Anthropic-only. Cache accounting and tool use are the point, and
the OpenRouter path has neither in the same shape; it fails loudly rather than
quietly producing numbers that mean something else.
"""

import json

import model
import tools as T
from tools import MANIFESTS, CANDIDATES

MAX_STEPS = int(__import__("os").environ.get("AGENT_MAX_STEPS", "6"))

# ---------------------------------------------------------------- protocol tools
#
# Declared for every candidate in this fixed order. The description is the
# interface, so each says which role may use it — the model is told the rule
# here as well as in the role block, and the guard enforces it either way.

PROTOCOL_SPECS = [
    {
        "name": "announce",
        "description": (
            "MANAGER ONLY. Publish the task to the other candidates as a "
            "TASK-ANNOUNCEMENT. Write what the work is and who is eligible; "
            "the bid format and the deadline are fixed by the protocol and "
            "added for you. Call this once per task you manage."),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_abstraction": {
                    "type": "string",
                    "description": "What the work is, in one or two sentences."},
                "eligibility": {
                    "type": "string",
                    "description": "Which candidates should bid, and why."},
            },
            "required": ["task_abstraction", "eligibility"],
            "additionalProperties": False,
        },
    },
    {
        "name": "bidding",
        "description": (
            "CONTRACTOR ONLY. Answer an announcement with a BID. Bid only if "
            "your own tools can finish the work. `evidence` must name the "
            "tools you would actually use; it is checked against the tools you "
            "hold, and naming one you do not hold is recorded against you."),
        "input_schema": {
            "type": "object",
            "properties": {
                "bid": {"type": "boolean",
                        "description": "True to bid, false to stand down."},
                "confidence": {"type": "integer", "minimum": 0, "maximum": 100,
                               "description": "How sure you are you would finish it."},
                "evidence": {"type": "array", "items": {"type": "string"},
                             "description": "Tool names you would use."},
                "reason": {"type": "string", "description": "One short sentence."},
            },
            "required": ["bid", "confidence", "evidence", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_trajectory",
        "description": (
            "MANAGER ONLY. Look up what a candidate has done before this "
            "moment: how often it bid, how often it was awarded work, how "
            "often it reported back. Counts only, and only events that precede "
            "your decision. Use it when the bids alone do not separate them."),
        "input_schema": {
            "type": "object",
            "properties": {"candidate": {
                "type": "string", "enum": list(CANDIDATES),
                "description": "Which candidate to look up."}},
            "required": ["candidate"],
            "additionalProperties": False,
        },
    },
    {
        "name": "award",
        "description": (
            "MANAGER ONLY. Give the contract to one candidate that bid, as an "
            "ANNOUNCED-AWARD. You are not required to take the highest "
            "confidence; say in `reason` what decided it."),
        "input_schema": {
            "type": "object",
            "properties": {
                "candidate": {"type": "string", "enum": list(CANDIDATES),
                              "description": "Who gets the contract."},
                "reason": {"type": "string", "description": "What decided it."},
            },
            "required": ["candidate", "reason"],
            "additionalProperties": False,
        },
    },
]

PROTOCOL_NAMES = tuple(s["name"] for s in PROTOCOL_SPECS)

# Three role states, not two. A candidate that won a contract has to be able
# to announce a piece of it, because Smith's recursion is exactly that: the
# contractor that took the work becomes the manager of the pieces it breaks
# the work into. `winner` therefore carries the manager's tools. The label is
# separate from `manager` for the model's sake — what it should be doing now
# differs — while the permission set is identical.
ROLE_TOOLS = {
    "manager": ("announce", "get_trajectory", "award"),
    "contractor": ("bidding",),
    "winner": ("announce", "get_trajectory", "award"),
}

SYSTEM = """You are candidate {name}, one of four peers ({peers}) in a contract net.

WHAT YOU HOLD
{manifest}
These are the only tools you can run. A task you cannot finish with them is a
task you cannot finish alone.

HOW WORK IS SHARED
A task is announced by whichever candidate is managing it. Candidates that can
do the work bid. The manager awards the contract to one of them. The winner
does the work and reports the answer back. Your role is not fixed: for one
task you may be the manager, for the next a contractor. You are told your role
for the current task at the start of each turn.

Your role for the current turn is one of three:
  manager     — you may use announce, get_trajectory, award.
  contractor  — you may use bidding.
  winner      — you took the contract; you may use announce, get_trajectory,
                award, because a piece you hand out makes you its manager.
All four protocol tools are visible to you at all times; using one that does
not belong to your current role is refused and recorded.

WHEN YOU ARE THE WINNER
Run your own tools, then report. End your report with a line of the form
  Answer: <the answer>
and put nothing after it. If the task asks for two things, give both on that
one line.

If your tools do not cover the whole task, break out the part you cannot do
and announce it to the others; you are the manager of that piece and will be
given its result. You still owe the answer for the whole task. Pieces cannot
be broken up again. If nobody takes a piece, report what you do have and say
which part is missing.

HONESTY RULES
Bid only on work your own tools cover. When you name tools in `evidence`, name
the ones you would really use. Do not claim a tool result you did not get."""


class OutOfRole(Exception):
    pass


class Turn:
    """What one act() produced."""

    def __init__(self):
        self.action = None        # (protocol tool name, args) or None
        self.text = ""
        self.work_calls = []      # (tool, args, result)
        self.out_of_role = []     # (tool, args) refused for the current role
        self.disabled = []        # (tool, args) refused by the condition
        self.steps = 0
        self.stopped = ""         # why the loop ended


class Agent:
    """One candidate: a stable prefix plus an append-only conversation."""

    def __init__(self, name, journal=None, persistent=True, meter=None,
                 trajectory_fn=None, manifest=None, allow_trajectory=True):
        if name not in MANIFESTS:
            raise ValueError(f"unknown candidate {name!r}")
        self.name = name
        # A condition may hand out a different manifest (ext_uniform_tools).
        # It changes the prefix, which is correct: a candidate with different
        # tools is a different candidate and deserves its own cache.
        self.manifest = tuple(manifest if manifest is not None else MANIFESTS[name])
        # Whether history may be consulted is a condition. The tool stays
        # DECLARED either way — withdrawing it would make the tools array, and
        # so the cached prefix, differ between conditions, and then a cache
        # figure from one could not be compared with the other. It is refused
        # at call time instead, and the role block says so up front.
        self.allow_trajectory = allow_trajectory
        self.journal = journal
        self.persistent = persistent
        self.meter = meter or model.Meter()
        self.trajectory_fn = trajectory_fn     # set by the runner; causal cut
        self.messages = []
        self.cache = {"read": 0, "created": 0}

    # ---- the invariant half of the prefix

    def system_prompt(self) -> str:
        lines = [f"  {n} — {T.SPECS[n]['description'].split('. ')[0]}."
                 for n in T.TOOL_NAMES if n in self.manifest]
        return SYSTEM.format(name=self.name, peers=", ".join(CANDIDATES),
                             manifest="\n".join(lines))

    def tool_specs(self) -> list:
        """Protocol tools first, in a fixed order, then this candidate's work
        tools sorted. Never varies with role or task."""
        return list(PROTOCOL_SPECS) + T.specs_for(self.manifest)

    # ---- one turn

    def act(self, role: str, task_block: str, round_no: int = 0) -> Turn:
        """Take one turn in the given role. Returns the protocol action, if any.

        The role goes in the message, after the cache boundary. Work tools run
        here and their results are appended; a protocol tool ends the turn,
        because it is an outward action and the harness, not the agent, turns
        it into a message.
        """
        if role not in ROLE_TOOLS:
            raise ValueError(f"role must be manager or contractor, got {role!r}")
        if model.PROVIDER != "anthropic":
            # Checked here rather than in __init__ so the prefix — the part the
            # cache design lives in — can be inspected and tested without a key.
            raise SystemExit(
                "the extension layer needs the Anthropic path: prompt-cache "
                "accounting and tool use are what it measures. Set "
                "ANTHROPIC_API_KEY, or run the spec layer with run.py instead.")
        if not self.persistent:
            self.messages = []

        turn = Turn()
        self.messages.append({"role": "user", "content":
                              f"ROLE: {role}\n{task_block}"})

        for step in range(1, MAX_STEPS + 1):
            turn.steps = step
            resp = model.client().messages.create(
                model=model.MODEL,
                max_tokens=int(__import__("os").environ.get("AGENT_EXT_MAX_TOKENS", "700")),
                output_config={"effort": model.EFFORT},
                thinking={"type": "disabled"},
                cache_control={"type": "ephemeral"},
                system=self.system_prompt(),
                tools=self.tool_specs(),
                messages=self.messages,
            )
            self._account(resp.usage)
            self.messages.append({"role": "assistant", "content": resp.content})
            text = "".join(b.text for b in resp.content if b.type == "text")
            if text:
                turn.text = text
            calls = [b for b in resp.content if b.type == "tool_use"]

            if not calls:
                turn.stopped = "no tool call"
                return turn

            results = []
            for b in calls:
                name, args = b.name, dict(b.input)
                if name in PROTOCOL_NAMES:
                    if name not in ROLE_TOOLS[role]:
                        turn.out_of_role.append((name, args))
                        results.append({
                            "type": "tool_result", "tool_use_id": b.id,
                            "is_error": True,
                            "content": (f"refused: {name} belongs to the "
                                        f"{'manager' if name != 'bidding' else 'contractor'} "
                                        f"role and you are a {role} for this task")})
                        continue
                    if name == "get_trajectory":
                        if not self.allow_trajectory:
                            turn.disabled.append((name, args))
                            results.append({
                                "type": "tool_result", "tool_use_id": b.id,
                                "is_error": True,
                                "content": ("refused: history is not available "
                                            "in this configuration")})
                            continue
                        out = self._trajectory(args.get("candidate"))
                        turn.work_calls.append((name, args, out))
                        results.append({"type": "tool_result",
                                        "tool_use_id": b.id, "content": out})
                        continue
                    # announce / bidding / award end the turn: the harness sends them.
                    turn.action = (name, args)
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": "accepted by the protocol layer"})
                    self.messages.append({"role": "user", "content": results})
                    turn.stopped = f"protocol action {name}"
                    return turn

                if name not in self.manifest:
                    turn.out_of_role.append((name, args))
                    results.append({
                        "type": "tool_result", "tool_use_id": b.id,
                        "is_error": True,
                        "content": f"refused: you do not hold {name}"})
                    continue

                out = T.run_tool(name, args)
                turn.work_calls.append((name, args, out))
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": out})

            self.messages.append({"role": "user", "content": results})

        turn.stopped = f"MAX_STEPS={MAX_STEPS}"
        return turn

    # ---- helpers

    def _trajectory(self, who) -> str:
        if who not in CANDIDATES:
            return f"error: no candidate named {who!r}"
        if self.trajectory_fn is None:
            return "no history is available yet"
        t = self.trajectory_fn(who)
        if not t or not any(t.get(k) for k in
                            ("bids", "no_bids", "awards", "accepted", "reports")):
            return f"{who} has no recorded history before this decision"
        return json.dumps(t, sort_keys=True)

    def _account(self, usage):
        self.meter.add(getattr(usage, "input_tokens", 0),
                       getattr(usage, "output_tokens", 0))
        self.cache["read"] += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        self.cache["created"] += int(
            getattr(usage, "cache_creation_input_tokens", 0) or 0)
        if self.journal is not None:
            self.journal.note_usage(usage)


def team(journal=None, persistent=True, meter=None, trajectory_fn=None) -> dict:
    """The four candidates, sharing one meter so run totals are one number."""
    meter = meter or model.Meter()
    return {n: Agent(n, journal=journal, persistent=persistent, meter=meter,
                     trajectory_fn=trajectory_fn) for n in CANDIDATES}
