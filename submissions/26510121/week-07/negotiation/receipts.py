"""Round-2 canonical receipt signing; private key contents are never logged."""
import base64
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from negotiation.a2a_client import ProtocolError, integer_money
from negotiation.config import ROOT


FIELDS = ("shop", "item", "price", "task")


def canonical_receipt(receipt):
    try:
        body = {field: receipt[field] for field in FIELDS}
    except (KeyError, TypeError):
        raise ProtocolError("receipt_missing_signed_fields") from None
    for field in ("shop", "item", "task"):
        if not isinstance(body[field], str) or not body[field].strip():
            raise ProtocolError("receipt_invalid_signed_fields")
    body["price"] = integer_money(body["price"])
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_receipt(receipt, key_path=ROOT / "shop_key.pem"):
    # Normalize before signing because A2A decodes whole numbers as doubles.
    result = {field: receipt[field] for field in FIELDS}
    result["price"] = integer_money(result["price"])
    key = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Shop key must be Ed25519")
    result["sig_seller"] = base64.b64encode(key.sign(canonical_receipt(result))).decode("ascii")
    return result


def verify_receipt(receipt, public_key):
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
        signature = base64.b64decode(receipt["sig_seller"], validate=True)
        key.verify(signature, canonical_receipt(receipt))
        return True
    except (InvalidSignature, KeyError, ValueError, TypeError, ProtocolError):
        return False
