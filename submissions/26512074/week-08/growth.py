"""Loyalty, zero-price digital gifts and explicitly logged course-game cheap talk."""
import base64
import json
from pathlib import Path
import re
import time

ROOT = Path(__file__).resolve().parent
TACTIC_SCHEMA = {"type": "object", "additionalProperties": False,
    "properties": {"gift_enabled": {"type": "boolean"}, "game_bluff": {"type": "boolean"},
                   "exclusive_discount": {"type": "integer"}, "repeat_discount": {"type": "integer"}},
    "required": ["gift_enabled", "exclusive_discount", "repeat_discount", "game_bluff"]}


def validate_tactics(config):
    if set(config) != set(TACTIC_SCHEMA["required"]):
        raise ValueError("unknown growth-policy fields")
    if type(config["gift_enabled"]) is not bool or type(config["game_bluff"]) is not bool:
        raise ValueError("growth toggles must be boolean")
    if type(config["exclusive_discount"]) is not int or not 0 <= config["exclusive_discount"] <= 3:
        raise ValueError("pledge-only discount is capped at three")
    if type(config["repeat_discount"]) is not int or not 0 <= config["repeat_discount"] <= 5:
        raise ValueError("verified-repeat discount is capped at five")


def settings():
    config = json.loads((ROOT / "growth-policy.json").read_text(encoding="utf-8"))
    runtime = ROOT / "runtime-policy.json"
    if runtime.exists():
        config = json.loads(runtime.read_text(encoding="utf-8")).get("policy", {}).get("growth", config)
    validate_tactics(config)
    return config


def pledged(text, metadata=None):
    # A claim, not proof of exclusivity. Its benefit is deliberately small.
    if metadata and metadata.get("exclusive_pledge") is True:
        return True
    return bool(re.search(r"\bI (?:will|promise to) (?:buy|purchase|source) (?:only|exclusively) from (?:you|your shop)\b", text, re.I))


def opening_message(item, previous, config=None):
    config = config or settings()
    text = f"I want to buy a {item}. Quote the current total; no extra fees."
    meta = {}
    if config["game_bluff"]:
        text += " In this course game, I will buy only from your shop if you give me your exclusive-customer discount. Please quote your loyalty price."
        meta["exclusive_pledge"] = True
    token = previous.get("loyalty_token")
    if isinstance(token, str) and len(token) <= 4096:
        meta["loyalty_token"] = token
    audit = {"mode": "game_bluff" if config["game_bluff"] else "neutral",
             "claim_enforced": False, "signed_exclusive_contract": False,
             "subsequent_supplier_selection": "independent"}
    return text, meta, audit


def _encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_token(receipt, key, now=None):
    now = int(time.time() if now is None else now)
    body = {"program": "guild-loyalty/v1", "shop": receipt["shop"], "source_task": receipt["task"],
            "issued": now, "expires": now + 86400}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return _encode(raw) + "." + _encode(key.sign(raw))


def verify_token(token, public_key, shop, now=None):
    if not isinstance(token, str) or len(token) > 4096:
        raise ValueError("invalid loyalty token")
    encoded, signature = token.split(".")
    raw = _decode(encoded)
    public_key.verify(_decode(signature), raw)
    body = json.loads(raw)
    now = time.time() if now is None else now
    if body.get("program") != "guild-loyalty/v1" or body.get("shop") != shop or not body["issued"] <= now < body["expires"]:
        raise ValueError("expired or mismatched loyalty token")
    return body["source_task"]


def discount_price(ask, reserve, requested):
    discount = max(0, min(int(requested), int(ask) - int(reserve)))
    return int(ask) - discount, discount


def courtesy_gift(item, token=None):
    gift = {"kind": "courtesy-note", "monetary_value": 0,
            "message": f"A tiny desk cat to keep your {item} company. Thank you from the Price Alchemist Guild!",
            "ascii_art": [" /\\_/\\", "( o.o )", " > ^ <"],
            "notice": "Fictional digital keepsake. No extra fee, payment credit, rating obligation or physical warranty."}
    if token:
        gift["loyalty_token"] = token
        gift["loyalty_notice"] = "One use at the next quote, only after this source task settles; not cash or identity proof."
    return gift
