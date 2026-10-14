"""Live-failure regressions for propagated typed exceptions."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest

from market_a2a.catalog import CatalogDataError, MissingProductError
from market_a2a.models import HarnessError

if TYPE_CHECKING:
    from collections.abc import Generator


@contextmanager
def managed() -> Generator[None]:
    """Exercise contextlib's traceback assignment during exception propagation."""
    yield


@pytest.mark.parametrize(
    "error",
    [
        HarnessError("model failed"),
        MissingProductError("absent"),
        CatalogDataError("bad"),
    ],
)
def test_typed_error_when_propagated_through_managed_resource(
    error: HarnessError | MissingProductError | CatalogDataError,
) -> None:
    # Given the typed errors that were frozen in the first live challenge.
    with pytest.raises(type(error)) as caught, managed():
        # When a context manager propagates the original exception.
        raise error
    # Then the original exception survives instead of a TypeError masking it.
    assert caught.value is error
