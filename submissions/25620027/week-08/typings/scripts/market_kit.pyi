"""Type boundary for the unmodified instructor kit at the pinned commit."""

from typing import Protocol

import httpx2
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import JsonValue
from starlette.requests import Request
from starlette.responses import Response

__file__: str
KIT_VERSION: str

class MarketPort(Protocol):
    # This signature mirrors the required instructor adapter ABI.
    def call(self, method: str, path: str, role: str = ..., *, json: dict[str, JsonValue]) -> httpx2.Response: ...

class AcpShop:
    def __init__(self, key: Ed25519PrivateKey, market: MarketPort) -> None: ...
    async def handle(self, req: Request, path: str, body: bytes, public: str) -> Response: ...

def canon(d: JsonValue) -> bytes: ...
def pub_b64(key: Ed25519PrivateKey) -> str: ...
def jwt_sign(key: Ed25519PrivateKey, payload: dict[str, JsonValue]) -> str: ...
def jwt_payload(token: str, pubkey_b64: str) -> dict[str, JsonValue] | None: ...
# Existing upstream public API, deliberately not redesigned here.
def acp_pay(o: dict[str, JsonValue], shop_url: str, shop_pub: str, receipt: dict[str, JsonValue], buyer_key: Ed25519PrivateKey) -> dict[str, JsonValue] | None: ...
