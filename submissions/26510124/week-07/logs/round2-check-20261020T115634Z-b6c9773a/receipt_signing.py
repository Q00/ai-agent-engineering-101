"""Week-07 LAB 2 receipt format; sign integer prices before A2A serialization."""
import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def load_shop_key(path):
    key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("shop_key.pem must contain an Ed25519 private key")
    return key


def sign_receipt(key, *, shop, item, price, task):
    if isinstance(price, bool) or not isinstance(price, int) or price < 0:
        raise ValueError("receipt price must be a nonnegative integer")
    receipt = {"shop": shop, "item": item, "price": price, "task": task}
    payload = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {**receipt, "sig_seller": base64.b64encode(key.sign(payload)).decode("ascii")}
