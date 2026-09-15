"""The messages of the contract net, as data.

Smith (1980) fixes the protocol, not the agents: an announcement carries an
eligibility specification, a bid answers it, an award names one winner. These
dataclasses are that protocol. Everything an agent says has to fit in one of
them, which is what keeps the manager from peeking at a contractor's internals.

Two checks exist, and the order matters:

  BidCheck        runs BEFORE the award. Nothing has been executed yet, so
                  there is no trajectory to inspect -- only the reply itself.
  TrajectoryCheck runs AFTER execution. This is the first moment the claim in
                  the bid can be held against a record of what was done.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Task:
    id: str
    desc: str
    gold: str
    phase: str = "measure"          # "icebreak" | "measure"


@dataclass
class TaskAnnounce:
    """What the manager broadcasts. The manager writes `eligibility` without
    knowing the contractor roster, exactly as a broadcast announcement should:
    it states what the job needs, not who it wants."""
    task_id: str
    desc: str
    eligibility: List[str]

    def render(self) -> str:
        bullets = "\n".join(f"  - {e}" for e in self.eligibility)
        return (f"[TaskAnnounce]\n"
                f"task_id: {self.task_id}\n"
                f"work: {self.desc}\n"
                f"eligibility (a bidder must be able to do all of these):\n{bullets}")


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
    contractor: str
    task_id: str
    output: str
    trajectory: List[str]
    parse_ok: bool = True
    raw: str = ""


@dataclass
class TrajectoryCheck:
    """Post-execution. Compares the trajectory against the eligibility the
    contractor claimed to meet when it bid."""
    supports_output: bool
    covers_eligibility: bool
    note: str = ""


@dataclass
class Trial:
    """One contractor's attempt at one task: what it claimed, what it did, and
    the manager's verdict on the gap between the two."""
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
    vetoed: List[str]
    # What the plain 1980 rule would have chosen from these same bids. Bias
    # never talks to the contractors, so the bids are unaffected by its
    # presence and this counterfactual is exact, not an estimate: every Bias
    # run carries its own no-Bias control inside it.
    counterfactual_winner: Optional[str]
    result: Optional[ExecResult]
    traj_check: Optional[TrajectoryCheck]
    messages: int

    @property
    def correct(self) -> bool:
        return self.winner == self.gold

    @property
    def misawarded(self) -> bool:
        return self.winner is not None and self.winner != self.gold

    @property
    def unassigned(self) -> bool:
        return self.winner is None

    # --- decomposition of `unassigned`, which a veto makes ambiguous ---------
    @property
    def unassigned_nobid(self) -> bool:
        """Nobody bid, or every bid was unparseable. The 1980 failure mode."""
        return self.winner is None and not self.vetoed

    @property
    def unassigned_veto(self) -> bool:
        """Bias refused everyone who bid. A failure mode the protocol did not
        previously have -- it is the price of the veto."""
        return self.winner is None and bool(self.vetoed)

    @property
    def veto_hit_gold(self) -> bool:
        """Bias vetoed the contractor that should have won. This is the harm
        side of the veto and has to be counted separately, otherwise a rise in
        `unassigned` cannot be read as good or bad."""
        return self.gold in self.vetoed

    @property
    def bias_helped(self) -> bool:
        """Bias changed a wrong award into a right one, or into no award."""
        cf, w = self.counterfactual_winner, self.winner
        if cf == w:
            return False
        return cf != self.gold and (w == self.gold or w is None)

    @property
    def bias_hurt(self) -> bool:
        """Bias changed a right award into a wrong one, or into no award."""
        cf, w = self.counterfactual_winner, self.winner
        if cf == w:
            return False
        return cf == self.gold and w != self.gold
