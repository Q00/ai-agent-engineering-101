"""Exercise deployment preparation without contacting any service."""

from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

import anyio
import pytest

from deploy.vps.bundle import build_context
from deploy.vps.launch import activate, validate_paid_launch
from deploy.vps.settings import LaunchSettings
from market_a2a.models import HarnessError
from market_a2a.paid_fallback import DailyQuota
from r3.kit_pin import KIT_SHA256


def settings(tmp_path: Path) -> LaunchSettings:
    return LaunchSettings(
        market_url="https://market.ourolabs.ai",
        public_url="https://ax-week08.example.test",
        remaining_orders=1,
        stop_at=datetime.now(UTC) + timedelta(minutes=5),
        run_directory=tmp_path / "runtime",
        private_directory=tmp_path / "private",
    )


def test_default_activation_refuses_before_files_or_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a valid finite config but no explicit activation flag.
    config = settings(tmp_path)
    monkeypatch.delenv("AX_R3_ACTIVATE", raising=False)
    # When: accidentally invoke the executable activation function.
    with pytest.raises(HarnessError):
        _ = anyio.run(activate, config)
    # Then: no credential reads, runtime files, joins or model calls occurred.
    assert not config.run_directory.exists()


def test_haiku_launch_requires_shared_ledger_and_preserves_existing_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: explicitly approved Haiku fallback and the R3 shared volume.
    config = settings(tmp_path)
    quota = DailyQuota(config.run_directory / "paid-quota.sqlite3", 1)
    _ = quota.reserve()
    for key, value in {
        "AX_OPENAI_FALLBACK": "0",
        "AX_PAID_FALLBACK": "anthropic",
        "ANTHROPIC_API_KEY": "offline",
        "ANTHROPIC_MODEL": "claude-haiku-5-5",
        "AX_PAID_QUOTA_PATH": str(quota.path),
        "AX_PAID_DAILY_LIMIT": "1",
    }.items():
        monkeypatch.setenv(key, value)
    # When: validate deployment without activating or contacting a service.
    validate_paid_launch(config)
    # Then: existing attempts cannot be reset by changing the paid provider.
    with pytest.raises(HarnessError, match="limit reached"):
        _ = DailyQuota(quota.path, 1).reserve()


def test_haiku_launch_refuses_missing_ledger_before_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a key but no approved persistent quota mount.
    config = settings(tmp_path)
    monkeypatch.setenv("AX_OPENAI_FALLBACK", "0")
    monkeypatch.setenv("AX_PAID_FALLBACK", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("AX_PAID_QUOTA_PATH", str(config.run_directory / "missing"))
    # When/Then: fail closed before any quota reset, model call or market entry.
    with pytest.raises(HarnessError, match="shared R3 paid quota ledger"):
        validate_paid_launch(config)
    assert not config.run_directory.exists()


@pytest.mark.parametrize("offset", [-1, 17 * 86400])
def test_expired_and_overlong_windows_are_rejected(tmp_path: Path, offset: int) -> None:
    # Given: a stale deadline or a window longer than the 16-day Round 3 bound.
    now = datetime.now(UTC)
    config = settings(tmp_path).model_copy(
        update={"stop_at": now + timedelta(seconds=offset)}
    )
    # When/Then: no command can start by treating an old approval as a new window.
    with pytest.raises(HarnessError):
        _ = config.seconds_remaining(now)


def test_bundle_excludes_secrets_and_pins_the_original_kit(tmp_path: Path) -> None:
    # Given: the real source tree, not a hand-crafted source list assertion.
    source = Path(__file__).resolve().parents[1]
    destination = tmp_path / "build"
    # When: create the same build context that the future VPS image will consume.
    manifest = build_context(source, destination)
    # Then: every byte matches its manifest and no private/runtime/test material ships.
    assert manifest["scripts/market_kit.py"] == KIT_SHA256
    assert (destination / "deploy/vps/Dockerfile").is_file()
    for name, digest in manifest.items():
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == digest
        assert not any(
            part in {"logs", ".market", "tests", ".venv", "private"}
            for part in Path(name).parts
        )
        assert not name.endswith((".pem", ".env", ".sqlite", ".log"))


def test_deadline_stops_a_real_local_child_without_market_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a fake private identity and a sleeper replacing only the kit command.
    app = tmp_path / "build"
    _ = build_context(Path(__file__).resolve().parents[1], app)
    config = settings(tmp_path).model_copy(
        update={"stop_at": datetime.now(UTC) + timedelta(seconds=0.5)}
    )
    config.private_directory.mkdir()
    for name in ("shop_key.pem", "buyer_key.pem"):
        _ = (config.private_directory / name).write_text("local-test-placeholder")
    for key, value in {
        "AX_R3_ACTIVATE": "1",
        "AX_MODEL_PROVIDER": "openrouter-free",
        "AX_OPENAI_FALLBACK": "0",
        "MARKET_OWNER": "local-only-placeholder",
        "OPENROUTER_API_KEY": "local-only-placeholder",
    }.items():
        monkeypatch.setenv(key, value)

    def sleeper(_settings: LaunchSettings, _app: Path) -> tuple[str, ...]:
        return (sys.executable, "-c", "import time; time.sleep(60)")

    monkeypatch.setattr(LaunchSettings, "command", sleeper)
    # When: exercise the real launch/timeout/finally-stop path with no market code.
    started = datetime.now(UTC)
    code = anyio.run(partial(activate, config, app))
    # Then: it exits at the deadline and leaves the supplied identity unchanged.
    assert code == 0
    assert (datetime.now(UTC) - started).total_seconds() < 5
    copied = config.run_directory / "shop_key.pem"
    assert copied.is_file()
    assert not copied.is_symlink()
    assert copied.read_text() == "local-test-placeholder"
    assert oct(copied.stat().st_mode & 0o777) == "0o600"
    assert (
        config.private_directory / "shop_key.pem"
    ).read_text() == "local-test-placeholder"
