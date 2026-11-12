"""Offline cryptographic receipt contract and private-key loading checks."""

from __future__ import annotations

import base64
import json
import stat
from typing import TYPE_CHECKING

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from pydantic import JsonValue, ValidationError

from market_a2a.receipts import ReceiptBody, ReceiptKeyError, ReceiptSigner

if TYPE_CHECKING:
    from pathlib import Path


def test_signature_when_unicode_receipt_uses_exact_canonical_body() -> None:
    # Given
    key = ed25519.Ed25519PrivateKey.generate()
    signer = ReceiptSigner(key)
    # When
    receipt = signer.sign(shop="작은 상점", item="lamp", price=120, task="task-7")
    # Then
    expected = '{"item":"lamp","price":120,"shop":"작은 상점","task":"task-7"}'.encode()
    assert receipt.canonical() == expected
    assert type(receipt.price) is int
    assert len(receipt.model_dump()) == 5
    key.public_key().verify(
        base64.b64decode(receipt.sig_seller, validate=True), expected
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("shop", "other"),
        ("item", "chair"),
        ("price", 121),
        ("task", "another-task"),
    ],
)
def test_signature_rejected_when_signed_field_tampered(
    field: str, value: str | int
) -> None:
    # Given
    key = ed25519.Ed25519PrivateKey.generate()
    receipt = ReceiptSigner(key).sign(
        shop="shop", item="lamp", price=120, task="task-7"
    )
    body = {
        "shop": receipt.shop,
        "item": receipt.item,
        "price": receipt.price,
        "task": receipt.task,
    }
    body[field] = value
    modified = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    # When / Then
    with pytest.raises(InvalidSignature):
        key.public_key().verify(base64.b64decode(receipt.sig_seller), modified)


def test_signature_rejected_when_verified_with_another_key() -> None:
    # Given
    receipt = ReceiptSigner(ed25519.Ed25519PrivateKey.generate()).sign(
        shop="shop",
        item="lamp",
        price=120,
        task="task-7",
    )
    other = ed25519.Ed25519PrivateKey.generate().public_key()
    # When / Then
    with pytest.raises(InvalidSignature):
        other.verify(base64.b64decode(receipt.sig_seller), receipt.canonical())


@pytest.mark.parametrize("price", [True, -1, 120.0, 120.5, "120", 1_000_000_000])
def test_price_rejected_when_not_a_small_nonnegative_integer(
    price: JsonValue,
) -> None:
    # Given
    data = {"shop": "shop", "item": "lamp", "price": price, "task": "task-7"}
    # When / Then
    with pytest.raises(ValidationError):
        _ = ReceiptBody.model_validate(data)


def test_key_load_when_existing_pem_has_broad_permissions(tmp_path: Path) -> None:
    # Given
    key = ed25519.Ed25519PrivateKey.generate()
    path = tmp_path / "shop_key.pem"
    _ = path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.chmod(0o644)
    # When
    signer = ReceiptSigner.load(path)
    # Then
    receipt = signer.sign(shop="shop", item="lamp", price=120, task="task-7")
    key.public_key().verify(base64.b64decode(receipt.sig_seller), receipt.canonical())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "PRIVATE KEY" not in repr(signer)


def test_key_missing_raises_without_creating_file(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "missing.pem"
    # When / Then
    with pytest.raises(ReceiptKeyError, match=r"existing readable shop_key\.pem"):
        _ = ReceiptSigner.load(path)
    assert not path.exists()


@pytest.mark.parametrize("wrong_algorithm", [False, True])
def test_key_invalid_raises_safe_error(
    tmp_path: Path, *, wrong_algorithm: bool
) -> None:
    # Given
    path = tmp_path / "shop_key.pem"
    contents = b"invalid secret material"
    if wrong_algorithm:
        contents = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        ).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    _ = path.write_bytes(contents)
    # When / Then
    with pytest.raises(ReceiptKeyError, match="Ed25519") as raised:
        _ = ReceiptSigner.load(path)
    assert "invalid secret material" not in str(raised.value)
    assert "PRIVATE KEY" not in str(raised.value)
