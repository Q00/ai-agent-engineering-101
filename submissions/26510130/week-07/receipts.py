"""Week 07 — signing and verifying a receipt.

The market counts a deal only when the receipt is signed by the shop over the
canonical JSON of {shop, item, price, task}. "Canonical" has to mean the same
bytes on both sides, so this module is the single place that decides it:

    json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

Two traps the assignment warns about, both handled in `whole()`:

  * A2A data Parts carry numbers as doubles, so a price that left the shop as
    120 arrives as 120.0. Signing 120.0 and verifying 120 fails. Every numeric
    field is normalised to an int before it is signed or checked.
  * The signature covers only those four keys, in that order after sorting.
    `sig_seller` itself is never part of the signed body.
"""
import base64
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

RECEIPT_KEYS = ("shop", "item", "price", "task")


def whole(v):
    """120.0 -> 120. A float that is not whole, or anything else, is left as is."""
    if isinstance(v, bool):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def canon(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def body_of(receipt: dict) -> dict:
    return {k: whole(receipt.get(k)) for k in RECEIPT_KEYS}


def load_key(path="shop_key.pem") -> Ed25519PrivateKey:
    """Load the shop key, creating it on first use.

    The kit creates this file too; whichever runs first wins and the other
    reuses it, which is what keeps a shop's identity stable across restarts.
    """
    p = Path(path)
    if not p.exists():
        k = Ed25519PrivateKey.generate()
        p.write_bytes(k.private_bytes(serialization.Encoding.PEM,
                                      serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
        gi = Path(".gitignore")
        lines = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
        if "*.pem" not in lines:
            gi.write_text("\n".join(lines + ["*.pem"]) + "\n", encoding="utf-8")
    return serialization.load_pem_private_key(p.read_bytes(), password=None)


def sign_receipt(body: dict, key: Ed25519PrivateKey) -> dict:
    """Return {shop, item, price, task, sig_seller}."""
    b = {k: whole(body.get(k)) for k in RECEIPT_KEYS}
    return {**b, "sig_seller": base64.b64encode(key.sign(canon(b))).decode()}


def verify_receipt(receipt: dict, public_key) -> bool:
    try:
        public_key.verify(base64.b64decode(receipt["sig_seller"]),
                          canon(body_of(receipt)))
        return True
    except (InvalidSignature, KeyError, ValueError, TypeError):
        return False
