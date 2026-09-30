"""Two-buyer, one-seller negotiation with feedback, affinity, and penalties."""

from dataclasses import dataclass
import json
import re


BUYERS = ("buyer_1", "buyer_2")
TAG_RE = re.compile(
    r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)\s*(.*)$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class Offer:
    buyer_id: str
    price: int | None


@dataclass(frozen=True)
class SellerDecision:
    performative: str
    buyer_id: str | None = None


def _object(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError("message is not valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("message must be a JSON object")
    return value


def _price(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("price must be a positive integer")
    return value


def _buyer_id(value) -> str:
    if value not in BUYERS:
        raise ValueError("buyer_id must be buyer_1 or buyer_2")
    return value


def parse_structured_offer(raw: str, buyer_id: str) -> Offer:
    value = _object(raw)
    content = value.get("content")
    if not isinstance(content, dict):
        raise ValueError("structured offer needs object content")
    performative = value.get("performative")
    if performative == "refuse":
        return Offer(_buyer_id(buyer_id), None)
    if performative != "propose":
        raise ValueError("buyer must propose or refuse")
    return Offer(_buyer_id(buyer_id), _price(content.get("price")))


def parse_structured_seller_decision(raw: str) -> SellerDecision:
    value = _object(raw)
    content = value.get("content")
    if not isinstance(content, dict):
        raise ValueError("structured seller decision needs object content")
    performative = value.get("performative")
    if performative == "accept-proposal":
        return SellerDecision(performative, _buyer_id(content.get("buyer_id")))
    if performative in {"reject-proposal", "refuse"}:
        return SellerDecision(performative)
    raise ValueError("seller must accept-proposal, reject-proposal, or refuse")


def parse_reader_output(raw: str) -> dict:
    value = _object(raw)
    performative = value.get("performative")
    if performative not in {
        "propose",
        "accept-proposal",
        "reject-proposal",
        "refuse",
    }:
        raise ValueError("reader returned an unsupported performative")
    return value


def parse_tagged_offer(raw: str, buyer_id: str, price_reader) -> Offer:
    match = TAG_RE.match(raw or "")
    if not match:
        raise ValueError("missing performative tag")
    performative, body = match.groups()
    if performative.lower() == "refuse":
        return Offer(_buyer_id(buyer_id), None)
    if performative.lower() != "propose":
        raise ValueError("buyer tag must be propose or refuse")
    return Offer(_buyer_id(buyer_id), _price(price_reader(body)))


def parse_tagged_seller_decision(raw: str, accept_reader) -> SellerDecision:
    match = TAG_RE.match(raw or "")
    if not match:
        raise ValueError("missing performative tag")
    performative, body = match.groups()
    performative = performative.lower()
    if performative == "accept-proposal":
        return SellerDecision(performative, _buyer_id(accept_reader(body)))
    if performative in {"reject-proposal", "refuse"}:
        return SellerDecision(performative)
    raise ValueError("seller tag must accept, reject, or refuse")


def buyer_penalty(offer: int, budget: int) -> float:
    """Return over-budget severity as a percentage of the private budget."""
    return 100.0 * max(0, offer - budget) / budget


def effective_offer(offer: int, affinity: int, reserve: int) -> float:
    """Each affinity point is worth one percent of the seller reserve."""
    return offer + reserve * 0.01 * affinity


def seller_penalties(
    accepted_price: int,
    reserve: int,
    highest_valid_offer: int | None,
) -> tuple[float, float]:
    reserve_penalty = 100.0 * max(0, reserve - accepted_price) / reserve
    if not highest_valid_offer:
        opportunity_penalty = 0.0
    else:
        opportunity_penalty = (
            100.0 * max(0, highest_valid_offer - accepted_price) / highest_valid_offer
        )
    return reserve_penalty, opportunity_penalty


def valid_offers(offers: dict[str, Offer], budgets: dict[str, int]) -> dict[str, Offer]:
    return {
        buyer_id: offer
        for buyer_id, offer in offers.items()
        if offer.price is not None and offer.price <= budgets[buyer_id]
    }


def award_affinity(
    affinity: dict[str, int],
    offers: dict[str, Offer],
    budgets: dict[str, int],
) -> tuple[dict[str, int], str | None]:
    updated = dict(affinity)
    eligible = valid_offers(offers, budgets)
    if not eligible:
        return updated, None
    highest = max(offer.price for offer in eligible.values())
    leaders = [
        buyer_id for buyer_id, offer in eligible.items() if offer.price == highest
    ]
    if len(leaders) != 1:
        return updated, None
    awarded = leaders[0]
    updated[awarded] += 1
    return updated, awarded


def evaluate_competitive_outcome(
    scenario: dict,
    outcome: str,
    winner: str | None,
    price: int | None,
) -> tuple[int, int]:
    possible = any(
        budget >= scenario["reserve"] for budget in scenario["budgets"].values()
    )
    if outcome == "deal":
        if winner not in scenario["budgets"] or price is None:
            return 0, 1
        violation = int(
            price < scenario["reserve"]
            or price > scenario["budgets"][winner]
        )
        return int(possible and not violation), violation
    return int(outcome == "no_deal" and not possible), 0


def format_instruction(condition: str, role: str) -> str:
    if condition == "free":
        return "Write one or two plain English sentences without tags or JSON."
    if condition == "tagged":
        acts = (
            "(propose) or (refuse)"
            if role == "buyer"
            else "(accept-proposal), (reject-proposal), or (refuse)"
        )
        return f"Start with exactly one tag, {acts}, then one English sentence."
    if condition == "structured":
        if role == "buyer":
            return (
                'Return JSON only: {"performative":"propose"|"refuse",'
                '"content":{"price":integer|null}}.'
            )
        return (
            'Return JSON only: {"performative":"accept-proposal"|'
            '"reject-proposal"|"refuse","content":{"buyer_id":'
            '"buyer_1"|"buyer_2"|null}}.'
        )
    raise ValueError(f"unknown condition: {condition}")


def buyer_system(buyer_id: str, scenario: dict, condition: str) -> str:
    budget = scenario["budgets"][buyer_id]
    return (
        f"You are {buyer_id}, competing to buy {scenario['item']}. "
        f"Your private maximum budget is {budget}. Never reveal it. "
        "Offer a positive whole-number price or refuse. A higher valid rejected bid "
        "earns one affinity point. Every amount above your budget adds a normalized "
        "penalty and cannot earn affinity. "
        + format_instruction(condition, "buyer")
    )


def seller_system(scenario: dict, condition: str) -> str:
    return (
        f"You are the seller of {scenario['item']}. Your private reserve is "
        f"{scenario['reserve']}. Never reveal it. In each round, accept exactly one "
        "buyer, reject both, or refuse. Each affinity point adds one percent of your "
        "reserve to that buyer's effective offer for preference only; the actual deal "
        "price remains the buyer's bid. Accepting below reserve creates a reserve "
        "penalty, and choosing below the highest valid offer creates opportunity loss. "
        + format_instruction(condition, "seller")
    )


READER_PROMPT = (
    "You are a protocol reader for a two-buyer price negotiation. Label the LAST "
    "message only. Return JSON only with performative, integer price or null, and "
    "buyer_id or null. Allowed performatives are propose, accept-proposal, "
    "reject-proposal, and refuse."
)


def _transcript_messages(transcript: list[dict]) -> list[dict[str, str]]:
    rendered = "\n".join(
        f"round {event['round']} {event['agent']}: {event['raw']}"
        for event in transcript
    )
    return [{"role": "user", "content": rendered + "\nLabel the LAST message only."}]


def run_competitive_episode(
    scenario: dict,
    condition: str,
    model,
    log,
    max_rounds: int = 4,
) -> dict:
    budgets = scenario["budgets"]
    affinity = {buyer_id: 0 for buyer_id in BUYERS}
    penalties = {buyer_id: 0.0 for buyer_id in BUYERS}
    active = {buyer_id: True for buyer_id in BUYERS}
    histories = {
        buyer_id: [{
            "role": "user",
            "content": (
                f"Round 1 of {max_rounds}. Your affinity is 0 and penalty is 0. "
                "Submit your offer now."
            ),
        }]
        for buyer_id in BUYERS
    }
    histories["seller"] = []
    transcript = []
    highest_trace = []
    reader_calls = 0
    format_errors = 0
    agent_calls = 0
    outcome = "open"
    winner = None
    price = None
    rounds_used = 0
    seller_reserve_penalty = 0.0
    seller_opportunity_penalty = 0.0

    log({
        "event": "episode_start",
        "scenario": scenario["id"],
        "reserve": scenario["reserve"],
        "budgets": budgets,
        "max_rounds": max_rounds,
    })

    def read_offer(raw: str, buyer_id: str) -> Offer:
        nonlocal reader_calls
        if condition == "structured":
            return parse_structured_offer(raw, buyer_id)
        if condition == "tagged":
            def price_reader(body):
                nonlocal reader_calls
                reader_calls += 1
                read_raw = model(
                    READER_PROMPT,
                    [{"role": "user", "content": f"Read this buyer proposal:\n{body}"}],
                )
                log({"event": "reader", "agent": buyer_id, "raw": read_raw})
                value = parse_reader_output(read_raw)
                if value["performative"] != "propose":
                    raise ValueError("reader did not return propose")
                return value.get("price")
            return parse_tagged_offer(raw, buyer_id, price_reader)
        if condition == "free":
            reader_calls += 1
            read_raw = model(READER_PROMPT, _transcript_messages(transcript))
            log({"event": "reader", "agent": buyer_id, "raw": read_raw})
            value = parse_reader_output(read_raw)
            if value["performative"] == "refuse":
                return Offer(buyer_id, None)
            if value["performative"] != "propose":
                raise ValueError("buyer reader did not return propose or refuse")
            return Offer(buyer_id, _price(value.get("price")))
        raise ValueError(f"unknown condition: {condition}")

    def read_seller(raw: str) -> SellerDecision:
        nonlocal reader_calls
        if condition == "structured":
            return parse_structured_seller_decision(raw)
        if condition == "tagged":
            def accept_reader(body):
                nonlocal reader_calls
                reader_calls += 1
                read_raw = model(
                    READER_PROMPT,
                    [{"role": "user", "content": f"Read this seller acceptance:\n{body}"}],
                )
                log({"event": "reader", "agent": "seller", "raw": read_raw})
                value = parse_reader_output(read_raw)
                return value.get("buyer_id")
            return parse_tagged_seller_decision(raw, accept_reader)
        if condition == "free":
            reader_calls += 1
            read_raw = model(READER_PROMPT, _transcript_messages(transcript))
            log({"event": "reader", "agent": "seller", "raw": read_raw})
            value = parse_reader_output(read_raw)
            performative = value["performative"]
            if performative == "accept-proposal":
                return SellerDecision(performative, _buyer_id(value.get("buyer_id")))
            if performative in {"reject-proposal", "refuse"}:
                return SellerDecision(performative)
            raise ValueError("seller reader returned propose")
        raise ValueError(f"unknown condition: {condition}")

    for round_number in range(1, max_rounds + 1):
        rounds_used = round_number
        offers = {}
        raw_offers = {}

        for buyer_id in BUYERS:
            if not active[buyer_id]:
                offers[buyer_id] = Offer(buyer_id, None)
                raw_offers[buyer_id] = "(inactive)"
                continue
            raw = model(
                buyer_system(buyer_id, scenario, condition),
                histories[buyer_id],
            )
            agent_calls += 1
            raw_offers[buyer_id] = raw
            event = {
                "event": "message",
                "round": round_number,
                "agent": buyer_id,
                "raw": raw,
            }
            transcript.append(event)
            log(event)
            histories[buyer_id].append({"role": "assistant", "content": raw})
            try:
                offer = read_offer(raw, buyer_id)
            except ValueError as error:
                format_errors += 1
                offer = Offer(buyer_id, None)
                log({
                    "event": "parse_error",
                    "round": round_number,
                    "agent": buyer_id,
                    "error": str(error),
                })
            offers[buyer_id] = offer
            if offer.price is None:
                active[buyer_id] = False
            else:
                added_penalty = buyer_penalty(offer.price, budgets[buyer_id])
                penalties[buyer_id] += added_penalty
                log({
                    "event": "parsed_offer",
                    "round": round_number,
                    "buyer_id": buyer_id,
                    "price": offer.price,
                    "valid": offer.price <= budgets[buyer_id],
                    "penalty_added": added_penalty,
                    "penalty_total": penalties[buyer_id],
                })

        if not any(active.values()) and not any(
            offer.price is not None for offer in offers.values()
        ):
            outcome = "no_deal"
            break

        seller_context = {
            buyer_id: {
                "message": raw_offers[buyer_id],
                "parsed_price": offers[buyer_id].price,
                "affinity": affinity[buyer_id],
                "effective_offer": (
                    effective_offer(
                        offers[buyer_id].price,
                        affinity[buyer_id],
                        scenario["reserve"],
                    )
                    if offers[buyer_id].price is not None
                    else None
                ),
            }
            for buyer_id in BUYERS
        }
        histories["seller"].append({
            "role": "user",
            "content": (
                f"Round {round_number} of {max_rounds}. Offers and public affinity:\n"
                + json.dumps(seller_context, ensure_ascii=False, sort_keys=True)
            ),
        })
        seller_raw = model(
            seller_system(scenario, condition),
            histories["seller"],
        )
        agent_calls += 1
        seller_event = {
            "event": "message",
            "round": round_number,
            "agent": "seller",
            "raw": seller_raw,
        }
        transcript.append(seller_event)
        log(seller_event)
        histories["seller"].append({"role": "assistant", "content": seller_raw})
        try:
            decision = read_seller(seller_raw)
            log({
                "event": "parsed_decision",
                "round": round_number,
                "performative": decision.performative,
                "buyer_id": decision.buyer_id,
            })
        except ValueError as error:
            format_errors += 1
            decision = SellerDecision("reject-proposal")
            log({
                "event": "parse_error",
                "round": round_number,
                "agent": "seller",
                "error": str(error),
            })

        eligible = valid_offers(offers, budgets)
        highest_valid = max(
            (offer.price for offer in eligible.values()),
            default=None,
        )
        highest_trace.append(highest_valid)

        if decision.performative == "accept-proposal":
            selected = offers.get(decision.buyer_id)
            if selected is None or selected.price is None:
                format_errors += 1
                log({
                    "event": "parse_error",
                    "round": round_number,
                    "agent": "seller",
                    "error": "accepted buyer has no current offer",
                })
                decision = SellerDecision("reject-proposal")
            else:
                outcome = "deal"
                winner = decision.buyer_id
                price = selected.price
                (
                    seller_reserve_penalty,
                    seller_opportunity_penalty,
                ) = seller_penalties(
                    price,
                    scenario["reserve"],
                    highest_valid,
                )
                break

        if decision.performative == "refuse":
            outcome = "no_deal"
            break

        affinity, awarded = award_affinity(affinity, offers, budgets)
        feedback_event = {
            "event": "round_feedback",
            "round": round_number,
            "highest_valid_offer": highest_valid,
            "affinity_awarded": awarded,
            "affinity": dict(affinity),
            "penalties": dict(penalties),
        }
        log(feedback_event)
        for buyer_id in BUYERS:
            if active[buyer_id]:
                histories[buyer_id].append({
                    "role": "user",
                    "content": (
                        f"Both offers were rejected in round {round_number}. "
                        f"The highest valid offer was {highest_valid}. Your affinity "
                        f"is {affinity[buyer_id]} and your cumulative penalty is "
                        f"{penalties[buyer_id]:.2f}. Submit your next offer."
                    ),
                })

    correct, violation = evaluate_competitive_outcome(
        scenario, outcome, winner, price
    )
    result = {
        "outcome": outcome,
        "winner": winner,
        "price": price,
        "correct": correct,
        "violation": violation,
        "rounds": rounds_used,
        "agent_calls": agent_calls,
        "format_errors": format_errors,
        "reader_calls": reader_calls,
        "total_model_calls": agent_calls + reader_calls,
        "affinity_buyer_1": affinity["buyer_1"],
        "affinity_buyer_2": affinity["buyer_2"],
        "penalty_buyer_1": round(penalties["buyer_1"], 4),
        "penalty_buyer_2": round(penalties["buyer_2"], 4),
        "seller_reserve_penalty": round(seller_reserve_penalty, 4),
        "seller_opportunity_penalty": round(seller_opportunity_penalty, 4),
        "highest_offers": highest_trace,
    }
    log({"event": "episode_result", **result})
    return result
