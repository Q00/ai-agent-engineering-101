"""Week-07 Ed25519 receipts. Sign only the four canonical course fields."""
import base64
import json
import math
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ROOT = Path(__file__).resolve().parent
FIELDS = ("shop", "item", "price", "task")


def canonical_receipt(receipt):
    body = {field: receipt[field] for field in FIELDS}
    price = body["price"]
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0 or int(price) != price:
        raise ValueError("Receipt price must be a positive whole number")
    body["price"] = int(price)  # protobuf DataPart numbers arrive as doubles
    if any(not isinstance(body[k], str) or not body[k].strip() for k in ("shop", "item", "task")):
        raise ValueError("Receipt identity fields must be nonempty strings")
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def load_shop_key(path=None):
    # market_kit/market_test create this private key before starting the seller.
    key = serialization.load_pem_private_key(Path(path or ROOT / "shop_key.pem").read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Expected Ed25519 shop key")
    return key


def sign_receipt(receipt, key):
    return {**receipt, "price": int(receipt["price"]),
            "sig_seller": base64.b64encode(key.sign(canonical_receipt(receipt))).decode("ascii")}


def verify_signature(receipt, public_key):
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
        key.verify(base64.b64decode(receipt["sig_seller"], validate=True), canonical_receipt(receipt))
        return True
    except Exception:
        return False
