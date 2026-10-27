"""Read an already-private mounted key without trying to change its filesystem."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from market_a2a.receipts import ReceiptSigner

if TYPE_CHECKING:
    import pytest


def test_private_readonly_mount_does_not_require_chmod(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the production private volume is read-only and its existing key is0600.
    key = Ed25519PrivateKey.generate()
    path = tmp_path / "shop_key.pem"
    _ = path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.chmod(0o600)

    def readonly(_path: Path, _mode: int) -> None:
        message = "Read-only filesystem"
        raise OSError(message)

    monkeypatch.setattr(Path, "chmod", readonly)
    # When: the seller loads the existing private key.
    signer = ReceiptSigner.load(path)
    # Then: the key stays private and a real signature still verifies.
    receipt = signer.sign(shop="Local", item="lamp", price=100, task="local-task")
    key.public_key().verify(base64.b64decode(receipt.sig_seller), receipt.canonical())
    assert receipt.price == 100
    assert "PRIVATE KEY" not in repr(signer)
