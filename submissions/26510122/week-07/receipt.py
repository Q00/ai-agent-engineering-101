"""Canonical Round 2 seller receipts. The market kit owns the private key file."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def canonical(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_receipt(shop: str, item: str, price: int, task: str) -> dict:
    if not isinstance(price, int) or isinstance(price, bool):
        raise ValueError("receipt price must be a whole number")
    body = {"shop": shop, "item": item, "price": price, "task": task}
    key_path = Path(os.environ.get("WEEK07_SHOP_KEY", "shop_key.pem"))
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("shop key must be Ed25519")
    return {**body, "sig_seller": base64.b64encode(key.sign(canonical(body))).decode("ascii")}
