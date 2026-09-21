"""Additional experiment, track B: a confidence-bond contract net.

Smith's protocol has no message that checks whether a bid is true, and the
required runs show what that costs when the bid price is a number the bidder
made up about itself. This file adds the three things the week-03 discussion
question asks about -- a cost attached to the claim, a counter-question, and a
reputation carried between rounds -- and measures what each one costs in
messages.

What changes relative to `manager.py`:

  1 sealed bid   the bid carries stake, evidence and a failure_condition, not
                 just confidence. A contractor has BUDGET points per round and
                 a confidence band fixes the minimum stake, so it cannot claim
                 95 on everything without running out.
  2 validate     a bid whose stake is below the band minimum, or above what is
                 left of the budget, is rejected as an invalid bid. It is a
                 message and it is counted.
  3 challenge    the top CHALLENGE_TOP bidders are asked for the strongest
                 reason they might fail, and for a final confidence. The drop
                 between the two is the measurement.
  4 calibrate    score = revised_confidence x reliability, reliability coming
                 from earlier rounds only.
  5 settle       after the round ends, an evaluator that the manager cannot
                 query compares each bid with the gold contractor, scores it
                 with Brier, moves the stake, and updates reliability. Nothing
                 about the current round reaches the manager before it decides.

Usage:
  python bond_net.py --rounds 3
  python bond_net.py --rounds 3 --fake      # no provider, wiring check only
"""
import argparse
import csv
import json
import sys
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import chat
from contractor import ANNOUNCEMENT, Contractor, build_team, system_prompt

HERE = Path(__file__).resolve().parent
LOGS = HERE / "logs"
NAMES = ("A", "B", "C")

BUDGET = 40             # bid points a contractor may stake in one round
CHALLENGE_TOP = 2       # how many leaders get the counter-question
TRUST_FLOOR = 0.8       # below this a contractor counts as having lost trust
BANDS = ((90, 20), (70, 10), (0, 5))    # confidence floor -> minimum stake


def required_stake(confidence: float) -> int:
    for floor, stake in BANDS:
        if confidence >= floor:
            return stake
    return BANDS[-1][1]


# ---------------------------------------------------------------- messages

BOND_SYSTEM = (
    "You are contractor {name} in a contract net. Your skill: {skill}. "
    "You receive a task announcement and decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "A bid costs bid points: you have {budget} points for this round and "
    "{left} left. Confidence 90-100 costs at least 20 points, 70-89 at least "
    "10, below 70 at least 5. You win the points back if the award turns out "
    "to have been right for you and lose them if it did not. "
    "Reply with one JSON object and nothing else: "
    '{{"bid": true or false, "confidence": 0-100, "stake": integer, '
    '"evidence": ["short fact", "short fact"], '
    '"failure_condition": "the condition under which you would fail", '
    '"reason": "one short sentence"}}. '
    "No code block, no text before or after the object."
)

CHALLENGE = (
    "CHALLENGE contract {cid}\n"
    "Your bid was confidence {confidence:.0f}, stake {stake}.\n"
    "Give the single strongest reason you might fail this task, then give your "
    "final confidence after taking that reason into account.\n"
    "Reply with one JSON object and nothing else: "
    '{{"original_confidence": 0-100, "revised_confidence": 0-100, '
    '"main_risk": "one short sentence"}}. '
    "No code block, no text before or after the object."
)


def bond_system_prompt(c: Contractor, left: int) -> str:
    system = BOND_SYSTEM.format(name=c.name, skill=c.skill, budget=BUDGET, left=left)
    if c.overconfident:
        # the same one sentence as the required runs, so the conditions stay
        # comparable across the two protocols
        from contractor import OVERCONFIDENT
        system += OVERCONFIDENT
    return system


# ---------------------------------------------------------------- parsing

def _json_object(raw: str):
    try:
        obj = json.loads((raw or "").strip())
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _number(value, low=0, high=100):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if low <= value <= high else None


def parse_bond_bid(raw: str):
    obj = _json_object(raw)
    if obj is None or not isinstance(obj.get("bid"), bool):
        return None
    conf = _number(obj.get("confidence"))
    if conf is None:
        return None
    stake = obj.get("stake")
    stake = int(stake) if isinstance(stake, (int, float)) and not isinstance(stake, bool) else None
    evidence = obj.get("evidence")
    if isinstance(evidence, str):
        evidence = [evidence]
    if not isinstance(evidence, list):
        evidence = []
    return {"bid": obj["bid"], "confidence": conf, "stake": stake,
            "evidence": [str(e)[:120] for e in evidence[:3]],
            "failure_condition": str(obj.get("failure_condition", ""))[:200],
            "reason": str(obj.get("reason", ""))[:200]}


def parse_revision(raw: str, original: float):
    obj = _json_object(raw)
    if obj is None:
        return None
    revised = _number(obj.get("revised_confidence"))
    if revised is None:
        return None
    return {"original_confidence": _number(obj.get("original_confidence")) or original,
            "revised_confidence": revised,
            "main_risk": str(obj.get("main_risk", ""))[:200]}


# ---------------------------------------------------------------- ledger

class Ledger:
    """Reputation and stake, carried between rounds. The evaluator owns it;
    the manager may read reliability and nothing else."""

    def __init__(self):
        self.brier_history = defaultdict(list)   # name -> per-round mean brier
        self.stake_balance = defaultdict(int)    # name -> cumulative points
        self.lost_trust_round = {}               # name -> round reliability fell below
        self.recovered_round = {}                # name -> round it came back

    def reliability(self, name: str) -> float:
        past = self.brier_history[name]
        return 1.0 - (sum(past) / len(past)) if past else 1.0

    def settle(self, round_no: int, records, winners):
        """Called once the round is over. `records` is every bid of the round,
        `winners` maps task id -> contractor name."""
        briers = defaultdict(list)
        moved = defaultdict(int)
        for rec in records:
            if not rec["parse_ok"]:
                continue
            p = (rec["confidence"] / 100.0) if rec["bid"] else 0.0
            y = 1.0 if rec["contractor"] == rec["gold"] else 0.0
            briers[rec["contractor"]].append((p - y) ** 2)
            if rec["bid"] and rec["valid"]:
                won = winners.get(rec["task"]) == rec["contractor"]
                if won:
                    moved[rec["contractor"]] += rec["stake"] if y else -rec["stake"]
        for name in NAMES:
            if briers[name]:
                self.brier_history[name].append(sum(briers[name]) / len(briers[name]))
            self.stake_balance[name] += moved[name]
            # trust recovery time: rounds between falling under TRUST_FLOOR and
            # climbing back over it. A contractor that never fell has neither.
            rel = self.reliability(name)
            if rel < TRUST_FLOOR and name not in self.lost_trust_round:
                self.lost_trust_round[name] = round_no
            elif (rel >= TRUST_FLOOR and name in self.lost_trust_round
                    and name not in self.recovered_round):
                self.recovered_round[name] = round_no
        return dict(moved)

    def recovery_time(self, name: str):
        if name in self.recovered_round:
            return self.recovered_round[name] - self.lost_trust_round[name]
        return None


# ---------------------------------------------------------------- one round

@dataclass
class BondRound:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0
    invalid_bids: int = 0
    challenges: int = 0
    drops: list = field(default_factory=list)
    records: list = field(default_factory=list)
    winners: dict = field(default_factory=dict)


def run_bond_round(tasks, team, ledger, meter, ask, condition, round_no, log=print):
    r = BondRound(tasks=len(tasks))
    left = {c.name: BUDGET for c in team}
    reliability = {c.name: ledger.reliability(c.name) for c in team}
    log("[ledger] reliability carried in: "
        + " ".join("%s=%.3f" % (n, reliability[n]) for n in NAMES))

    for t in tasks:
        announcement = ANNOUNCEMENT.format(cid=t["id"], desc=t["desc"])
        r.messages += len(team)
        log("\n[announce] contract %s -> %s"
            % (t["id"], ", ".join(c.name for c in team)))
        log("  task-abstraction: %s" % t["desc"])

        proposals = []
        for c in team:
            raw = ask(bond_system_prompt(c, left[c.name]), announcement, meter)
            parsed = parse_bond_bid(raw)
            rec = {"condition": condition, "round": round_no, "task": t["id"],
                   "gold": t["gold"], "contractor": c.name, "parse_ok": parsed is not None,
                   "bid": None, "confidence": None, "stake": 0, "valid": False,
                   "evidence": [], "failure_condition": "", "revised_confidence": None,
                   "main_risk": "", "raw": (raw or "").strip()[:500]}
            if parsed is None:
                r.parse_fails += 1
                log("  [no-bid] %s: unparseable: %s"
                    % (c.name, (raw or "").strip()[:110].replace("\n", " | ")))
                r.records.append(rec)
                continue

            r.messages += 1
            rec.update(bid=parsed["bid"], confidence=parsed["confidence"],
                       evidence=parsed["evidence"],
                       failure_condition=parsed["failure_condition"])
            if not parsed["bid"]:
                log("  [refuse] %s: bid=False reason=%s" % (c.name, parsed["reason"]))
                r.records.append(rec)
                continue

            need = required_stake(parsed["confidence"])
            stake = parsed["stake"] if parsed["stake"] is not None else -1
            if stake < need or stake > left[c.name]:
                r.invalid_bids += 1
                r.messages += 1          # reject-proposal: invalid bid
                log("  [reject] %s: confidence %.0f needs stake >= %d with %d left, "
                    "offered %s" % (c.name, parsed["confidence"], need,
                                    left[c.name], parsed["stake"]))
                r.records.append(rec)
                continue

            rec.update(valid=True, stake=stake)
            left[c.name] -= stake
            proposals.append(rec)
            r.records.append(rec)
            log("  [propose] %s: confidence=%.0f stake=%d left=%d evidence=%s "
                "failure_condition=%s"
                % (c.name, parsed["confidence"], stake, left[c.name],
                   "; ".join(parsed["evidence"]) or "-", parsed["failure_condition"] or "-"))

        if not proposals:
            r.unassigned += 1
            log("  [unassigned] contract %s drew no valid proposal (gold %s)"
                % (t["id"], t["gold"]))
            continue

        # challenge the leaders on the calibrated score, not the raw claim
        proposals.sort(key=lambda p: -(p["confidence"] * reliability[p["contractor"]]))
        for p in proposals[:CHALLENGE_TOP]:
            r.messages += 1              # the challenge
            r.challenges += 1
            question = CHALLENGE.format(cid=t["id"], confidence=p["confidence"],
                                        stake=p["stake"])
            contractor = next(c for c in team if c.name == p["contractor"])
            raw = ask(bond_system_prompt(contractor, left[p["contractor"]]),
                      question, meter)
            revision = parse_revision(raw, p["confidence"])
            if revision is None:
                r.parse_fails += 1
                log("  [challenge] %s: unparseable revision, confidence stands at %.0f"
                    % (p["contractor"], p["confidence"]))
                p["revised_confidence"] = p["confidence"]
                continue
            r.messages += 1              # the revised bid
            p["revised_confidence"] = revision["revised_confidence"]
            p["main_risk"] = revision["main_risk"]
            drop = p["confidence"] - revision["revised_confidence"]
            r.drops.append({"contractor": p["contractor"], "task": t["id"],
                            "drop": drop})
            log("  [challenge] %s: %.0f -> %.0f (drop %.0f) risk=%s"
                % (p["contractor"], p["confidence"], revision["revised_confidence"],
                   drop, revision["main_risk"]))

        for p in proposals:
            if p["revised_confidence"] is None:
                p["revised_confidence"] = p["confidence"]

        scored = [(p["revised_confidence"] * reliability[p["contractor"]], p)
                  for p in proposals]
        scored.sort(key=lambda x: -x[0])
        win = scored[0][1]
        r.messages += 1                  # accept-proposal
        r.messages += len(proposals) - 1  # reject-proposal to the rest
        r.winners[t["id"]] = win["contractor"]
        if win["contractor"] == t["gold"]:
            r.correct += 1
        else:
            r.misawards += 1
        log("  [award] %s at score %.1f (revised %.0f x reliability %.3f), gold %s -> %s"
            % (win["contractor"], scored[0][0], win["revised_confidence"],
               reliability[win["contractor"]], t["gold"],
               "correct" if win["contractor"] == t["gold"] else "MISAWARD"))

    moved = ledger.settle(round_no, r.records, r.winners)
    log("\n[settle] stake moved this round: "
        + " ".join("%s=%+d" % (n, moved.get(n, 0)) for n in NAMES))
    log("[settle] reliability after the round: "
        + " ".join("%s=%.3f" % (n, ledger.reliability(n)) for n in NAMES))
    return r


# ---------------------------------------------------------------- runner

RUN_HEADER = ["round", "condition", "tasks", "correct", "messages", "unassigned",
              "misawards", "parse_fails", "invalid_bids", "challenges",
              "mean_confidence_drop", "note"]
LEDGER_HEADER = ["round", "condition", "contractor", "reliability_in",
                 "reliability_out", "stake_balance", "awards", "recovery_rounds"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--condition", choices=("baseline", "homogeneous", "overconfident"),
                    action="append")
    ap.add_argument("--fake", action="store_true")
    args = ap.parse_args()

    conditions = args.condition or ["baseline", "homogeneous", "overconfident"]
    tasks = json.loads((HERE / "tasks.json").read_text(encoding="utf-8"))
    LOGS.mkdir(exist_ok=True)

    if args.fake:
        from fake_provider import make_fake_bond_ask
        ask = make_fake_bond_ask()
        settings = "provider=fake (deterministic stub, wiring check only)"
    else:
        ask = chat.ask
        settings = None

    run_rows, ledger_rows = [], []
    for condition in conditions:
        ledger = Ledger()
        team = build_team(condition)
        for round_no in range(1, args.rounds + 1):
            lines = []

            def log(msg, _lines=lines):
                print(msg)
                _lines.append(str(msg))

            meter = chat.Meter()
            log("=== bond round %d condition=%s budget=%d challenge_top=%d"
                % (round_no, condition, BUDGET, CHALLENGE_TOP))
            before = {n: ledger.reliability(n) for n in NAMES}
            try:
                r = run_bond_round(tasks, team, ledger, meter, ask, condition,
                                   round_no, log)
                drop = (sum(d["drop"] for d in r.drops) / len(r.drops)) if r.drops else ""
                run_rows.append([round_no, condition, r.tasks, r.correct, r.messages,
                                 r.unassigned, r.misawards, r.parse_fails,
                                 r.invalid_bids, r.challenges,
                                 "%.1f" % drop if drop != "" else "",
                                 "tokens=%d calls=%d" % (meter.tokens, meter.calls)])
                awards = defaultdict(int)
                for name in r.winners.values():
                    awards[name] += 1
                for name in NAMES:
                    recovery = ledger.recovery_time(name)
                    ledger_rows.append([round_no, condition, name,
                                        "%.4f" % before[name],
                                        "%.4f" % ledger.reliability(name),
                                        ledger.stake_balance[name], awards[name],
                                        "" if recovery is None else recovery])
                log("\n[result] correct=%d/%d messages=%d unassigned=%d misawards=%d "
                    "invalid_bids=%d parse_fails=%d"
                    % (r.correct, r.tasks, r.messages, r.unassigned, r.misawards,
                       r.invalid_bids, r.parse_fails))
            except Exception as e:
                log(traceback.format_exc())
                run_rows.append([round_no, condition, "", "", "", "", "", "", "", "",
                                 "", "crashed: %s: %s" % (type(e).__name__, str(e)[:160])])
            head = settings or chat.settings_line()
            (LOGS / ("bond-%02d-%s.txt" % (round_no, condition))).write_text(
                head + "\n" + "\n".join(lines) + "\n", encoding="utf-8")

    with (HERE / "bond_results.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(RUN_HEADER)
        w.writerows(run_rows)
    with (HERE / "bond_ledger.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(LEDGER_HEADER)
        w.writerows(ledger_rows)
    print("\nwrote bond_results.csv and bond_ledger.csv; logs in logs/bond-*.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
