from __future__ import annotations
from llm import LLM


from typing import Literal

from parse import ProtocolLayer
from prompts import system_prompt
from typeset import AclMessage, Condition, EpisodeResult, Scenario


MAX_TURNS = 8


def score(scenario: Scenario, outcome: str, price: int | None) -> tuple[int, int]:
    violation = 0

    if outcome == "deal" and price is not None:
        if price < scenario.reserve or price > scenario.budget:
            violation = 1
    if scenario.feasible:
        correct = int(outcome == "deal" and price is not None and scenario.reserve <= price <= scenario.budget)
        print(f"scenario.feasible: {scenario.feasible}")
    else:
        correct = int(outcome == "no_deal")
    
    return correct, violation

def run_episode(llm: LLM, protocol: ProtocolLayer, scenario: Scenario, condition: Condition, run_id: str) -> EpisodeResult:
    buyer_sys = system_prompt("buyer", scenario.item, scenario.budget, condition)
    seller_sys = system_prompt("seller", scenario.item, scenario.reserve, condition)
    last_propose: dict[str, int | None] = {"buyer": None, "seller": None}
    format_errors = 0
    reader_calls = 0
    outcome: Literal["deal", "no_deal", "open"] = "open"
    deal_price: int | None = None
    note =""
    speaker = "buyer"

    conversation: list[str] = []
    transcript: list[str] = []
    turns = 0

    active_offer: tuple[str, int] | None = None # (offer_id, sender, price)


    histories = {
        "buyer": [
            {"role": "user", "content": "Begin the negotiation. Send your opening message to the seller."}
        ],
        "seller": [],
    }

    start_input = llm.input_tokens
    start_output = llm.output_tokens

    try:
        for turn in range(1, MAX_TURNS + 1):
            raw = llm.complete(buyer_sys if speaker == "buyer" else seller_sys, histories[speaker])
            
            turns += 1
            other = "seller" if speaker == "buyer" else "buyer"

            label = f"[{speaker}] {raw}"
            transcript.append(label)

            # current raw
            parsed = protocol.parse(condition, raw, conversation)
            format_errors += int(parsed.format_error)
            reader_calls += parsed.reader_calls
            
            if parsed.reader_calls:
                transcript.append(
                    f"  [reader] {parsed.raw_reader.strip() or parsed.performative}"
                )
            
            if parsed.format_error:
                transcript.append(
                    f"  # format_error"
                )

            conversation.append(label)
            histories[speaker].append({"role": "assistant", "content": raw})
            histories[other].append({"role": "user", "content": raw})

            # consume unread msg
            if parsed.format_error:
                speaker = other
                continue
            
            if parsed.performative == "propose":
                if parsed.price is None:
                    format_errors += 1
                    transcript.append(
                        "   # format_error: propose no price"
                    )
                else:
                    active_offer = (speaker, parsed.price)

            elif parsed.performative == "reject-proposal":
                if active_offer is not None and active_offer[0] == other:
                    active_offer = None

            elif parsed.performative == "accept-proposal":
                if active_offer is not None and active_offer[0] == other:
                    # last_propose[other]
                    deal_price = active_offer[1]
                    outcome = "deal"
                    break

                transcript.append(" # invalid_accept: no counterpart proposal")
                 
            elif parsed.performative == "refuse":
                active_offer = None
                outcome = "no_deal"
                break
            
            speaker = other
            
    except Exception as e:
        note = f"dead: {type(e).__name__}: {e}"[:200]
        outcome = "open"

    
    correct, violation = score(scenario, outcome, deal_price)
    deal_posisble = int(scenario.feasible)
    print(f"scenario.feasible: {scenario.feasible}")


    input_tokens = llm.input_tokens - start_input
    output_tokens = llm.output_tokens - start_output

    return EpisodeResult(
        run=run_id,
        condition=condition,
        scenario=scenario.id,
        item=scenario.item,
        deal_posisble=deal_posisble,
        reserve=scenario.reserve,
        budget=scenario.budget,
        outcome=outcome,  # type: ignore[arg-type]
        price=deal_price,
        correct=correct,
        violation=violation,
        turns=turns,
        format_errors=format_errors,
        reader_calls=reader_calls,
        note=note,
        transcript=transcript,
        input_tokens = input_tokens,
        output_tokens = output_tokens,
    )