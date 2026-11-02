"""Deterministic reserve and signing authority for the seller.

The negotiator never supplies receipt fields to the signer.  They are bound at
construction time, so a prompt cannot swap the item, task, or shop at signing.
"""
import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class ReserveSigner:
    def __init__(self, shop, item, reserve, task, key_path):
        if not isinstance(reserve, int) or reserve <= 0:
            raise ValueError("reserve must be a positive integer")
        self.shop = shop
        self.item = item
        self.reserve = reserve
        self.task = task
        self.key_path = key_path

    def protect(self, price, purpose):
        if not isinstance(price, (int, float)) or isinstance(price, bool):
            raise ValueError(f"{purpose} must be numeric")
        price = int(round(price))
        if price < self.reserve:
            print(f"[guard] {purpose} {price} below reserve {self.reserve}; raised to reserve", flush=True)
            return self.reserve
        return price

    def sign(self, agreed_price):
        if not isinstance(agreed_price, int) or isinstance(agreed_price, bool):
            raise ValueError("agreed price must be a whole number")
        if agreed_price < self.reserve:
            raise ValueError(f"refusing to sign {agreed_price} below reserve {self.reserve}")
        body = {"shop": self.shop, "item": self.item, "price": agreed_price, "task": self.task}
        key = serialization.load_pem_private_key(Path(self.key_path).read_bytes(), password=None)
        return {**body, "sig_seller": base64.b64encode(key.sign(canonical(body))).decode()}
