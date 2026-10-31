"""Canonical Week 07 receipts signed with a locally provisioned shop key."""

from __future__ import annotations

import base64
import json
import stat
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, override

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import Field

from market_a2a.models import FrozenModel, HarnessError

if TYPE_CHECKING:
    from pathlib import Path

PRIVATE_KEY_MODE: Final = 0o600


class ReceiptBody(FrozenModel):
    """Validate whole prices within the seller's nine-digit offer boundary."""

    shop: str = Field(min_length=1, strict=True)
    item: str = Field(min_length=1, strict=True)
    price: int = Field(ge=0, le=999_999_999, strict=True)
    task: str = Field(min_length=1, strict=True)

    def canonical(self) -> bytes:
        """Encode exactly the four signed fields, even on a signed subclass."""
        return json.dumps(
            {
                "shop": self.shop,
                "item": self.item,
                "price": self.price,
                "task": self.task,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")


class SignedReceipt(ReceiptBody):
    """The market receipt with a base64 Ed25519 seller signature."""

    sig_seller: str


class ReceiptKeyError(HarnessError):
    """A provisioned shop key cannot be loaded safely."""

    def __init__(self, path: Path, detail: str) -> None:
        """Keep only a path and safe diagnostic, never private key bytes."""
        self.path: Path = path
        super().__init__(detail)

    @override
    def __str__(self) -> str:
        """Identify the failing local key without leaking its contents."""
        return f"Cannot load shop signing key {self.path}: {self.detail}"


@dataclass(frozen=True, slots=True)
class ReceiptSigner:
    """Own one Ed25519 key; provisioning remains the market kit's responsibility."""

    key: Ed25519PrivateKey = field(repr=False)

    @classmethod
    def load(cls, path: Path) -> ReceiptSigner:
        """Restrict an existing key to owner read/write, then parse it once."""
        try:
            if stat.S_IMODE(path.stat().st_mode) != PRIVATE_KEY_MODE:
                path.chmod(PRIVATE_KEY_MODE)
            if stat.S_IMODE(path.stat().st_mode) != PRIVATE_KEY_MODE:
                detail = "Private key permissions must be 0600."
                raise ReceiptKeyError(path, detail)
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        except OSError as error:
            detail = (
                "An existing readable shop_key.pem is required; run the market kit."
            )
            raise ReceiptKeyError(path, detail) from error
        except (ValueError, TypeError, UnsupportedAlgorithm) as error:
            detail = "Expected an unencrypted Ed25519 PEM private key."
            raise ReceiptKeyError(path, detail) from error
        if not isinstance(key, Ed25519PrivateKey):
            detail = "The shop key must use Ed25519."
            raise ReceiptKeyError(path, detail)
        return cls(key)

    def sign(self, *, shop: str, item: str, price: int, task: str) -> SignedReceipt:
        """Sign the actual task and accepted integer price, without SKU metadata."""
        body = ReceiptBody(shop=shop, item=item, price=price, task=task)
        signature = base64.b64encode(self.key.sign(body.canonical())).decode("ascii")
        return SignedReceipt(
            shop=body.shop,
            item=body.item,
            price=body.price,
            task=body.task,
            sig_seller=signature,
        )
