"""Deployment gates keep R3 OpenAI failover explicit and on shared limits."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from deploy.vps.bundle import DIRECTORIES, FILES, build_context
from deploy.vps.launch import validate_paid_launch
from deploy.vps.settings import LaunchSettings
from market_a2a.models import HarnessError
from market_a2a.paid_fallback import DailyQuota


@pytest.fixture
def paid_launch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LaunchSettings:
    """Use only fake route keys and a real persistent quota in this test tree."""
    config = LaunchSettings(
        market_url="https://market.ourolabs.ai",
        public_url="https://ax-week08.example.test",
        remaining_orders=1,
        stop_at=datetime.now(UTC) + timedelta(minutes=5),
        run_directory=tmp_path / "runtime",
        private_directory=tmp_path / "private",
    )
    quota = DailyQuota(config.run_directory / "paid-quota.sqlite3", 1)
    for name in (
        "AX_R3_OPENAI_FALLBACK",
        "AX_R3_OPENAI_MODEL",
        "AX_R3_OPENAI_TRADE_MODEL",
        "AX_R3_STRATEGY_MODEL",
        "AX_R3_TACTICAL_MODEL",
        "AX_R3_DAILY_USD",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in {
        "AX_OPENAI_FALLBACK": "0",
        "AX_PAID_FALLBACK": "anthropic",
        "ANTHROPIC_API_KEY": "offline-anthropic",
        "ANTHROPIC_MODEL": "claude-haiku-5-5",
        "OPENAI_API_KEY": "offline-openai",
        "AX_PAID_QUOTA_PATH": str(quota.path),
        "AX_PAID_DAILY_LIMIT": "1",
    }.items():
        monkeypatch.setenv(name, value)
    return config


def test_explicit_r3_failover_keeps_existing_shared_attempt_usage(
    paid_launch: LaunchSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a spent shared slot and both explicitly approved provider routes.
    quota = DailyQuota(paid_launch.run_directory / "paid-quota.sqlite3", 1)
    _ = quota.reserve()
    monkeypatch.setenv("AX_R3_OPENAI_FALLBACK", "1")
    # When: validate the fallback deployment without contacting either provider.
    validate_paid_launch(paid_launch)
    # Then: adding OpenAI cannot create a fresh daily allowance.
    with pytest.raises(HarnessError, match="limit reached"):
        _ = quota.reserve()


def test_enabled_r3_failover_requires_its_own_openai_key(
    paid_launch: LaunchSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: Claude is configured but the requested secondary route has no key.
    monkeypatch.setenv("AX_R3_OPENAI_FALLBACK", "1")
    monkeypatch.delenv("OPENAI_API_KEY")
    # When/Then: reject before any activation, rather than silently losing failover.
    with pytest.raises(HarnessError, match="OPENAI_API_KEY"):
        validate_paid_launch(paid_launch)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AX_R3_OPENAI_FALLBACK", "true"),
        ("AX_R3_OPENAI_MODEL", "unapproved-model"),
        ("AX_R3_OPENAI_TRADE_MODEL", "unapproved-model"),
    ],
)
def test_r3_failover_rejects_malformed_or_unapproved_configuration(
    paid_launch: LaunchSettings,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    # Given: a requested fallback whose flag or model is not approved.
    monkeypatch.setenv("AX_R3_OPENAI_FALLBACK", "1")
    monkeypatch.setenv(name, value)
    # When/Then: a valid Claude key cannot mask invalid secondary configuration.
    with pytest.raises(HarnessError):
        validate_paid_launch(paid_launch)


def test_r3_openai_can_cover_a_missing_primary_key_when_explicitly_enabled(
    paid_launch: LaunchSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the primary key is unavailable but the authorized secondary exists.
    monkeypatch.setenv("AX_R3_OPENAI_FALLBACK", "1")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    # When: validate the still quota-backed R3 deployment.
    validate_paid_launch(paid_launch)
    # Then: model role pins and the existing ledger are sufficient for startup.
    assert (paid_launch.run_directory / "paid-quota.sqlite3").is_file()


def test_openai_key_alone_does_not_implicitly_enable_r3_failover(
    paid_launch: LaunchSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a secondary key exists, but its opt-in flag remains disabled.
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    # When/Then: preserve the original explicit-primary-key requirement.
    with pytest.raises(HarnessError, match="Haiku API key"):
        validate_paid_launch(paid_launch)


def test_enabled_r3_failover_keeps_the_primary_role_allowlist(
    paid_launch: LaunchSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a valid secondary cannot authorize an unapproved primary role model.
    monkeypatch.setenv("AX_R3_OPENAI_FALLBACK", "1")
    monkeypatch.setenv("AX_R3_STRATEGY_MODEL", "gpt-5-mini")
    # When/Then: the unchanged Claude role allowlist still rejects the request.
    with pytest.raises(HarnessError, match="approved Claude models"):
        validate_paid_launch(paid_launch)


def test_enabled_r3_only_models_still_require_the_shared_quota_ledger(
    paid_launch: LaunchSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: legacy paid fallback is off while explicit R3 OpenAI remains enabled.
    monkeypatch.setenv("AX_PAID_FALLBACK", "none")
    monkeypatch.setenv("AX_R3_OPENAI_FALLBACK", "1")
    monkeypatch.setenv("AX_PAID_QUOTA_PATH", str(paid_launch.run_directory / "missing"))
    # When/Then: R3 calls cannot bypass the approved persistent attempt ledger.
    with pytest.raises(HarnessError, match="shared R3 paid quota ledger"):
        validate_paid_launch(paid_launch)


@pytest.mark.parametrize(
    ("name", "value"),
    [("AX_R3_DAILY_USD", "51"), ("AX_PAID_DAILY_LIMIT", "5001")],
)
def test_r3_only_failover_preserves_dollar_and_attempt_limit_validation(
    paid_launch: LaunchSettings,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    # Given: R3 has an explicit secondary while the legacy paid selector is off.
    monkeypatch.setenv("AX_PAID_FALLBACK", "none")
    monkeypatch.setenv("AX_R3_OPENAI_FALLBACK", "1")
    monkeypatch.setenv(name, value)
    # When/Then: this new route cannot bypass the existing limit validators.
    with pytest.raises(HarnessError, match="between"):
        validate_paid_launch(paid_launch)


def test_r3_failover_does_not_reconfigure_the_legacy_paid_backend(
    paid_launch: LaunchSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the R3 flag is not an authorization for the separate legacy selector.
    monkeypatch.setenv("AX_R3_OPENAI_FALLBACK", "1")
    monkeypatch.setenv("AX_PAID_FALLBACK", "openai")
    # When/Then: keep legacy traffic on its existing explicitly approved route.
    with pytest.raises(HarnessError, match="deployment authorizes"):
        validate_paid_launch(paid_launch)


def test_bundle_keeps_the_optional_openai_env_outside_runtime_source(
    tmp_path: Path,
) -> None:
    # Given: a source tree with provider secrets in likely build-adjacent locations.
    source = tmp_path / "bundle/submissions/me/week-08"
    source.mkdir(parents=True)
    for name in FILES:
        _ = (source / name).write_text("local public source")
    for name in DIRECTORIES:
        (source / name).mkdir()
    dockerfile = source / "deploy/vps/Dockerfile"
    dockerfile.parent.mkdir()
    _ = dockerfile.write_text("FROM scratch\n")
    kit = source.parents[2] / "scripts/market_kit.py"
    kit.parent.mkdir()
    original = Path(__file__).resolve().parents[1]
    _ = kit.write_bytes((original.parents[2] / "scripts/market_kit.py").read_bytes())
    for parent in (source, source / "private", source / "r3"):
        parent.mkdir(exist_ok=True)
        _ = (parent / "openai-fallback.env").write_text(
            "OPENAI_API_KEY=offline-secret-placeholder\n"
        )
    # When: build the production source allowlist.
    destination = tmp_path / "runtime"
    manifest = build_context(source, destination)
    # Then: no provider env or its fake secret enters the deployable image context.
    assert not any(name.endswith(".env") for name in manifest)
    files = [path for path in destination.rglob("*") if path.is_file()]
    assert not any(path.suffix == ".env" for path in files)
    assert all(b"offline-secret-placeholder" not in path.read_bytes() for path in files)
