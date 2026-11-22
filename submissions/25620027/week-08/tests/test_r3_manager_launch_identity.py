"""The production manager launch uses the market shop identity for stockouts."""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, Unpack

import anyio
from pydantic import TypeAdapter

from deploy.vps.bundle import build_context
from deploy.vps.launch import activate
from deploy.vps.settings import LaunchSettings
from market_a2a.models import FrozenModel
from r3.stockouts import load_stockouts
from tests.r3_stock import CURTAIN
from tests.test_r3_stockouts import ledger

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import pytest
    from anyio.abc import Process


class SpawnOptions(TypedDict, total=False):
    """Only options the real launch passes to its existing process boundary."""

    cwd: Path
    stdout: None
    stderr: None
    env: Mapping[str, str]


class Observation(FrozenModel):
    """Read the real manager CLI's instantiated identities from the child."""

    shop_name: str
    buyer_name: str
    shop_url: str


def test_launched_manager_counts_stockouts_under_registered_shop_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: supplies use the kit's registered name and sales use its public URL.
    source = Path(__file__).resolve().parents[1]
    app = tmp_path / "app"
    _ = build_context(source, app)
    now = time.time()
    config = LaunchSettings(
        market_url="https://market.ourolabs.ai",
        public_url="https://ax-week08.example.test",
        remaining_orders=1,
        stop_at=datetime.now(UTC) + timedelta(seconds=30),
        run_directory=tmp_path / "run",
        private_directory=tmp_path / "private",
    )
    kit_command = config.command(app)
    registered = kit_command[kit_command.index("--name") + 1]
    config.run_directory.mkdir()
    _ = ledger(
        config.run_directory / "r3-ledger.sqlite",
        [
            (
                now - 5 * 3600,
                "supply",
                registered,
                "",
                {"sku": CURTAIN, "units": ["u1", "u2"]},
            ),
            (now - 4.95 * 3600, "settle", "https://rival", CURTAIN, {"unit": "r0"}),
            (now - 4.9 * 3600, "settle", config.public_url, CURTAIN, {"unit": "u1"}),
            (now - 4.8 * 3600, "settle", config.public_url, CURTAIN, {"unit": "u2"}),
            (now - 3 * 3600, "settle", "https://rival", CURTAIN, {"unit": "r1"}),
            (now - 2 * 3600, "settle", "https://rival", CURTAIN, {"unit": "r2"}),
            (now - 3600, "settle", "https://rival", CURTAIN, {"unit": "r3"}),
        ],
    )
    config.private_directory.mkdir()
    for name in ("shop_key.pem", "buyer_key.pem"):
        _ = (config.private_directory / name).write_text("local-test-placeholder")
    for key, value in {
        "AX_R3_ACTIVATE": "1",
        "AX_MODEL_PROVIDER": "openrouter-free",
        "AX_PAID_FALLBACK": "none",
        "AX_OPENAI_FALLBACK": "0",
        "MARKET_OWNER": "local-test-placeholder",
    }.items():
        monkeypatch.setenv(key, value)
    original_open = anyio.open_process
    # Keep actual CLI parsing/Manager construction/report writing; replace its
    # network loop only, and replace the market kit with the local deadline sleeper.
    probe = f"""
import json
import anyio
import typer
from r3.manager import Manager, main
async def once(self):
    self.record_stockouts({{}}, {now!r})
    (self.run_dir / 'identity-observed.json').write_text(json.dumps({{
        'shop_name': self.shop_name, 'buyer_name': self.buyer_name,
        'shop_url': self.shop_url}}))
    await anyio.sleep_forever()
Manager.loop = once
typer.run(main)
"""

    async def local_process(
        command: Sequence[str], **kwargs: Unpack[SpawnOptions]
    ) -> Process:
        if "r3.manager" in command:
            command = (command[0], "-c", probe, *command[3:])
        return await original_open(command, **kwargs)

    def wait_for_manager(_config: LaunchSettings, _app: Path) -> tuple[str, ...]:
        ready = config.run_directory / "identity-observed.json"
        # Finish the fake kit when the real CLI child has published its result.
        # CPU-limited Linux imports must not race a short unrelated deadline.
        wait = f"""
from pathlib import Path
import time
ready = Path({str(ready)!r})
end = time.monotonic() + 15
while not ready.exists() and time.monotonic() < end:
    time.sleep(0.01)
raise SystemExit(0 if ready.exists() else 2)
"""
        return (sys.executable, "-c", wait)

    monkeypatch.setattr(anyio, "open_process", local_process)
    monkeypatch.setattr(LaunchSettings, "command", wait_for_manager)
    # When: activate() launches its production manager command into a real child.
    result = anyio.run(partial(activate, config, app))
    # Then: the parsed shop name matches the official kit name and supplies join.
    observed = TypeAdapter(Observation).validate_json(
        (config.run_directory / "identity-observed.json").read_bytes()
    )
    assert result == 0
    assert observed.shop_name == registered
    assert observed.buyer_name == registered
    assert observed.shop_url == config.public_url
    assert load_stockouts(config.run_directory / "r3-stockouts.json", now) == {
        CURTAIN: (1.0, round(2 / 3, 3))
    }
