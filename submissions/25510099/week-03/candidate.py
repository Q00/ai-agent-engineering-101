"""Candidate: one node class, two roles.

Every participant is a Candidate. The role decides which path `handle`
takes: the manager announces, collects and awards; a contractor bids. This
week the roles are fixed (Candidate 0 is the manager, 1-3 are contractors)
and the manager is a rule, not a model, so the only judged part of the
protocol is the bid.

Manager and contractors never call each other directly. Every message goes
through the MessageBus, and the bus count is the `messages` metric.
"""
from dataclasses import dataclass, field

from prompts import ANNOUNCEMENT, ContractorSpec
from protocol import Announcement, Award, Bid, MessageBus, NoBid, parse_bid
from tools_shared import Chat, Meter


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


def award_by_confidence(bids: list[Bid]):
    """Default award policy: highest confidence among participating bids;
    a tie goes to the earlier registered contractor. Returns (winner, tie)."""
    best, tie = None, False
    for b in bids:
        if best is None or b.confidence > best.confidence:
            best, tie = b, False
        elif b.confidence == best.confidence:
            tie = True
    return (best.contractor if best else None), tie


class Candidate:
    def __init__(self, name: str, role: str, bus: MessageBus, meter: Meter,
                 spec: ContractorSpec | None = None, log=print,
                 chat_factory=Chat, award_policy=award_by_confidence):
        self.name = name
        self.role = role
        self.bus = bus
        self.meter = meter
        self.spec = spec
        self.log = log
        self.chat_factory = chat_factory
        self.award_policy = award_policy

    # ------------------------------------------------------------ dispatch
    def handle(self, task: dict, team: list["Candidate"] | None = None):
        if self.role == "manager":
            return self._manage(task, team or [])
        raise RuntimeError("a contractor handles announcements, not tasks; call bidding()")

    # ------------------------------------------------------------ manager
    def _manage(self, task: dict, team: list["Candidate"]) -> TaskOutcome:
        anns = self.announce(task, team)
        replies = self.get(anns, team)
        return self.award(task, replies)

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
        winner, tie = self.award_policy(bids)
        if winner is None:
            self.log(f"  [unassigned] task {task['id']}: no bid")
        else:
            self.bus.send(Award(task["id"], winner))
            mark = "correct" if winner == task["gold"] else "MISAWARD"
            tie_note = " tie->first registered" if tie else ""
            self.log(f"  [award] task {task['id']} -> {winner} (gold {task['gold']}) {mark}{tie_note}")
        return TaskOutcome(task["id"], task["gold"], winner, tie, replies)

    # ------------------------------------------------------------ contractor
    def bidding(self, ann: Announcement):
        """One announcement, one fresh Chat, one model call, one Bid or NoBid."""
        chat = self.chat_factory(self.spec.system, self.meter)
        chat.add_user(ann.text)
        try:
            raw = chat.send()
        except Exception as e:
            if _is_fatal(e):
                raise                          # daily quota etc.: the whole run crashes
            return NoBid(self.name, "api_error", raw=f"{type(e).__name__}: {e}")
        return parse_bid(self.name, raw)


def _is_fatal(e: Exception) -> bool:
    """A 429 (daily free-model quota) would turn every remaining call into an
    api_error and the run into all-unassigned; better to crash and record it."""
    return type(e).__name__ in ("RateLimitError", "AuthenticationError")


def _short(s: str, n: int = 300) -> str:
    s = (s or "").replace("\n", " | ")
    return s if len(s) <= n else s[:n] + "..."
