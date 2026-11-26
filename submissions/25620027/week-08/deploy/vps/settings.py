"""Validate a future launch without reading any credential contents."""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, Field, field_validator

from market_a2a.models import FrozenModel, HarnessError
from r3.kit_pin import KIT_SHA256

MAX_WINDOW_DAYS: Final = 16
MAX_ORDERS: Final = 200_000


class LaunchSettings(FrozenModel):
    """Require a fresh, finite authorization rather than reusing old deadlines."""

    market_url: Literal["https://market.ourolabs.ai"]
    public_url: str
    port: int = Field(default=18088, strict=True, ge=1024, le=65535)
    # 0 asks the kit for unlimited orders until stop_at; Round 3 lasts 14 days.
    remaining_orders: int = Field(strict=True, ge=0, le=MAX_ORDERS)
    stop_at: AwareDatetime
    run_directory: Path = Path("/data/r3")
    private_directory: Path = Path("/private")

    @field_validator("public_url")
    @classmethod
    def public_origin(cls, value: str) -> str:
        """Require a single HTTPS origin; it must already route to the kit port."""
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.port not in (None, 443)
        ):
            message = "public_url must be an HTTPS origin"
            raise ValueError(message)
        return value.rstrip("/")

    def seconds_remaining(self, now: datetime) -> float:
        """Reject expired or unreasonably long activation windows."""
        seconds = (self.stop_at - now).total_seconds()
        if not 0 < seconds <= timedelta(days=MAX_WINDOW_DAYS).total_seconds():
            message = f"stop_at must be in the next {MAX_WINDOW_DAYS} days"
            raise HarnessError(message)
        return seconds

    def command(self, app: Path) -> tuple[str, ...]:
        """Use the official proxy path, which owns both A2A and ACP authorization."""
        return (
            sys.executable,
            str(app / "r3" / "kit_entry.py"),
            "join",
            "--dir",
            str(self.run_directory),
            "--market",
            self.market_url,
            "--name",
            "25620027",
            "--public-url",
            self.public_url,
            "--port",
            str(self.port),
            "--orders",
            str(self.remaining_orders),
        )


def verify_kit(app: Path) -> None:
    """Check the vendored instructor source before starting any subprocess."""
    kit = app / "scripts" / "market_kit.py"
    if hashlib.sha256(kit.read_bytes()).hexdigest() != KIT_SHA256:
        message = "Pinned kit v4 hash mismatch"
        raise HarnessError(message)
