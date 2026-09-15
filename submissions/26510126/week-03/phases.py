"""One task through the protocol: announce, bid and select, execute and report.

The assignment asks for the phases to be designed before they are run, so they
are separate functions here and the boundary between them is where a message
gets sent. Each phase does one thing and hands the next one a message, never a
Python object shortcut.

WHAT IS AN AGENT'S JUDGEMENT AND WHAT IS THE HARNESS'S RULE

  announce   the manager writes task-abstraction and eligibility; the harness
             fixes bid-specification and expiration-time. Judgement from the
             agent, contract envelope from the code, so parsing stays stable.

  bid        the contractor's own call. The harness only checks `evidence`
             against the bidder's manifest.

  select     the manager's call in three of the four conditions. In
             `ext_coded_manager` the harness takes max(confidence) instead —
             that condition exists to isolate what making this a judgement
             changed.

  accept     NOT a judgement. Capacity is one contract, so a committed
             candidate refuses and an idle one accepts, mechanically. Making
             this a model call would spend a request per task on a decision
             that has no freedom in it.

  execute    the winner's own tool loop, ending in a report. If its tools do
             not cover the task it may announce a piece, which makes it the
             manager of that piece — Smith's recursion, capped at one level.

WHY A REFUSED AWARD IS RETRIED ONCE

An agent manager can award a candidate that never bid. The endpoint refuses
it, and rather than losing the task the refusal reason goes back to the
manager for one more attempt. Both the refusal and the retry are recorded: a
manager that needs the rule explained to it is a finding about handing this
decision to a model.
"""

import json

import agents as A
import tools as T
from protocol import (ANNOUNCE, BID, AWARD, ACCEPTANCE, REFUSAL, REPORT,
                      MAX_DEPTH, depth_of, subtask_id, trajectory)

MAX_SUBTASKS = 2            # per parent; a piece cannot be broken up again
AWARD_RETRIES = 1


class TaskResult:
    """Everything one task produced. Rows and metrics are derived from this."""

    def __init__(self, task_id, gold, capable, answer):
        self.task = task_id
        self.gold = gold
        self.capable = list(capable)
        self.answer = answer
        self.manager = None
        self.number = str(task_id).split("-")[-1].split(".")[0]
        self.bids = []            # dicts: who, bid, confidence, evidence, false_evidence
        self.awarded = None
        self.award_reason = ""
        self.refusals = []        # candidates that refused an award
        self.report = ""
        self.subtasks = []        # child TaskResult
        self.failures = []        # strings; every one is a countable mode
        self.trajectory_calls = 0
        self.messages = 0
        # Which work tools the winner actually ran, and with what. Without
        # this the logs cannot answer the question the design raises: how a
        # candidate finished a task the tools it holds were not supposed to
        # cover. The first runs had no record of it and the answer had to be
        # guessed — the same gap this project's own week-03 review flagged one
        # level down, repeated here.
        self.work_calls = []      # (who, tool, args, first line of result)

    # -- the three metrics of the extension
    @property
    def feasible(self):
        return bool(self.awarded) and self.awarded in self.capable

    @property
    def optimal(self):
        return bool(self.awarded) and self.gold is not None and self.awarded == self.gold

    @property
    def solved(self):
        return bool(self.report) and T.judge(self.report, self.answer)

    @property
    def partial(self):
        return any("orphan_subtask" in f for f in self.failures)

    def note(self, mode: str):
        self.failures.append(mode)

    def row(self):
        return {
            "task": self.task, "number": self.number,
            "manager": self.manager, "gold": self.gold,
            "awarded": self.awarded, "feasible": int(self.feasible),
            "optimal": int(self.optimal), "solved": int(self.solved),
            "partial": int(self.partial), "messages": self.messages,
            "bids": len([b for b in self.bids if b["bid"]]),
            "false_evidence": sum(len(b["false_evidence"]) for b in self.bids),
            "trajectory_calls": self.trajectory_calls,
            "failures": ";".join(self.failures),
            "subtasks": len(self.subtasks),
            "tool_calls": ";".join(
                f"{who}:{tool}" for who, tool, _a, _r in self.work_calls),
        }


# ---------------------------------------------------------------- envelope


def announcement_text(task_id, abstraction, eligibility):
    """Smith 1980 Fig. 1's four fields. Two written, two fixed."""
    return ("TASK-ANNOUNCEMENT contract {cid}\n"
            "task-abstraction: {abstraction}\n"
            "eligibility-specification: {eligibility}\n"
            "bid-specification: call bidding with bid, confidence, evidence, reason\n"
            "expiration-time: reply now").format(
        cid=task_id, abstraction=abstraction, eligibility=eligibility)


def _send(endpoints, msg):
    if msg is None:
        return None
    for ep in endpoints.values():
        ep.deliver(msg)
    return msg


# ---------------------------------------------------------------- phase 1


def announce_phase(task, manager, endpoints, agents, result, round_no=0,
                   parent=None, brief=None):
    """The manager publishes the task. Returns the announcement text or None."""
    tid = str(task["id"])
    others = [n for n in endpoints if n != manager]
    ep = endpoints[manager]

    block = (f"TASK {tid}\n{brief or task['desc']}\n\n"
             "You are managing this task. Announce it to the other candidates "
             "with the announce tool: say what the work is and who should bid.")
    turn = agents[manager].act("winner" if parent else "manager", block, round_no)
    for name, _ in turn.out_of_role:
        result.note(f"out_of_role:{manager}:{name}")

    if not turn.action or turn.action[0] != "announce":
        result.note("no_announce")
        return None

    args = turn.action[1]
    text = announcement_text(tid, args.get("task_abstraction", task["desc"]),
                             args.get("eligibility", "any capable candidate"))
    msg = ep.send(ANNOUNCE, tuple(others), tid,
                  {"text": text, **args}, round_no=round_no)
    if msg is None:
        result.note("announce_refused:" + (ep.violations[-1]["reason"] if ep.violations else "?"))
        return None
    _send(endpoints, msg)
    result.messages += 1
    return text


# ---------------------------------------------------------------- phase 2


def bid_phase(task, manager, endpoints, agents, result, announcement,
              round_no=0):
    """Every contractor answers. Returns the bids the manager may award on."""
    tid = str(task["id"])
    for who in [n for n in endpoints if n != manager]:
        turn = agents[who].act("contractor", announcement, round_no)
        for name, _ in turn.out_of_role:
            result.note(f"out_of_role:{who}:{name}")

        if not turn.action or turn.action[0] != "bidding":
            # The schema is enforced by the provider, so a missing call is not
            # a malformed bid — it is a contractor that answered in prose or
            # not at all. Week 03's parse_fail is replaced by this, which the
            # report has to state rather than present as fewer failures.
            result.note(f"no_bid_call:{who}")
            result.bids.append({"who": who, "bid": False, "confidence": 0,
                                "evidence": [], "false_evidence": [],
                                "reason": (turn.text or "")[:200]})
            continue

        args = turn.action[1]
        ev = list(args.get("evidence") or [])
        bad = T.false_evidence(ev, agents[who].manifest)
        if bad:
            result.note(f"false_evidence:{who}:{','.join(bad)}")
        payload = {"bid": bool(args.get("bid")),
                   "confidence": int(args.get("confidence") or 0),
                   "evidence": ev, "reason": args.get("reason", "")}
        msg = endpoints[who].send(BID, manager, tid, payload, round_no=round_no)
        if msg is None:
            result.note(f"bid_refused:{who}")
            continue
        _send(endpoints, msg)
        result.messages += 1
        result.bids.append({**payload, "who": who, "false_evidence": bad})

    return [b for b in result.bids if b["bid"]]


# ---------------------------------------------------------------- phase 3


def _bid_digest(bids):
    lines = ["BIDS RECEIVED"]
    for b in bids:
        lines.append(f"  {b['who']}: confidence={b['confidence']} "
                     f"evidence={b['evidence']} reason={b['reason']!r}")
    return "\n".join(lines)


def select_phase(task, manager, endpoints, agents, result, bids, journal,
                 coded=False, round_no=0, parent=None):
    """Pick a winner and award. Returns the awarded candidate or None."""
    tid = str(task["id"])
    ep = endpoints[manager]
    if not bids:
        result.note("unassigned:no_bid")
        return None

    if coded:
        # Week 03's rule exactly: highest confidence, ties to whoever was asked
        # first. Kept identical so the two layers differ in one thing only.
        best = sorted(bids, key=lambda b: -b["confidence"])[0]
        msg = ep.send(AWARD, best["who"], tid,
                      {"reason": "max(confidence) by the harness"}, round_no=round_no)
        if msg is None:
            result.note("award_refused:coded")
            return None
        _send(endpoints, msg)
        result.messages += 1
        result.awarded = best["who"]
        result.award_reason = "max(confidence)"
        return best["who"]

    # The manager decides. get_trajectory, if it calls it, is cut at this
    # moment: only what causally precedes the decision is visible.
    cut = ep.decision_cut()
    records = journal.records()
    agents[manager].trajectory_fn = lambda who: trajectory(
        records, who, before=cut, members=tuple(endpoints))

    block = (f"TASK {tid}\n{task['desc']}\n\n{_bid_digest(bids)}\n\n"
             "Award the contract with the award tool. You may call "
             "get_trajectory first if the bids do not separate them.")
    excluded = []
    for attempt in range(AWARD_RETRIES + 1):
        turn = agents[manager].act("winner" if parent else "manager", block, round_no)
        result.trajectory_calls += sum(
            1 for n, _, _ in turn.work_calls if n == "get_trajectory")
        for name, _ in turn.out_of_role:
            result.note(f"out_of_role:{manager}:{name}")

        if not turn.action or turn.action[0] != "award":
            result.note("no_award_call")
            return None
        who = turn.action[1].get("candidate")
        reason = turn.action[1].get("reason", "")
        msg = ep.send(AWARD, who, tid, {"reason": reason}, round_no=round_no)
        if msg is not None:
            _send(endpoints, msg)
            result.messages += 1
            result.awarded = who
            result.award_reason = reason
            return who

        why = ep.violations[-1]["reason"] if ep.violations else "refused"
        result.note(f"invalid_award:{who}:{why}")
        excluded.append(who)
        if attempt < AWARD_RETRIES:
            result.note("award_retry")
            block = (f"TASK {tid}\n{task['desc']}\n\n{_bid_digest(bids)}\n\n"
                     f"Your award to {who} was refused: {why}. "
                     "Award one of the candidates that actually bid.")
    return None


def accept_phase(task, manager, winner, endpoints, result, round_no=0):
    """Capacity, not judgement. A committed candidate refuses."""
    tid = str(task["id"])
    ep = endpoints[winner]
    ok, _ = ep.can_send(ACCEPTANCE, tid)
    kind = ACCEPTANCE if ok else REFUSAL
    payload = {} if ok else {"justification": f"committed to {ep.committed}"}
    msg = ep.send(kind, manager, tid, payload, round_no=round_no)
    if msg is None:
        result.note(f"accept_refused:{winner}")
        return False
    _send(endpoints, msg)
    result.messages += 1
    if kind == REFUSAL:
        result.refusals.append(winner)
        result.note(f"refused_award:{winner}")
        return False
    return True


# ---------------------------------------------------------------- phase 4


def execute_phase(task, manager, winner, endpoints, agents, result, journal,
                  cfg, round_no=0):
    """The winner works, possibly hands out a piece, then reports."""
    tid = str(task["id"])
    ep = endpoints[winner]
    block = (f"TASK {tid}\n{task['desc']}\n\n"
             "You won this contract. Do the work with your own tools and "
             "report. End with a line 'Answer: ...'.")
    if depth_of(tid) < MAX_DEPTH:
        block += ("\nIf your tools do not cover all of it, announce the part "
                  "you cannot do; you will be given its result.")

    for _ in range(MAX_SUBTASKS + 1):
        turn = agents[winner].act("winner", block, round_no)
        for name, args, out in turn.work_calls:
            if name != "get_trajectory":
                result.work_calls.append(
                    (winner, name, args, str(out).splitlines()[0][:90]))
        for name, _ in turn.out_of_role:
            result.note(f"out_of_role:{winner}:{name}")

        if turn.action and turn.action[0] == "announce":
            if depth_of(tid) >= MAX_DEPTH:
                result.note("subtask_refused:max_depth")
                break
            if len(result.subtasks) >= MAX_SUBTASKS:
                result.note("subtask_refused:max_subtasks")
                break
            child = _run_subtask(task, winner, turn.action[1], endpoints,
                                 agents, result, journal, cfg, round_no)
            block = (f"TASK {tid}\n{task['desc']}\n\n"
                     f"The piece you handed out came back: "
                     f"{child.report or 'nobody took it'}\n"
                     "Now finish and report. End with 'Answer: ...'.")
            continue

        if turn.text:
            result.report = turn.text
        else:
            result.note("no_report_text")
        break

    if not result.report:
        result.note("no_report")
        return False
    msg = ep.send(REPORT, manager, tid, {"answer": result.report},
                  round_no=round_no)
    if msg is None:
        why = ep.violations[-1]["reason"] if ep.violations else "refused"
        result.note(f"report_refused:{why}")
        return False
    _send(endpoints, msg)
    result.messages += 1
    return True


def _run_subtask(parent_task, owner, args, endpoints, agents, result, journal,
                 cfg, round_no):
    """One level of recursion. The owner is the manager of this piece."""
    child_id = subtask_id(str(parent_task["id"]), len(result.subtasks) + 1)
    brief = args.get("task_abstraction", "a piece of the parent task")
    child_task = {"id": child_id, "desc": brief,
                  "capable": [], "gold": None, "answer": ""}
    child = TaskResult(child_id, None, [], "")
    child.manager = owner
    result.subtasks.append(child)

    ann = announce_phase(child_task, owner, endpoints, agents, child,
                         round_no=round_no, parent=str(parent_task["id"]),
                         brief=brief)
    if ann is None:
        endpoints[owner].mark_orphan(child_id, "announcement failed")
        result.note(f"orphan_subtask:{child_id}")
        return child

    bids = bid_phase(child_task, owner, endpoints, agents, child, ann, round_no)
    winner = select_phase(child_task, owner, endpoints, agents, child, bids,
                          journal, coded=cfg.get("coded_manager", False),
                          round_no=round_no, parent=str(parent_task["id"]))
    if winner is None or not accept_phase(child_task, owner, winner, endpoints,
                                          child, round_no):
        endpoints[owner].mark_orphan(child_id, "no taker")
        result.note(f"orphan_subtask:{child_id}")
        return child

    execute_phase(child_task, owner, winner, endpoints, agents, child, journal,
                  cfg, round_no)
    result.messages += child.messages
    return child


# ---------------------------------------------------------------- one task


def contract_id(task_id, round_no) -> str:
    """The protocol id for this task in this round.

    A round re-poses the same questions, but each announcement is a NEW
    contract: the send guard refuses a second ANNOUNCE for an id it has
    already announced, and it is right to. The first two-round run failed
    every task in round 2 with announce_refused because the task number was
    used directly as the contract id, so round 2 looked like a duplicate
    announcement of round 1's contracts.

    The separator is "-" and not "." because "." is what marks depth: "2-5"
    is round 2 of task 5 at depth 1, and "2-5.1" is a piece of it at depth 2.
    """
    return f"{round_no}-{task_id}" if round_no else str(task_id)


def run_task(task, endpoints, agents, journal, cfg, round_no=0):
    """All phases for one top-level task."""
    number = str(task["id"])            # rotation and gold use the task number
    tid = contract_id(number, round_no)  # the protocol uses the contract id
    names = tuple(sorted(endpoints))
    manager = names[(int(number) - 1) % len(names)]
    result = TaskResult(tid, task.get("gold"), task.get("capable", []),
                        task.get("answer", ""))
    result.number = number
    result.manager = manager
    endpoints[manager].assign_role(tid, "manager")
    task = {**task, "id": tid}          # every phase below sends on this id

    ann = announce_phase(task, manager, endpoints, agents, result, round_no)
    if ann is None:
        return result

    bids = bid_phase(task, manager, endpoints, agents, result, ann, round_no)
    winner = select_phase(task, manager, endpoints, agents, result, bids,
                          journal, coded=cfg.get("coded_manager", False),
                          round_no=round_no)
    if winner is None:
        return result

    if not accept_phase(task, manager, winner, endpoints, result, round_no):
        # A refusal is the concurrency case: the manager falls to the next bid.
        rest = [b for b in bids if b["who"] not in result.refusals]
        if not rest:
            result.note("unassigned:all_refused")
            return result
        result.awarded = None
        winner = select_phase(task, manager, endpoints, agents, result, rest,
                              journal, coded=cfg.get("coded_manager", False),
                              round_no=round_no)
        if winner is None or not accept_phase(task, manager, winner, endpoints,
                                              result, round_no):
            result.note("unassigned:after_refusal")
            return result

    execute_phase(task, manager, winner, endpoints, agents, result, journal,
                  cfg, round_no)
    return result
