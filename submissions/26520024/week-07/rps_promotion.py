"""Opt-in quote promotion only; this module never accepts a purchase."""

import hashlib
import hmac
import json
import re
import secrets


MOVES = ("rock", "paper", "scissors")
BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
PROMOTION_KEYS = {
    "task_id", "reserve", "dealer_move", "nonce", "commitment", "used"
}


def _commitment(task, move, nonce):
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must be a nonempty string")
    if not isinstance(move, str) or move not in MOVES:
        raise ValueError("invalid dealer move")
    if not isinstance(nonce, str) or re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise ValueError("nonce must be 16 bytes encoded as lowercase hex")
    body = {"task": task, "move": move, "nonce": nonce}
    canonical = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_promotion(promotion):
    if not isinstance(promotion, dict) or set(promotion) != PROMOTION_KEYS:
        raise ValueError("invalid promotion fields")
    if type(promotion["reserve"]) is not int or promotion["reserve"] < 0:
        raise ValueError("reserve must be a nonnegative integer")
    if type(promotion["used"]) is not bool:
        raise ValueError("used must be a boolean")
    expected = _commitment(
        promotion["task_id"], promotion["dealer_move"], promotion["nonce"]
    )
    commitment = promotion["commitment"]
    if (not isinstance(commitment, str)
            or re.fullmatch(r"[0-9a-f]{64}", commitment) is None
            or not hmac.compare_digest(commitment, expected)):
        raise ValueError("promotion does not match its prior commitment")


def new_promotion(task_id, reserve):
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a nonempty string")
    if type(reserve) is not int or reserve < 0:
        raise ValueError("reserve must be a nonnegative integer")
    dealer_move = secrets.choice(MOVES)
    nonce = secrets.token_hex(16)
    return {
        "task_id": task_id, "reserve": reserve, "dealer_move": dealer_move,
        "nonce": nonce, "commitment": _commitment(task_id, dealer_move, nonce),
        "used": False,
    }


def parse_move(text):
    if not isinstance(text, str):
        return None
    match = re.fullmatch(r"\s*RPS\s+(rock|paper|scissors)\s*", text, re.IGNORECASE)
    return match.group(1).lower() if match else None


def is_game_request(text):
    return isinstance(text, str) and re.search(r"\bRPS\b", text, re.IGNORECASE) is not None


def resolve(promotion, buyer_move, current_ask):
    """Return a new consumed state and a quote, never a purchase acceptance."""
    _validate_promotion(promotion)
    if promotion["used"]:
        raise ValueError("promotion has already been used")
    if not isinstance(buyer_move, str) or buyer_move not in MOVES:
        raise ValueError("invalid buyer move")
    if type(current_ask) is not int or current_ask < promotion["reserve"]:
        raise ValueError("current_ask must be an integer at or above reserve")
    dealer_move = promotion["dealer_move"]
    if buyer_move == dealer_move:
        outcome = "draw"
    elif BEATS[buyer_move] == dealer_move:
        outcome = "win"
    else:
        outcome = "loss"
    price = promotion["reserve"] if outcome == "win" else current_ask
    updated = dict(promotion, used=True)
    result = {
        "task": promotion["task_id"], "buyer_move": buyer_move,
        "dealer_move": dealer_move, "nonce": promotion["nonce"],
        "commitment": promotion["commitment"], "outcome": outcome, "price": price,
    }
    return updated, result


def verify_result(result):
    """Check the revealed move hash only, not price, outcome, or prior publication.

    A caller must also compare commitment with the one it recorded before play.
    Price and outcome rules are enforced separately by resolve().
    """
    if not isinstance(result, dict):
        return False
    try:
        expected = _commitment(result["task"], result["dealer_move"], result["nonce"])
        commitment = result["commitment"]
        return isinstance(commitment, str) and hmac.compare_digest(commitment, expected)
    except (KeyError, TypeError, ValueError):
        return False


def invitation(promotion):
    _validate_promotion(promotion)
    return (
        "Optional RPS promotion: send RPS rock, RPS paper, or RPS scissors. "
        "If you win, your quote becomes the reserve price; on a loss or draw, "
        "the current asking price stays unchanged. One round per task. "
        "Playing does not buy anything: a separate numeric agreement is required. "
        "The dealer reveals its committed move and nonce after you play. "
        "Commitment: " + promotion["commitment"]
    )
