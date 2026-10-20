from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import harness_app
from market_a2a.market_buyer import MarketRequest, MarketResult

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

    from market_a2a.buyer import BuyerHarness


def test_cli_resolves_paths_before_isolation_and_prints_final_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a relative listing and state path in the caller's directory.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "local-cli-test")
    listing = tmp_path / "shops.json"
    _ = listing.write_text("[]", encoding="utf-8")
    observed: list[tuple[Path, Path, Path]] = []

    async def market_buy(
        _harness: BuyerHarness,
        request: MarketRequest,
        _emit: Callable[[str], None],
    ) -> MarketResult:
        observed.append((Path.cwd(), request.shops_path, request.memory_path))
        return MarketResult(
            shop="https://shop.example/", receipt=None, stars=3, review="No deal."
        )

    monkeypatch.setattr(harness_app, "market_buy", market_buy)

    # When the actual CLI crosses its isolated working-directory boundary.
    result = CliRunner().invoke(
        harness_app.app,
        [
            "market-buyer",
            "--shops",
            "shops.json",
            "--item",
            "lamp",
            "--budget",
            "120",
            "--memory",
            "state/memory.json",
        ],
    )

    # Then persistent paths retain their caller meaning and JSON is last.
    assert result.exit_code == 0, result.output
    assert len(observed) == 1
    isolated_cwd, shops_path, memory_path = observed[0]
    assert isolated_cwd != tmp_path
    assert shops_path == listing
    assert memory_path == tmp_path / "state/memory.json"
    assert Path.cwd() == tmp_path
    assert MarketResult.model_validate_json(result.stdout.splitlines()[-1]).stars == 3
