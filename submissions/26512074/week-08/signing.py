"""The market's canonical Ed25519 receipt format; never log private keys."""
import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

FIELDS = ("shop", "item", "price", "task")


def whole(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or not float(value).is_integer():
        raise ValueError("market prices must be nonnegative whole numbers")
    return int(value)


def body(receipt):
    result = {k: receipt[k] for k in FIELDS}
    result["price"] = whole(result["price"])
    return result


def canon(receipt):
    return json.dumps(body(receipt), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sign_receipt(receipt, key_path="shop_key.pem"):
    result = body(receipt)
    key = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    result["sig_seller"] = base64.b64encode(key.sign(canon(result))).decode("ascii")
    return result


def verify_receipt(receipt, pubkey):
    Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey, validate=True)).verify(
        base64.b64decode(receipt["sig_seller"], validate=True), canon(receipt))
