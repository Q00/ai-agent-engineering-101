"""Candidate: one node class, two roles.

Every participant is a Candidate. The role decides which path `handle`
takes: the manager announces, collects and awards; a contractor bids. This
week the roles are fixed (Candidate 0 is the manager, 1-3 are contractors)
and the manager is a rule, not a model, so the only judged part of the
protocol is the bid.

Manager and contractors never call each other directly. Every message goes
through the MessageBus, and the bus count is the `messages` metric.

Two plug-in points, both off in the three core conditions:
  award policy     what the manager looks at when it awards (policies.py)
  context policy   `fresh` = a new Chat per announcement (default);
                   `memory` = the contractor is told its own past bids and
                   who was awarded before it bids again
"""
from dataclasses import dataclass

from prompts import ANNOUNCEMENT, ContractorSpec
from protocol import Announcement, Award, Bid, MessageBus, NoBid, parse_bid
from tools_shared import Chat, Meter
from policies import ConfidencePolicy


@dataclass
class TaskOutcome:
    task_id: int
    gold: str
    winner: str | None
    tie: bool
    replies: list                 # Bid | NoBid per contractor, in registration order

    @property
    def verdict(self) -> str:
        if self.winner is None:
            return "unassigned"
        return "correct" if self.winner == self.gold else "misaward"


class Candidate:
    def __init__(self, name: str, role: str, bus: MessageBus, meter: Meter,
                 spec: ContractorSpec | None = None, log=print,
                 chat_factory=Chat, award_policy=None, context: str = "fresh"):
        self.name = name
        self.role = role
        self.bus = bus
        self.meter = meter
        self.spec = spec
        self.log = log
        self.chat_factory = chat_factory
        self.award_policy = award_policy or ConfidencePolicy(0)
        self.context = context
        self.memory: list[str] = []          # used only when context == "memory"

    # ------------------------------------------------------------ dispatch
    def handle(self, task: dict, team: list["Candidate"] | None = None):
        if self.role == "manager":
            return self._manage(task, team or [])
        raise RuntimeError("a contractor handles announcements, not tasks; call bidding()")

    # ------------------------------------------------------------ manager
    def _manage(self, task: dict, team: list["Candidate"]) -> TaskOutcome:
        anns = self.announce(task, team)
        replies = self.get(anns, team)
        outcome = self.award(task, replies)
        for c, r in zip(team, replies):
            c.remember(task["id"], r, outcome.winner)
        return outcome

    def announce(self, task: dict, team: list["Candidate"]) -> list[Announcement]:
        text = ANNOUNCEMENT.format(cid=task["id"], desc=task["desc"])
        names = ", ".join(c.name for c in team)
        self.log(f"[announce] task {task['id']} -> {names}: {task['desc']}")
        return [self.bus.send(Announcement(task["id"], task["desc"], c.name, text))
                for c in team]

    def get(self, anns: list[Announcement], team: list["Candidate"]) -> list:
        replies = []
        for ann, c in zip(anns, team):
            reply = c.bidding(ann)
            self.bus.send(reply)
            replies.append(reply)
            if isinstance(reply, Bid):
                flag = " (normalized)" if reply.normalized else ""
                self.log(f"  [bid] {c.name}: bid={str(reply.participate).lower()} "
                         f"confidence={reply.confidence:g}{flag} reason={reply.reason!r}")
            else:
                self.log(f"  [nobid] {c.name}: {reply.tag} raw={_short(reply.raw)!r}")
        return replies

    def award(self, task: dict, replies: list) -> TaskOutcome:
        bids = [r for r in replies if isinstance(r, Bid) and r.participate]
        winner, tie = self.award_policy.choose(bids, self.log)
        if winner is None:
            why = "no bid" if not bids else "no eligible bidder"
            self.log(f"  [unassigned] task {task['id']}: {why}")
        else:
            self.bus.send(Award(task["id"], winner))
            mark = "correct" if winner == task["gold"] else "MISAWARD"
            tie_note = " tie->first registered" if tie else ""
            self.log(f"  [award] task {task['id']} -> {winner} (gold {task['gold']}) {mark}{tie_note}")
        self.award_policy.record(task, winner)
        return TaskOutcome(task["id"], task["gold"], winner, tie, replies)

    # ------------------------------------------------------------ contractor
    def bidding(self, ann: Announcement):
        """One announcement, one fresh Chat, one model call, one Bid or NoBid.
        Under the memory context the user message starts with the contractor's
        own record so far; the announcement itself is unchanged."""
        chat = self.chat_factory(self.spec.system, self.meter)
        text = ann.text
        if self.context == "memory" and self.memory:
            text = ("Your record in this contract net so far:\n"
                    + "\n".join(f"- {m}" for m in self.memory) + "\n\n" + ann.text)
            self.log(f"  [memory] {self.name}: {len(self.memory)} entr{'y' if len(self.memory) == 1 else 'ies'}; "
                     f"last: {self.memory[-1]}")
        chat.add_user(text)
        try:
            raw = chat.send()
        except Exception as e:
            if _is_fatal(e):
                raise                          # daily quota etc.: the whole run crashes
            return NoBid(self.name, "api_error", raw=f"{type(e).__name__}: {e}")
        return parse_bid(self.name, raw)

    def remember(self, task_id: int, reply, winner):
        if self.context != "memory":
            return
        if isinstance(reply, Bid) and reply.participate:
            mine = f"you bid with confidence {reply.confidence:g}"
        elif isinstance(reply, Bid):
            mine = "you did not bid"
        else:
            mine = "your reply could not be parsed and counted as no bid"
        if winner is None:
            result = "no award (no bid)"
        elif winner == self.name:
            result = "awarded to you"
        else:
            result = f"awarded to {winner}"
        self.memory.append(f"task {task_id}: {mine}; {result}.")


def _is_fatal(e: Exception) -> bool:
    """A 429 (daily free-model quota) would turn every remaining call into an
    api_error and the run into all-unassigned; better to crash and record it."""
    return type(e).__name__ in ("RateLimitError", "AuthenticationError")


def _short(s: str, n: int = 300) -> str:
    s = (s or "").replace("\n", " | ")
    return s if len(s) <= n else s[:n] + "..."
