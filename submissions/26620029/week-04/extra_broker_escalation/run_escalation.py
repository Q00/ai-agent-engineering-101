"""Side experiment, NOT part of the graded week-04 submission.

Different from ../extra_broker/ (a subsidy-window model that widens the
acceptable price range and re-scores the *same* two-party negotiation).
This script instead adds a real third party to the conversation itself,
in three escalating stages, reusing the exact buyer/seller agents and
protocol layer from the parent directory unchanged:

  1. base       -- the normal MAX_TURNS=5 buyer<->seller negotiation
                   (negotiation.run_episode). If it ends deal/no_deal,
                   stop here.
  2. broker     -- only if stage 1 ended "open". A neutral broker with
                   no knowledge of either side's private reserve/budget
                   proposes a single compromise price (the midpoint of
                   the two sides' own last stated offers, nudging toward
                   whoever rejects on a second attempt), and asks each
                   side to accept-proposal/reject-proposal it, continuing
                   their EXACT stage-1 conversation histories rather than
                   starting fresh. Capped at 5 messages total (the
                   broker's own proposals plus each side's replies),
                   matching the assignment's MAX_TURNS=5 framing. If
                   either side answers refuse, the stage ends no_deal
                   immediately.
  3. master_broker -- only if stage 2 also ends "open" (5 messages spent,
                   nobody accepted, nobody refused). An authority steps
                   in with a binding price -- the midpoint of the two
                   sides' PRIVATE reserve/budget (something neither the
                   agents nor the ordinary broker ever see) -- and
                   *unconditionally* finalizes a deal at that price,
                   regardless of what the two sides reply. This always
                   terminates in a deal, which is the point: it is not
                   modeling a fourth communicative act, just a forced
                   settlement of last resort. For a deal_possible=1
                   scenario the forced midpoint is safely inside
                   [reserve, budget] (violation=0 by construction); for
                   deal_possible=0 it is definitionally outside at least
                   one side's limit (violation=1) -- reported as-is, not
                   hidden, per the assignment's "count it and say so".

Scope: all 8 scenarios x all 3 conditions x 1 repeat (24 base episodes),
not the graded 5-repeat grid -- this explores whether the escalation
mechanism works at all across every scenario/condition combination, not
statistical power over repeats.
"""
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PARENT = HERE.parent
sys.path.insert(0, str(PARENT))

from model import call_model, MODEL, TEMPERATURE  # noqa: E402
from protocol import build_system_prompt, parse_message  # noqa: E402
import negotiation  # noqa: E402
from negotiation import run_episode  # noqa: E402

assert negotiation.MAX_TURNS == 5, "this exploration assumes the graded MAX_TURNS=5 base episode"

MAX_BROKER_TURNS = 5
SCENARIOS = json.loads((PARENT / "scenarios.json").read_text(encoding="utf-8"))
CONDITIONS = ("free", "tagged", "structured")

LOGS_DIR = HERE / "logs_escalation"
RESULTS_PATH = HERE / "results_escalation.csv"
HEADER = ["scenario", "condition", "deal_possible", "base_outcome", "base_turns",
          "escalated_to_broker", "broker_outcome", "broker_turns",
          "escalated_to_master", "final_stage", "final_outcome", "final_price",
          "correct", "violation", "note"]


def _score(outcome, price, deal_possible, reserve, budget):
    if outcome == "deal":
        violation = int(price < reserve or price > budget)
        correct = int(deal_possible and not violation)
    else:
        violation = 0
        correct = int(not deal_possible)
    return correct, violation


def run_broker_mediation(scenario, condition, buyer_system, seller_system,
                          buyer_history, seller_history,
                          buyer_last_offer, seller_last_offer, log):
    """Continues the stage-1 conversation with a neutral broker. Returns
    (outcome, price, turns_used, note). outcome is 'deal', 'no_deal', or
    'open' (broker's 5-message budget exhausted with no resolution)."""
    if buyer_last_offer is None and seller_last_offer is None:
        log("  [broker] no concrete offer from either side in stage 1 -- no signal to mediate from")
        return "open", None, 0, "broker had no offers to work from"

    if buyer_last_offer is None:
        price = seller_last_offer
    elif seller_last_offer is None:
        price = buyer_last_offer
    else:
        price = round((buyer_last_offer + seller_last_offer) / 2)

    step = max(1, round(abs((seller_last_offer or price) - (buyer_last_offer or price)) / 4))
    accepted = {"buyer": False, "seller": False}
    turns_used = 0

    while turns_used < MAX_BROKER_TURNS and not (accepted["buyer"] and accepted["seller"]):
        for side, system, history, other_offer in (
            ("buyer", buyer_system, buyer_history, buyer_last_offer),
            ("seller", seller_system, seller_history, seller_last_offer),
        ):
            if accepted[side] or turns_used >= MAX_BROKER_TURNS:
                continue
            msg = (f"[BROKER] The negotiation hit its turn limit without a deal. "
                   f"As a neutral broker (I do not know either side's private limit), "
                   f"I propose settling at {price}. Reply with accept-proposal if you "
                   f"take this price, or reject-proposal if you do not.")
            history.append({"role": "user", "content": msg})
            reply = call_model(system, history)
            history.append({"role": "assistant", "content": reply})
            turns_used += 1
            parsed = parse_message(condition, reply)
            log(f"  [broker->  {side}] propose {price} | {side} replies: {reply}")
            log(f"    parse: performative={parsed.performative} price={parsed.price}")
            if parsed.performative == "accept-proposal":
                accepted[side] = True
            elif parsed.performative == "refuse":
                log(f"    -> {side} REFUSES the broker's price, no deal")
                return "no_deal", None, turns_used, f"{side} refused broker's price {price}"

        if accepted["buyer"] and accepted["seller"]:
            break
        if turns_used >= MAX_BROKER_TURNS:
            break
        # nudge toward whoever hasn't accepted yet for the next attempt
        if not accepted["buyer"] and not accepted["seller"]:
            price = price - step if price is not None else price
        elif not accepted["buyer"]:
            price = price - step
        else:
            price = price + step

    if accepted["buyer"] and accepted["seller"]:
        return "deal", price, turns_used, ""
    return "open", None, turns_used, f"broker exhausted {MAX_BROKER_TURNS} turns without both sides accepting"


def run_master_broker(scenario, condition, buyer_system, seller_system,
                       buyer_history, seller_history, log):
    """Unconditional final settlement -- always returns a deal."""
    forced_price = round((scenario["reserve"] + scenario["budget"]) / 2)
    notes = []
    for side, system, history in (("buyer", buyer_system, buyer_history),
                                   ("seller", seller_system, seller_history)):
        msg = (f"[MASTER BROKER] A master broker with binding authority has now stepped "
               f"in. The final settlement price is {forced_price}. This is not up for "
               f"negotiation. Acknowledge with your accept-proposal act.")
        history.append({"role": "user", "content": msg})
        reply = call_model(system, history)
        history.append({"role": "assistant", "content": reply})
        parsed = parse_message(condition, reply)
        log(f"  [master-broker->{side}] forced price {forced_price} | {side} replies: {reply}")
        log(f"    parse: performative={parsed.performative} price={parsed.price}")
        if parsed.performative != "accept-proposal":
            notes.append(f"{side} did not accept-proposal; master broker overrode anyway")
    log(f"  -> MASTER BROKER FORCES DEAL at {forced_price} (unconditional)")
    return forced_price, "; ".join(notes)


def run_all():
    LOGS_DIR.mkdir(exist_ok=True)
    rows = []
    for condition in CONDITIONS:
        for scenario in SCENARIOS:
            sid, item = scenario["id"], scenario["item"]
            reserve, budget = scenario["reserve"], scenario["budget"]
            deal_possible = reserve <= budget

            log_lines = [f"model={MODEL} temperature={TEMPERATURE} condition={condition} "
                         f"=== scenario {sid} ({item}) reserve={reserve} budget={budget} "
                         f"deal_possible={int(deal_possible)} ===\n"]

            def log(msg, _l=log_lines):
                print(f"[{condition}/{sid}] {msg}")
                _l.append(msg + "\n")

            log("--- stage 1: base negotiation (MAX_TURNS=5) ---")
            base = run_episode(scenario, condition, log=log)

            escalated_to_broker = False
            escalated_to_master = False
            broker_outcome, broker_turns = "", 0
            final_stage, final_outcome, final_price = "base", base["outcome"], base["price"]
            note = base["note"]

            if base["outcome"] == "open":
                escalated_to_broker = True
                log("--- stage 2: broker mediation (up to 5 messages) ---")
                buyer_system = build_system_prompt("buyer", scenario, condition)
                seller_system = build_system_prompt("seller", scenario, condition)
                broker_outcome, broker_price, broker_turns, broker_note = run_broker_mediation(
                    scenario, condition, buyer_system, seller_system,
                    base["buyer_history"], base["seller_history"],
                    base["buyer_last_offer"], base["seller_last_offer"], log,
                )
                final_stage, final_outcome, final_price, note = "broker", broker_outcome, broker_price, broker_note

                if broker_outcome == "open":
                    escalated_to_master = True
                    log("--- stage 3: master broker (unconditional, always settles) ---")
                    forced_price, master_note = run_master_broker(
                        scenario, condition, buyer_system, seller_system,
                        base["buyer_history"], base["seller_history"], log,
                    )
                    final_stage, final_outcome, final_price = "master_broker", "deal", forced_price
                    note = master_note

            correct, violation = _score(final_outcome, final_price, deal_possible, reserve, budget)
            log(f"=== FINAL stage={final_stage} outcome={final_outcome} price={final_price} "
                f"correct={correct} violation={violation} ===\n")

            (LOGS_DIR / f"{condition}-{sid}.txt").write_text("".join(log_lines), encoding="utf-8")
            rows.append({
                "scenario": sid, "condition": condition, "deal_possible": int(deal_possible),
                "base_outcome": base["outcome"], "base_turns": base["turns"],
                "escalated_to_broker": int(escalated_to_broker),
                "broker_outcome": broker_outcome, "broker_turns": broker_turns,
                "escalated_to_master": int(escalated_to_master),
                "final_stage": final_stage, "final_outcome": final_outcome, "final_price": final_price,
                "correct": correct, "violation": violation, "note": note,
            })

    with RESULTS_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    n = len(rows)
    resolved_at_base = sum(1 for r in rows if r["final_stage"] == "base")
    resolved_at_broker = sum(1 for r in rows if r["final_stage"] == "broker")
    resolved_at_master = sum(1 for r in rows if r["final_stage"] == "master_broker")
    total_correct = sum(r["correct"] for r in rows)
    total_violation = sum(r["violation"] for r in rows)
    print(f"\n{n} episodes: base={resolved_at_base} broker={resolved_at_broker} "
          f"master_broker={resolved_at_master}")
    print(f"correct={total_correct}/{n} violations={total_violation}")


if __name__ == "__main__":
    run_all()
