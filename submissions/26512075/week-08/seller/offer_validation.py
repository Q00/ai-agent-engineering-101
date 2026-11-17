"""Validate model-extracted offers against the current buyer message."""
import re


class OfferParsingError(ValueError):
    """The harness could not produce a valid, grounded negotiation decision."""


def positive_int(value):
    if type(value) is not int or value <= 0:
        raise ValueError(f"Expected a positive integer, got {value!r}")
    return value


def numeric_amounts(text):
    # Keep signs and decimals so -100 or 100.50 cannot become an offer of 100.
    tokens = re.findall(r"(?<![A-Za-z0-9_])[-+]?\d[\d,.]*(?![A-Za-z0-9_])", text)
    tokens = [token.removesuffix(".") for token in tokens]
    return {int(token.replace(",", "")) for token in tokens
            if re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", token)}


async def negotiate_followup(request, incoming_text, reserve, current_ask, decide, logger):
    plain = incoming_text.strip()
    plain_offer = (int(plain.replace(",", "")) if re.fullmatch(
        r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", plain) else None)
    if plain_offer is not None:
        request = {**request, "numericOffer": plain_offer}
    amounts = numeric_amounts(incoming_text)
    for attempt in range(2):
        choice = {}
        try:
            choice = await decide(request)
            if not isinstance(choice, dict):
                choice = {}
                raise ValueError("Harness must return a decision object")
            if "parsed_offer" not in choice:
                raise ValueError("Missing parsed_offer; use null when no current buyer price is identifiable")
            value = choice["parsed_offer"]
            # Zero is a legacy no-offer sentinel, never an acceptable payment.
            absent = value is None or (type(value) is int and value == 0)
            if absent:
                if plain_offer is not None and plain_offer > 0:
                    raise ValueError(f"The numeric buyer offer is {plain_offer}; return that parsed_offer")
                logger.info("clarification required: no identifiable positive buyer offer")
                return {"action": "clarify", "parsed_offer": None}, None
            offer = positive_int(value)
            if offer not in amounts:
                raise ValueError("parsed_offer must appear as an explicit price in the current buyer message; otherwise use null")
            if plain_offer is not None and offer != plain_offer:
                raise ValueError(f"The numeric buyer offer is {plain_offer}, not {offer}")
            action = choice.get("action")
            if offer >= current_ask and offer >= reserve and action != "accept":
                raise ValueError(f"Buyer offered {offer}, meeting your current ask {current_ask}; return accept with price {offer}")
            if action == "accept":
                price = positive_int(choice.get("price"))
                if price != offer or price < reserve:
                    raise ValueError("Acceptance must equal the buyer offer and meet reserve")
            elif action == "offer":
                positive_int(choice.get("price"))
            elif action not in ("reject", "walk_away"):
                raise ValueError("Unknown seller action")
        except ValueError as exc:
            logger.warning("invalid harness decision attempt=%s action=%s price=%s parsed_offer=%s feedback=%s",
                           attempt + 1, choice.get("action"), choice.get("price"), choice.get("parsed_offer"), exc)
            if attempt == 1:
                raise OfferParsingError(str(exc)) from exc
            request = {**request, "validationFeedback": str(exc)}
            continue
        logger.info("decision action=%s price=%s parsed_offer=%s attempt=%s",
                    action, choice.get("price"), offer, attempt + 1)
        return choice, offer
    raise AssertionError("Unreachable decision loop")
