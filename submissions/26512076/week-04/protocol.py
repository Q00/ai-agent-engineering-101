import json
import re

ALLOWED_ACTS = {
    "propose",
    "accept-proposal",
    "reject-proposal",
    "refuse",
}


def valid_price(value):
    return isinstance(value, int) and not isinstance(value, bool)


def parse_structured(message):
    """JSON을 직접 파싱한다. Reader 호출 없음."""
    try:
        data = json.loads(message)
        performative = data.get("performative")

        if performative not in ALLOWED_ACTS:
            raise ValueError("invalid performative")

        content = data.get("content", {})
        price = content.get("price")

        if performative == "propose" and not valid_price(price):
            raise ValueError("propose requires an integer price")

        return {
            "performative": performative,
            "price": price,
            "format_error": 0,
            "reader_calls": 0,
        }

    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return {
            "performative": None,
            "price": None,
            "format_error": 1,
            "reader_calls": 0,
        }


def parse_tagged(message, history, reader_call):
    """맨 앞 태그는 정규식, propose 가격은 Reader로 읽는다."""
    match = re.match(
        r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)",
        message,
    )

    if not match:
        return {
            "performative": None,
            "price": None,
            "format_error": 1,
            "reader_calls": 0,
        }

    performative = match.group(1)

    if performative != "propose":
        return {
            "performative": performative,
            "price": None,
            "format_error": 0,
            "reader_calls": 0,
        }

    try:
        reader_output = reader_call(history, message)
        data = json.loads(reader_output)
        price = data.get("price")

        if not valid_price(price):
            raise ValueError("reader did not return an integer price")

        return {
            "performative": performative,
            "price": price,
            "format_error": 0,
            "reader_calls": 1,
        }

    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return {
            "performative": performative,
            "price": None,
            "format_error": 1,
            "reader_calls": 1,
        }


def parse_free(message, history, reader_call):
    """Reader가 전체 대화를 보고 마지막 메시지를 해석한다."""
    try:
        reader_output = reader_call(history, message)
        data = json.loads(reader_output)

        performative = data.get("performative")
        price = data.get("price")

        if performative not in ALLOWED_ACTS:
            raise ValueError("invalid performative")

        if performative == "propose" and not valid_price(price):
            raise ValueError("propose requires an integer price")

        return {
            "performative": performative,
            "price": price,
            "format_error": 0,
            "reader_calls": 1,
        }

    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return {
            "performative": None,
            "price": None,
            "format_error": 1,
            "reader_calls": 1,
        }


def parse_message(condition, message, history, reader_call):
    if condition == "free":
        return parse_free(message, history, reader_call)

    if condition == "tagged":
        return parse_tagged(message, history, reader_call)

    if condition == "structured":
        return parse_structured(message)

    raise ValueError(f"Unknown condition: {condition}")