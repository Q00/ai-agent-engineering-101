"""Message layer: vector clocks, causal delivery, and the journal.

No model calls. This is the part that has to be right before any agent runs,
so it is written to be exercised offline.

Three requirements met here, and they turn out to be one requirement.

  Causality. A contractor must not see an award before the announcement it
  answers; a manager must not process a bid for an announcement it has no
  record of sending. Enforced with a vector clock per candidate and a
  hold-back queue: a message is delivered only once everything that causally
  precedes it has been.

  Concurrency. Several tasks can be in flight, a candidate can be manager for
  one and contractor for another, and two managers can want the same
  contractor. Ordering under concurrency is defined by the clocks, not by
  arrival time, so the journal is reproducible even when execution is not.

  Prompt cache. Anthropic prompt caching is a prefix match over
  tools -> system -> messages, so a conversation only stays cached while it is
  appended to. Causal delivery gives exactly that: holding a late message
  back until it is deliverable means the agent's message list is always
  appended in causal order and the prefix is never rewritten. Inserting a
  late message retroactively would break the prefix at the insertion point
  and lose every cached token after it.

So the hold-back queue is not only a correctness device. It is what keeps the
cache warm, and `journal.cache_stats()` reports whether that actually held.
"""

import json
import os
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Optional

# Smith 1980's message grammar, restricted to what this system sends.
#
# REFUSAL means one thing only: declining an award. Declining to bid travels
# inside BID as payload {"bid": false}, the way week 03 recorded it. Smith
# pairs ACCEPTANCE | REFUSAL as the response to an award, and letting REFUSAL
# mean both "I will not bid" and "I will not take it" would make the send-side
# state machine ambiguous about which message it is answering.
ANNOUNCE = "TASK-ANNOUNCEMENT"
BID = "BID"
AWARD = "ANNOUNCED-AWARD"
ACCEPTANCE = "ACCEPTANCE"
REFUSAL = "REFUSAL"
REPORT = "FINAL-REPORT"

TYPES = (ANNOUNCE, BID, REFUSAL, AWARD, ACCEPTANCE, REPORT)

# Which phase each type belongs to. The phases are the assignment's own split.
PHASE_OF = {
    ANNOUNCE: "announce",
    BID: "bid",
    AWARD: "select",
    ACCEPTANCE: "select",
    REFUSAL: "select",
    REPORT: "execute",
}


# ---------------------------------------------------------------- clocks


class VectorClock:
    """One entry per candidate. Comparison gives happens-before.

    Kept as a plain dict keyed by candidate name rather than a list, so a
    journal line stays readable and an added candidate does not shift
    positions.
    """

    def __init__(self, members, values=None):
        self.members = tuple(members)
        self.v = {m: 0 for m in self.members}
        if values:
            for m, n in values.items():
                if m in self.v:
                    self.v[m] = int(n)

    def copy(self):
        return VectorClock(self.members, dict(self.v))

    def as_dict(self):
        # Sorted so the serialized form is byte-stable: an unsorted dict in a
        # cached prefix is the classic silent cache invalidator.
        return {m: self.v[m] for m in sorted(self.v)}

    def tick(self, who):
        self.v[who] += 1
        return self

    def observe(self, other):
        """Receive: elementwise max, and nothing else.

        Deliberately does NOT advance our own entry. Our entry counts the
        messages we have sent; bumping it on a receive makes it run ahead of
        any message anyone has seen from us, after which no peer can ever
        satisfy `msg.vc[us] == local.vc[us] + 1` and every later message of
        ours is held forever. Ticking on receive is the standard way to get
        this wrong, and it hides itself until an agent both sends and
        receives — which every candidate here does.
        """
        for m in self.members:
            self.v[m] = max(self.v[m], other.v.get(m, 0))
        return self

    def leq(self, other) -> bool:
        """self <= other elementwise, i.e. self happened before or concurrent-
        equal. Used to cut the trajectory at a decision point."""
        return all(self.v[m] <= other.v.get(m, 0) for m in self.members)

    def __repr__(self):
        return "VC(" + ",".join(f"{m}:{self.v[m]}" for m in sorted(self.v)) + ")"


# ---------------------------------------------------------------- messages


MAX_DEPTH = 2          # depth 1 is the announced task, 2 its subtasks


def depth_of(task: str) -> int:
    """Task ids are hierarchical: "1" is depth 1, "1.2" is depth 2."""
    return str(task).count(".") + 1


def subtask_id(parent: str, n: int) -> str:
    return f"{parent}.{n}"


@dataclass
class Message:
    type: str
    frm: str
    to: tuple
    task: str                     # "1" at top level, "1.2" for a subtask
    payload: dict
    vc: dict                      # sender's clock at send time
    mid: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    seq: int = 0                  # journal order, assigned on record
    round: int = 0
    parent: Optional[str] = None  # set on a subtask's messages
    ts: float = field(default_factory=time.time)

    @property
    def depth(self) -> int:
        return depth_of(self.task)

    @property
    def phase(self) -> str:
        return PHASE_OF.get(self.type, "unknown")

    def record(self) -> dict:
        d = asdict(self)
        d["to"] = list(self.to)
        d["phase"] = self.phase
        return d


def make(type_: str, frm: str, to, task, payload: dict,
         clock: VectorClock, round_no: int = 0,
         parent: Optional[str] = None) -> Message:
    """Send: tick the sender's clock, stamp the message with it."""
    if type_ not in TYPES:
        raise ValueError(f"unknown message type {type_!r}")
    clock.tick(frm)
    if isinstance(to, str):
        to = (to,)
    task = str(task)
    if parent is None and "." in task:
        parent = task.rsplit(".", 1)[0]
    return Message(type=type_, frm=frm, to=tuple(to), task=task,
                   payload=payload, vc=clock.as_dict(), round=round_no,
                   parent=parent)


# ---------------------------------------------------------------- mailbox


class CausalMailbox:
    """One per candidate. Delivers in causal order, holds back the rest.

    Deliverability, the standard causal-broadcast test:
        msg.vc[sender] == local.vc[sender] + 1      # the next one from them
        msg.vc[k]      <= local.vc[k]  for k != sender   # nothing missing
    A message failing it waits until the messages it depends on arrive.

    That test assumes every participant observes every message, and this
    protocol is not a broadcast: an announcement goes to all contractors, but
    a bid goes only to the manager. Left as-is the assumption breaks in a
    specific and silent way — an award causally follows every bid the manager
    read, so a contractor that never saw a rival's bid can never satisfy the
    test for the award addressed to it, and holds it forever.

    So the two concerns are separated. The clock reconciles over *envelopes*,
    which the transport hands to everyone: type, sender, addressees, task,
    clock. Content is delivered only to addressees. `observed` counts toward
    causality; `delivered` is what this candidate may actually read, and a bid
    stays private to the manager it was sent to.

    The alternative, for a transport that cannot broadcast even metadata, is
    to carry an explicit dependency list per message instead of a vector
    clock. That is strictly more general and much heavier; the shared journal
    this system already writes makes envelope broadcast the honest model.
    """

    def __init__(self, owner: str, members):
        self.owner = owner
        self.members = tuple(members)
        self.clock = VectorClock(members)
        self.held = []            # (Message, visible) not yet deliverable
        self.seen = set()         # mid, for idempotent delivery
        self.observed = []        # every envelope, in causal order
        self.delivered = []       # only what this candidate may read

    def _deliverable(self, m: Message) -> bool:
        src = m.frm
        if m.vc.get(src, 0) != self.clock.v[src] + 1:
            return False
        return all(m.vc.get(k, 0) <= self.clock.v[k]
                   for k in self.members if k != src)

    def receive(self, m: Message, visible: bool = True) -> list:
        """Accept an envelope; return the messages that became readable.

        `visible` is whether this candidate is an addressee. An invisible
        envelope still advances the clock — that is the point — but never
        enters `delivered`, so it cannot be read by the guard, by the agent's
        context, or by anything else.
        """
        if m.mid in self.seen:
            return []             # duplicate delivery, dropped
        self.seen.add(m.mid)
        self.held.append((m, visible))
        return self._drain()

    def _drain(self) -> list:
        out = []
        moved = True
        while moved:
            moved = False
            for item in list(self.held):
                m, visible = item
                if self._deliverable(m):
                    self.held.remove(item)
                    self.clock.observe(VectorClock(self.members, m.vc))
                    self.observed.append(m)
                    if visible:
                        self.delivered.append(m)
                        out.append(m)
                    moved = True
        return out

    def pending(self) -> int:
        return len(self.held)


# ---------------------------------------------------------------- journal


class Journal:
    """Every message, in one canonical order, as JSONL.

    Execution may be concurrent; the journal is not. Lines are ordered by the
    causal order the mailboxes established, so two runs that interleaved
    differently still produce comparable logs.
    """

    def __init__(self, path: str):
        self.path = path
        self._n = 0
        self._lines = []
        self._cache = {"read": 0, "created": 0, "input": 0, "output": 0, "calls": 0}
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._fh = open(path, "w", encoding="utf-8")

    def write(self, m: Message):
        self._n += 1
        m.seq = self._n
        rec = m.record()
        line = json.dumps(rec, ensure_ascii=False, sort_keys=True)
        self._fh.write(line + "\n")
        self._fh.flush()
        self._lines.append(rec)
        return m

    def note_usage(self, usage):
        """Record cache behaviour per model call. If `read` stays at zero
        across rounds, something in the cached prefix is moving and the
        role-in-messages design is not doing its job."""
        self._cache["calls"] += 1
        self._cache["read"] += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        self._cache["created"] += int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        self._cache["input"] += int(getattr(usage, "input_tokens", 0) or 0)
        self._cache["output"] += int(getattr(usage, "output_tokens", 0) or 0)

    def cache_stats(self) -> dict:
        c = dict(self._cache)
        billed = c["input"] + c["created"]
        c["reuse"] = round(c["read"] / (c["read"] + billed), 3) if (c["read"] + billed) else 0.0
        return c

    def records(self) -> list:
        return list(self._lines)

    def close(self):
        if not self._fh.closed:
            self._fh.close()


# ---------------------------------------------------------------- endpoint


class ProtocolViolation(Exception):
    """An agent tried to generate a message its local state does not allow."""

    def __init__(self, who, type_, task, reason):
        super().__init__(f"{who} cannot send {type_} for task {task}: {reason}")
        self.who, self.type, self.task, self.reason = who, type_, task, reason


class Endpoint:
    """One candidate's view of the protocol. Owns the clock and the guard.

    Two things live here that the mailbox alone cannot provide.

    One clock, used for both directions. A send ticks it and a delivery merges
    into it. Keeping two clocks — one for sending, one for receiving — happens
    to work for a single linear exchange and is wrong as soon as an agent both
    sends and receives, which every candidate here does.

    A send-side state machine. The mailbox fixes delivery order: an award that
    arrives before its announcement waits. It says nothing about an award that
    was never preceded by an announcement at the source. When the manager is a
    model rather than a loop, that is not a hypothetical — it will try to award
    a task it never announced, or award a candidate that never bid. The guard
    below refuses those, and refuses them using only what this endpoint knows
    locally: what it has sent, and what has been delivered to it. No global
    view is consulted, because in a distributed system no participant has one.

    A refused attempt is recorded in `violations` rather than raised into the
    caller's face, so a run continues and the attempt becomes a measurement —
    "did the model try to skip a phase" is one of the more interesting things
    this system can report.
    """

    def __init__(self, name: str, members, journal=None):
        self.name = name
        self.members = tuple(members)
        self.mailbox = CausalMailbox(name, members)
        self.journal = journal
        self.roles = {}                          # task -> manager | contractor
        self.sent = defaultdict(list)            # task -> [Message]
        self.violations = []                     # (type, task, reason, to)

        # Capacity is two things, not one. `committed` is the single contract
        # this candidate owes a report on. `managing` is every task it is
        # running an auction for. They have to be separate once depth exists:
        # a winner that decomposes its task is simultaneously a contractor
        # (it owes the parent) and a manager (of the subtasks it announced).
        # Collapsing them into one counter deadlocks recursion at depth 2 —
        # the decomposer would be "busy" and unable to announce its own
        # subtasks.
        self.committed = None                    # task id, at most one
        self.managing = set()                    # task ids I announced
        self.subtasks = defaultdict(list)        # parent -> [child ids]
        self.orphans = set()                     # subtasks that drew no bid

    # -- clock is the mailbox's, so send and receive share one
    @property
    def clock(self) -> VectorClock:
        return self.mailbox.clock

    def assign_role(self, task, role: str):
        if role not in ("manager", "contractor"):
            raise ValueError(f"role must be manager or contractor, got {role!r}")
        self.roles[str(task)] = role

    # -- local views
    def _sent_types(self, task):
        return [m.type for m in self.sent[str(task)]]

    def _delivered(self, task, type_=None, frm=None):
        task = str(task)
        out = []
        for m in self.mailbox.delivered:
            if m.task != task:
                continue
            if type_ and m.type != type_:
                continue
            if frm and m.frm != frm:
                continue
            out.append(m)
        return out

    def bidders(self, task):
        """Who actually bid yes on my announcement for this task."""
        return [m.frm for m in self._delivered(task, BID)
                if m.payload.get("bid") is True]

    # -- the guard
    def can_send(self, type_: str, task, to=None):
        """(ok, reason). Local knowledge only.

        Task ids are normalised to str at every entry point. Storing under
        "1" and looking up under 1 yields an empty history rather than an
        error, so every guard silently passes or silently fails.
        """
        task = str(task)
        role = self.roles.get(task)
        sent = self._sent_types(task)
        target = to if isinstance(to, str) else (to[0] if to else None)

        if type_ == ANNOUNCE:
            if ANNOUNCE in sent:
                return False, "already announced this task"
            d = depth_of(task)
            if d > MAX_DEPTH:
                return False, f"depth {d} exceeds MAX_DEPTH={MAX_DEPTH}"
            if d == 1:
                if role != "manager":
                    return False, f"role for task {task} is {role!r}, not manager"
                return True, ""
            # A subtask is authorised by having accepted its parent, not by a
            # role someone handed out. This is Smith's role switching: the
            # contractor that took the work becomes the manager of the pieces
            # it breaks the work into.
            parent = str(task).rsplit(".", 1)[0]
            if self.committed != parent:
                return False, (f"cannot announce subtask of {parent}: this "
                               f"endpoint is committed to {self.committed!r}")
            return True, ""

        if type_ == BID:
            if role != "contractor":
                return False, f"role for task {task} is {role!r}, not contractor"
            if not self._delivered(task, ANNOUNCE):
                return False, "no announcement for this task has been delivered"
            if BID in sent:
                return False, "already answered the announcement"
            return True, ""

        if type_ == AWARD:
            if ANNOUNCE not in sent:
                return False, "cannot award a task this endpoint never announced"
            bidders = self.bidders(task)
            if not bidders:
                return False, "no bid has been delivered for this task"
            if target not in bidders:
                return False, f"{target} did not bid on this task"
            # An award is outstanding until it is answered. Re-awarding is
            # allowed only after a refusal, which is how a manager recovers
            # when its first choice is already committed elsewhere.
            awarded = [m for m in self.sent[str(task)] if m.type == AWARD]
            if awarded:
                answers = {m.type for m in self.mailbox.delivered
                           if m.task == task and m.type in (ACCEPTANCE, REFUSAL)}
                if ACCEPTANCE in answers:
                    return False, "this task has already been accepted"
                if REFUSAL not in answers:
                    return False, "an award is outstanding and unanswered"
                if target in [m.to[0] for m in awarded]:
                    return False, f"{target} was already awarded and refused"
            return True, ""

        if type_ in (ACCEPTANCE, REFUSAL):
            offered = [m for m in self._delivered(task, AWARD)
                       if self.name in m.to]
            if not offered:
                return False, "no award for this task was delivered to me"
            if ACCEPTANCE in sent or REFUSAL in sent:
                return False, "already answered the award"
            if type_ == ACCEPTANCE and self.committed not in (None, task):
                return False, (f"already committed to task {self.committed}; "
                               "capacity is one contract at a time")
            return True, ""

        if type_ == REPORT:
            if ACCEPTANCE not in sent:
                return False, "cannot report on a task that was never accepted"
            if REPORT in sent:
                return False, "already reported"
            open_ = self.open_subtasks(task)
            if open_:
                return False, (f"subtask(s) {sorted(open_)} of {task} are "
                               "neither reported nor orphaned")
            return True, ""

        return False, f"unknown type {type_!r}"

    def send(self, type_: str, to, task, payload: dict,
             round_no: int = 0, strict: bool = False):
        """Validate, then stamp and record. Returns the Message, or None when
        the attempt was refused (and logged in `violations`)."""
        task = str(task)
        ok, reason = self.can_send(type_, task, to)
        if not ok:
            self.violations.append({"type": type_, "task": task,
                                    "to": to, "reason": reason})
            if strict:
                raise ProtocolViolation(self.name, type_, task, reason)
            return None

        m = make(type_, self.name, to, task, payload, self.clock, round_no)
        self.sent[task].append(m)
        if type_ == ANNOUNCE:
            self.roles[task] = "manager"
            self.managing.add(task)
            if m.parent:
                self.subtasks[m.parent].append(task)
        elif type_ == ACCEPTANCE:
            self.committed = task
        elif type_ == REPORT:
            # Only the commitment clears here. The sender of a report is the
            # contractor, not the manager of that task, so `managing` is not
            # its business — an auction closes for whoever ran it, when the
            # report arrives. Discarding it here left a finished subtask in
            # the decomposer's `managing` forever.
            if self.committed == task:
                self.committed = None
        if self.journal is not None:
            self.journal.write(m)
        return m

    def deliver(self, m: Message) -> list:
        """Hand an envelope to this endpoint. Returns what became readable.

        Our own message is skipped: sending already ticked the clock, and it
        is in `sent`. Re-observing it would double-count our entry.

        Receiving an announcement addressed to us is what makes us a
        contractor for that task. Nothing central assigns it — which is also
        the only way a subtask can work, since the decomposer invents the
        subtask id at run time and no roster could have it in advance.
        """
        if m.frm == self.name:
            return []
        readable = self.mailbox.receive(m, visible=(self.name in m.to))
        for r in readable:
            if r.type == ANNOUNCE and self.name in r.to:
                self.roles.setdefault(r.task, "contractor")
            elif r.type == REPORT:
                # The auction I ran for this task is over.
                self.managing.discard(r.task)
        return readable

    def open_subtasks(self, task) -> set:
        """Children of `task` that have neither reported back nor been
        written off. A parent cannot report while any of these is open."""
        children = set(self.subtasks.get(str(task), ()))
        if not children:
            return set()
        reported = {m.task for m in self.mailbox.delivered if m.type == REPORT}
        return children - reported - self.orphans

    def mark_orphan(self, child: str, reason: str = "no bid"):
        """Write off a subtask that drew no bid. The parent may then report a
        partial result — losing the whole parent because one piece found no
        taker would hide which piece failed, and this course counts a failure
        that is recorded as better than one that is erased.
        """
        self.orphans.add(str(child))
        self.managing.discard(str(child))
        self.violations.append({"type": "orphan_subtask", "task": str(child),
                                "to": None, "reason": reason})

    def partial(self, task) -> bool:
        """True when this task's report cannot cover every piece of it."""
        return bool(set(self.subtasks.get(str(task), ())) & self.orphans)

    def decision_cut(self) -> dict:
        """The clock to hand `trajectory(before=...)` at a decision point."""
        return self.clock.as_dict()


def bus(endpoints):
    """The transport. Envelopes go to everyone, content only to addressees.

    Every endpoint is handed every envelope so the clocks stay reconcilable;
    each endpoint decides for itself whether it is an addressee and may read
    the content. Order is deliberately not guaranteed — the mailboxes are what
    make order safe, so a test can hand messages over shuffled and the outcome
    must not change.
    """
    def _send(m):
        # A refused send returns None. Callers write send(ep.send(...)), so
        # swallowing it here keeps a violation from turning into a crash.
        if m is None:
            return
        for ep in endpoints.values():
            ep.deliver(m)
    return _send


# ---------------------------------------------------------------- trajectory


def trajectory(records, candidate: str, before: Optional[dict] = None,
               members=None) -> dict:
    """What a manager is allowed to know about a candidate, as of `before`.

    `before` is the decision point's clock. Only events that causally precede
    it are visible. Without that cut a manager could read an outcome produced
    concurrently by another task — information the real protocol has no way
    to deliver yet, and a source of irreproducibility.

    Returns counts only, never the task answers: bids made, awards taken,
    reports delivered, refusals. The gold label and the expected answer never
    enter here, so consulting a trajectory cannot leak the thing being scored.
    """
    if members is None:
        members = sorted({r["frm"] for r in records} |
                         {t for r in records for t in r["to"]})
    cut = VectorClock(members, before) if before else None

    out = {"candidate": candidate, "bids": 0, "no_bids": 0, "refusals": 0,
           "awards": 0, "accepted": 0, "reports": 0, "tasks": []}
    for r in records:
        if r["frm"] != candidate and candidate not in r["to"]:
            continue
        if cut is not None:
            ev = VectorClock(members, r["vc"])
            if not ev.leq(cut):
                continue          # causally after the decision: not visible
        t = r["type"]
        if r["frm"] == candidate:
            if t == BID:
                if r["payload"].get("bid") is True:
                    out["bids"] += 1
                else:
                    out["no_bids"] += 1
            elif t == REFUSAL:
                out["refusals"] += 1        # declined an award it was offered
            elif t == ACCEPTANCE:
                out["accepted"] += 1
            elif t == REPORT:
                out["reports"] += 1
                out["tasks"].append(r["task"])
        elif t == AWARD and candidate in r["to"]:
            out["awards"] += 1
    return out
