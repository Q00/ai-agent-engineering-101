"""Validate model-extracted offers against the current buyer message."""
import re
import logging
import json


def explicit_offer(text, sku=None):
    """Accept only complete, unconditional price phrases or a strict offer object."""
    amount = r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)"
    plain = text.strip()
    if plain.startswith("{") or plain.startswith("```json"):
        if plain.startswith("```json"):
            match = re.fullmatch(r"```json\s*\n(.*?)\n```", plain, re.DOTALL)
            if not match:
                return None
            plain = match[1]
        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate offer field")
                result[key] = value
            return result
        try:
            data = json.loads(plain, object_pairs_hook=unique_pairs)
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict) or set(data) - {"action", "price", "sku", "qty", "currency"}:
            return None
        if data.get("action") not in ("buy", "accept", "offer"):
            return None
        if "sku" in data and (sku is None or data["sku"] != sku):
            return None
        if "qty" in data and (type(data["qty"]) is not int or data["qty"] != 1):
            return None
        if "currency" in data and data["currency"] != "credits":
            return None
        price = data.get("price")
        return price if type(price) is int and price > 0 else None
    patterns = [rf"({amount})", rf"I (?:accept|offer)\s+({amount})(?:\s+credits)?[.!]?",
                rf"I(?: want to| will|'ll) buy(?: it)? (?:at|for)\s+({amount})(?:\s+credits)?[.!]?",
                rf"({amount})(?:크레딧)?\s*에\s*(?:살게요|살게용|구매할게요|구매하겠습니다)[.!]?",
                rf"({amount})(?:크레딧)?\s*(?:수락합니다|수락할게요|제안합니다)[.!]?"]
    for pattern in patterns:
        match = re.fullmatch(pattern, plain, re.IGNORECASE)
        if match:
            price = int(match[1].replace(",", ""))
            return price if price > 0 else None
    return None


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


class NegotiationLogger(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return f"task={self.extra['task']} sku={self.extra['sku']} {msg}", kwargs


async def negotiate_followup(request, incoming_text, reserve, current_ask, decide, logger):
    logger = NegotiationLogger(logger, {"task": request.get("taskId", "unknown"),
                                       "sku": request.get("itemId", "unknown")})
    seen = request.get("seenBuyerOffers", [])
    def hold(offer, reason):
        ask = max(current_ask, reserve)
        logger.info("hold reason=%s offer=%s ask=%s reserve=%s model_called=false",
                    reason, offer, ask, reserve)
        return {"action": "offer", "price": ask, "parsed_offer": offer, "reason": reason}, offer

    plain = incoming_text.strip()
    plain_offer = (int(plain.replace(",", "")) if re.fullmatch(
        r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", plain) else None)
    if plain_offer is not None and plain_offer >= max(reserve, current_ask):
        logger.info("accept explicit buyer offer=%s without model", plain_offer)
        return {"action": "accept", "price": plain_offer, "parsed_offer": plain_offer}, plain_offer
    if current_ask <= reserve:
        identified = explicit_offer(incoming_text, request.get("itemId"))
        if identified is not None:
            if identified >= max(reserve, current_ask):
                logger.info("accept explicit buyer offer=%s without model", identified)
                return {"action": "accept", "price": identified, "parsed_offer": identified}, identified
            return hold(identified, "reserve_reached")
        logger.info("clarification required at reserve=%s model_called=false", reserve)
        return {"action": "clarify", "parsed_offer": None, "reason": "reserve_reached"}, None
    if plain_offer is not None and plain_offer > 0 and plain_offer in seen:
        return hold(plain_offer, "repeated_offer")
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
            if offer >= max(current_ask, reserve):
                return {"action": "accept", "price": offer, "parsed_offer": offer}, offer
            if offer in seen:
                return hold(offer, "repeated_offer")
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
