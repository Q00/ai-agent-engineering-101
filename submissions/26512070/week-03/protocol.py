"""The messages of the contract net, as data.

Smith (1980) fixes the protocol, not the agents: an announcement carries an
eligibility specification, a bid answers it, an award names one winner. These
dataclasses are that protocol. Everything an agent says has to fit in one of
them, which is what keeps the manager from peeking at a contractor's internals.

Two checks exist, and the order matters:

  BidCheck        runs BEFORE the award. Nothing has been executed yet, so
                  there is no trajectory to inspect -- only the reply itself.
  TrajectoryCheck runs AFTER execution. This is the first moment a claim made
                  in a bid can be held against a record of what was done.

And the record is not the contractor's to write. A contractor submits an
artifact; sandbox.py runs it and produces the ExecutionLog. `claimed_plan` is
kept alongside precisely so the gap between the two is visible.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sandbox import ExecutionLog


@dataclass
class Task:
    id: str
    desc: str
    gold: str
    phase: str = "measure"          # "icebreak" | "measure"
    check: dict = field(default_factory=dict)

    @property
    def has_fixture(self) -> bool:
        return self.check.get("kind", "none") != "none"


@dataclass
class TaskAnnounce:
    """What the manager broadcasts. The manager writes `eligibility` without
    knowing the contractor roster, exactly as a broadcast announcement should:
    it states what the job needs, not who it wants. `artifact_spec` is the
    manager's own requirement -- it has to be able to run what comes back."""
    task_id: str
    desc: str
    eligibility: List[str]
    artifact_spec: str = ""

    def render(self) -> str:
        bullets = "\n".join(f"  - {e}" for e in self.eligibility)
        out = (f"[TaskAnnounce]\n"
               f"task_id: {self.task_id}\n"
               f"work: {self.desc}\n"
               f"eligibility (a bidder must be able to do all of these):\n{bullets}")
        if self.artifact_spec:
            out += f"\ndeliverable: {self.artifact_spec}"
        return out


@dataclass
class Bid:
    contractor: str
    will_bid: bool
    eligibility_score: int   # 0-100, the bidder's own claim of fit
    confidence: int          # 0-100, how sure it is it can finish the work
    reason: str
    parse_ok: bool = True
    raw: str = ""


@dataclass
class BidCheck:
    """Pre-award validation. Response-level only, by construction."""
    accepted: bool
    flags: List[str] = field(default_factory=list)


@dataclass
class ExecResult:
    """What came back, split by who wrote it.

    artifact / claimed_plan   the contractor's own words
    log                       the harness's record, which the contractor
                              cannot reach
    """
    contractor: str
    task_id: str
    artifact: str
    claimed_plan: List[str]
    log: ExecutionLog
    parse_ok: bool = True
    raw: str = ""


@dataclass
class TrajectoryCheck:
    """Post-execution verdict, from two independent sources.

    supports_output / covers_eligibility are an LLM's reading of the artifact.
    ground_truth is the fixture's verdict, or None where no fixture exists.
    Keeping both lets the run report how often the judge and the facts
    disagree -- which is the number that says how much any of the other
    LLM-produced verdicts in this system are worth.
    """
    supports_output: bool
    covers_eligibility: bool
    note: str = ""
    ground_truth: Optional[bool] = None

    @property
    def judge_agrees(self) -> Optional[bool]:
        if self.ground_truth is None:
            return None
        return self.supports_output == self.ground_truth

    @property
    def verdict(self) -> bool:
        """The fixture wins where there is one. An opinion does not overrule
        a program that either ran or did not."""
        return self.ground_truth if self.ground_truth is not None else self.supports_output


@dataclass
class Trial:
    """One contractor's attempt at one task: what it claimed, what it did, and
    the verdict on the gap between the two."""
    contractor: str
    bid: Bid
    result: ExecResult
    check: TrajectoryCheck


@dataclass
class IceBreakRecord:
    """An icebreaking round: every contractor bids AND executes the same task,
    regardless of who would have won it.

    This exists because of a hole in the plain protocol -- only the winner ever
    executes, so the manager accumulates evidence about exactly one contractor
    and stays permanently ignorant about the others. If an overconfident
    contractor sweeps the awards, nobody ever finds out what the specialists
    could have done. Icebreaking buys that evidence up front, at the cost of
    N executions per task instead of one.

    Its awards are not counted. Its only product is what Bias learns.
    """
    task_id: str
    desc: str
    gold: str
    announce: TaskAnnounce
    trials: List[Trial]
    messages: int


@dataclass
class TaskRecord:
    """One completed negotiation, written to the history file the Bias agent
    reads. This is the only thing Bias is allowed to learn from."""
    task_id: str
    desc: str
    gold: str
    announce: TaskAnnounce
    bids: List[Bid]
    bid_checks: Dict[str, List[str]]
    bias_advice: Optional[Dict[str, int]]
    winner: Optional[str]
    # What the plain 1980 rule would have chosen from these same bids. Bias
    # never talks to the contractors, so the bids are unaffected by its
    # presence and this counterfactual is exact, not an estimate: every Bias
    # run carries its own no-Bias control inside it.
    counterfactual_winner: Optional[str]
    result: Optional[ExecResult]
    traj_check: Optional[TrajectoryCheck]
    messages: int

    # --- allocation quality ------------------------------------------------
    @property
    def correct(self) -> bool:
        return self.winner == self.gold

    @property
    def misawarded(self) -> bool:
        return self.winner is not None and self.winner != self.gold

    @property
    def unassigned(self) -> bool:
        """With the veto gone this has one meaning again: nobody bid, or no
        bid could be parsed. The 1980 failure mode, undiluted."""
        return self.winner is None

    # --- what Bias changed, against its own exact control -------------------
    @property
    def bias_helped(self) -> bool:
        cf, w = self.counterfactual_winner, self.winner
        if cf == w:
            return False
        return cf != self.gold and (w == self.gold or w is None)

    @property
    def bias_hurt(self) -> bool:
        cf, w = self.counterfactual_winner, self.winner
        if cf == w:
            return False
        return cf == self.gold and w != self.gold

    # --- did the work actually work ----------------------------------------
    @property
    def delivered(self) -> Optional[bool]:
        """Fixture verdict on the awarded work. None when there is no fixture
        or no award. Distinct from `correct`: the right contractor can still
        ship something broken, and the wrong one can get lucky."""
        if self.result is None:
            return None
        return self.result.log.passed

    @property
    def judge_disagreed(self) -> bool:
        return bool(self.traj_check and self.traj_check.judge_agrees is False)
