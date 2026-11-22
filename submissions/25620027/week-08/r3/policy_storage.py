"""Locked legacy snapshots with hash-bound, crash-safe expiry sidecars."""

from __future__ import annotations

import errno
import fcntl
import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Concatenate

from pydantic import Field, ValidationError

from market_a2a.models import FrozenModel

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from typing import TextIO


class ExpirySnapshots(FrozenModel):
    """Keep metadata for the current and immediately preceding file snapshot."""

    snapshots: dict[str, dict[str, float]] = Field(default_factory=dict)
    previous_snapshot: str | None = None


def locked[**P, R](
    function: Callable[Concatenate[Path, P], R],
) -> Callable[Concatenate[Path, P], R]:
    """Serialize read/compare/write across processes sharing a policy path."""

    @wraps(function)
    def guarded(path: Path, *args: P.args, **kwargs: P.kwargs) -> R:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(path.suffix + ".lock").open("a") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                return function(path, *args, **kwargs)
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    return guarded


def _sidecar(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".expiries")


def _read(path: Path) -> ExpirySnapshots:
    try:
        return ExpirySnapshots.model_validate_json(_sidecar(path).read_bytes())
    except (OSError, ValidationError, ValueError):
        return ExpirySnapshots()


def read_previous_snapshot(path: Path) -> str | None:
    """Read the prior snapshot retained for scoped layer-aware rollback."""
    return _read(path).previous_snapshot


def read_expiries(path: Path, digest: str) -> dict[str, float]:
    """Return exact metadata only when it belongs to this complete snapshot."""
    return _read(path).snapshots.get(digest, {})


def atomic_snapshot(path: Path, content: str) -> None:
    """Atomically replace one snapshot file."""
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        try:
            _ = stream.write(content)
            stream.flush()
            _ = temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def write_snapshot(path: Path, content: str, expiries: dict[str, float]) -> None:
    """Write metadata first, retaining the old generation across interruption."""
    snapshots: dict[str, dict[str, float]] = {}
    previous_snapshot = path.read_text() if path.exists() else None
    if previous_snapshot is not None:
        previous = hashlib.sha256(previous_snapshot.encode()).hexdigest()
        snapshots[previous] = read_expiries(path, previous)
    digest = hashlib.sha256(content.encode()).hexdigest()
    snapshots[digest] = expiries
    atomic_snapshot(
        _sidecar(path),
        ExpirySnapshots(
            snapshots=snapshots, previous_snapshot=previous_snapshot
        ).model_dump_json(),
    )
    atomic_snapshot(path, content)


def open_reader_lock(path: Path) -> TextIO:
    """Open an existing shared lock without requiring write access."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        return lock_path.open("r")
    except FileNotFoundError:
        return lock_path.open("a")


@contextmanager
def reader_lock(path: Path) -> Generator[bool]:
    """Share an existing lock without writing; immutable snapshots need none."""
    try:
        stream = open_reader_lock(path)
    except OSError as error:
        if error.errno not in {errno.EROFS, errno.EACCES, errno.ENOENT}:
            raise
        yield False
        return
    with stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
        try:
            yield True
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True, slots=True)
class CoherentSnapshot:
    """Main bytes and exact expiry metadata observed together."""

    raw: bytes
    expiries: dict[str, float]


class SnapshotConflictError(ValueError):
    """A lockless reader could not obtain a coherent generation."""


def coherent_snapshot(path: Path) -> CoherentSnapshot:
    """Bounded verified reads for immutable mounts with no readable lock file."""
    for _ in range(3):
        raw = path.read_bytes()
        try:
            metadata = _sidecar(path).read_bytes()
        except FileNotFoundError:
            metadata = None
        if path.read_bytes() != raw:
            continue
        try:
            repeated_metadata = _sidecar(path).read_bytes()
        except FileNotFoundError:
            repeated_metadata = None
        if repeated_metadata != metadata:
            continue
        if metadata is None:
            return CoherentSnapshot(raw, {})
        snapshots = ExpirySnapshots.model_validate_json(metadata).snapshots
        digest = hashlib.sha256(raw).hexdigest()
        if digest in snapshots:
            return CoherentSnapshot(raw, snapshots[digest])
    message = "Policy main and expiry snapshots did not form a coherent generation"
    raise SnapshotConflictError(message)
