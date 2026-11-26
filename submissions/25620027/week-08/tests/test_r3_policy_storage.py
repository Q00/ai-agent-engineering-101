"""Policy revisions and independently expiring overlays."""

from __future__ import annotations

import errno
import fcntl
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from threading import Event
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from r3 import policy, policy_storage
from r3.policy import (
    Policy,
    PolicyConflictError,
    PolicyHistory,
    TacticalOverlay,
    apply_changes,
    expire_tactical,
    load_history,
    load_policy,
    rollback_policy,
    save_bands,
    save_policy,
    save_tactical,
)


def test_independent_expiry_and_noop(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    _ = save_tactical(path, {"seller.accept_margin": 0.16}, "seller", now=100, ttl=100)
    _ = save_tactical(
        path, {"buyer.late_accept_share": 0.49}, "buyer", now=150, ttl=100
    )
    _ = save_tactical(path, {"seller.accept_margin": 0.16}, "same", now=160, ttl=100)
    assert load_history(path).field_expiries == {
        "seller.accept_margin": 200,
        "buyer.late_accept_share": 250,
    }
    assert load_policy(path, 210).seller.accept_margin == 0.15
    assert load_policy(path, 210).buyer.late_accept_share == 0.49
    _ = expire_tactical(path, now=210)
    assert load_history(path).field_expiries == {"buyer.late_accept_share": 250}


def test_strategy_preserves_overlay_and_legacy_json(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    _ = save_tactical(path, {"seller.accept_margin": 0.16}, "seller", now=100, ttl=100)
    history = load_history(path)
    _ = save_policy(
        path,
        apply_changes(history.base(), {"buyer.order_seconds": 19}),
        "buyer",
        expected_version=history.current.version,
    )
    assert load_policy(path, 150).seller.accept_margin == 0.16
    assert load_policy(path, 201).seller.accept_margin == 0.15
    raw = PolicyHistory.model_validate_json(path.read_bytes())
    assert raw.field_expiries == {}
    assert raw.tactical is not None
    assert raw.tactical.expires_at == 200
    assert load_history(path).current.version == 3


def test_legacy_common_expiry(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    history = PolicyHistory(
        current=Policy(version=10),
        strategy=Policy(),
        tactical=TacticalOverlay(
            changes={"seller.accept_margin": 0.16}, expires_at=200
        ),
    )
    _ = path.write_text(history.model_dump_json())
    assert load_history(path).field_expiries == {"seller.accept_margin": 200}
    assert load_policy(path, 150).seller.accept_margin == 0.16


def _compete(path: Path) -> bool:
    try:
        _ = save_policy(path, Policy(), "writer", expected_version=1)
    except PolicyConflictError:
        return False
    return True


def test_process_revision_conflict(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    with ProcessPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(_compete, [path, path]))
    assert sorted(outcomes) == [False, True]
    assert load_history(path).current.version == 2


def test_band_revision_and_scoped_rollback(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    _ = save_policy(
        path,
        apply_changes(Policy(), {"buyer.order_seconds": 19}),
        "buyer",
        expected_version=1,
    )
    _ = save_bands(path, {}, expected_version=2)
    assert load_history(path).current.version == 3
    with pytest.raises(PolicyConflictError):
        _ = save_tactical(path, {}, "stale", expected_version=2)
    _ = save_policy(
        path,
        apply_changes(
            load_history(path).base(),
            {"buyer.order_seconds": 20, "seller.markup_a": 0.94},
        ),
        "two",
    )
    _ = rollback_policy(
        path,
        "buyer only",
        fields=("buyer.order_seconds",),
        expected_values={"buyer.order_seconds": 20},
        expected_version=4,
    )
    assert load_history(path).current.buyer.order_seconds == 19
    assert load_history(path).current.seller.markup_a == 0.94
    with pytest.raises(PolicyConflictError):
        _ = rollback_policy(
            path,
            "stale field",
            fields=("seller.markup_a",),
            expected_values={"seller.markup_a": 0.95},
        )


def test_interruption_after_sidecar_preserves_old_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "policy.json"
    _ = save_tactical(path, {"seller.accept_margin": 0.16}, "seller", now=100, ttl=100)
    original = policy_storage.atomic_snapshot

    def interrupted(target: Path, content: str) -> None:
        if target == path:
            raise FileNotFoundError
        original(target, content)

    monkeypatch.setattr(policy_storage, "atomic_snapshot", interrupted)
    with pytest.raises(FileNotFoundError):
        _ = save_tactical(
            path, {"buyer.late_accept_share": 0.49}, "buyer", now=150, ttl=100
        )
    assert load_history(path).current.version == 2
    assert load_history(path).field_expiries == {"seller.accept_margin": 200}


def test_rollback_preserves_previous_field_deadline(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    _ = save_tactical(path, {"seller.accept_margin": 0.16}, "first", now=100, ttl=100)
    _ = save_tactical(
        path,
        {"seller.accept_margin": 0.155, "buyer.late_accept_share": 0.49},
        "second",
        now=150,
        ttl=100,
    )
    _ = rollback_policy(
        path,
        "seller",
        fields=("seller.accept_margin",),
        expected_values={"seller.accept_margin": 0.155},
        expected_version=3,
    )
    assert load_history(path).field_expiries == {
        "seller.accept_margin": 200,
        "buyer.late_accept_share": 250,
    }
    assert load_policy(path, 210).seller.accept_margin == 0.15
    assert load_policy(path, 210).buyer.late_accept_share == 0.49


def test_downgrade_projection_never_extends_expiry(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    _ = save_tactical(path, {"seller.accept_margin": 0.16}, "first", now=100, ttl=100)
    _ = save_tactical(
        path, {"buyer.late_accept_share": 0.49}, "second", now=150, ttl=100
    )
    projected = PolicyHistory.model_validate_json(path.read_bytes())
    assert projected.current.version == 3
    assert projected.tactical is not None
    assert projected.tactical.expires_at == 200
    assert projected.field_expiries == {}
    assert load_history(path).field_expiries["buyer.late_accept_share"] == 250


def test_reader_holds_snapshot_lock_across_sidecar_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "policy.json"
    _ = save_tactical(
        path, {"buyer.late_accept_share": 0.49}, "first", now=100, ttl=100
    )
    _ = save_tactical(path, {"seller.accept_margin": 0.16}, "second", now=150, ttl=100)
    main_read = Event()
    resume_reader = Event()
    original = policy_storage.read_expiries

    def paused_sidecar(target: Path, digest: str) -> dict[str, float]:
        main_read.set()
        assert resume_reader.wait(timeout=5)
        return original(target, digest)

    monkeypatch.setattr(policy, "read_expiries", paused_sidecar)
    with ThreadPoolExecutor(max_workers=1) as pool:
        reader = pool.submit(load_policy, path, 210)
        try:
            assert main_read.wait(timeout=5)
            # Two independent writer descriptors cannot enter while main and
            # sidecar form an unfinished read; no scheduling sleeps are needed.
            for _ in range(2):
                with (
                    path.with_suffix(path.suffix + ".lock").open("a") as writer,
                    pytest.raises(BlockingIOError),
                ):
                    fcntl.flock(writer.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            resume_reader.set()
        observed = reader.result(timeout=5)
    assert observed.seller.accept_margin == 0.16
    monkeypatch.setattr(policy, "read_expiries", original)
    for _ in range(2):
        _ = save_bands(path, {})
    assert load_policy(path, 210).seller.accept_margin == 0.16
    assert load_history(path).field_expiries["seller.accept_margin"] == 250


@pytest.mark.parametrize("retain_lock", [False, True])
def test_readonly_snapshot_retains_independent_deadlines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, retain_lock: bool
) -> None:
    path = tmp_path / "policy.json"
    _ = save_tactical(
        path, {"buyer.late_accept_share": 0.49}, "first", now=100, ttl=100
    )
    _ = save_tactical(path, {"seller.accept_margin": 0.16}, "second", now=150, ttl=100)
    lock_path = path.with_suffix(path.suffix + ".lock")
    if retain_lock:
        lock_path.chmod(0o444)
    else:
        lock_path.unlink()
        monkeypatch.setattr(policy_storage, "open_reader_lock", _deny_readonly_lock)
    path.chmod(0o444)
    path.with_suffix(path.suffix + ".expiries").chmod(0o444)
    tmp_path.chmod(0o555)
    try:
        assert load_policy(path, 210).seller.accept_margin == 0.16
        assert load_history(path).field_expiries["seller.accept_margin"] == 250
        assert lock_path.exists() == retain_lock
    finally:
        tmp_path.chmod(0o755)


def test_readonly_snapshot_digest_miss_refuses_early_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "policy.json"
    _ = save_tactical(
        path, {"buyer.late_accept_share": 0.49}, "first", now=100, ttl=100
    )
    _ = save_tactical(path, {"seller.accept_margin": 0.16}, "second", now=150, ttl=100)
    _ = path.with_suffix(path.suffix + ".expiries").write_text('{"snapshots": {}}')
    path.with_suffix(path.suffix + ".lock").unlink()
    tmp_path.chmod(0o555)
    monkeypatch.setattr(policy_storage, "open_reader_lock", _deny_readonly_lock)
    try:
        with pytest.raises(PolicyConflictError, match="inconsistent"):
            _ = load_policy(path, 210)
    finally:
        tmp_path.chmod(0o755)


def test_readonly_legacy_without_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "policy.json"
    _ = path.write_text(PolicyHistory(current=Policy(version=42)).model_dump_json())
    tmp_path.chmod(0o555)
    monkeypatch.setattr(policy_storage, "open_reader_lock", _deny_readonly_lock)
    try:
        assert load_policy(path).version == 42
        assert not path.with_suffix(path.suffix + ".lock").exists()
    finally:
        tmp_path.chmod(0o755)


def _deny_readonly_lock(path: Path) -> NoReturn:
    raise OSError(errno.EROFS, "Read-only filesystem", str(path))


def test_lockless_snapshot_retries_two_interleaved_publications(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "policy.json"
    _ = save_tactical(
        path, {"buyer.late_accept_share": 0.49}, "first", now=100, ttl=100
    )
    _ = save_tactical(path, {"seller.accept_margin": 0.16}, "second", now=150, ttl=100)
    original = type(path).read_bytes
    reads = 0

    def interleaved_read(target: Path) -> bytes:
        nonlocal reads
        if target == path:
            reads += 1
            if reads == 2:
                _ = save_bands(path, {})
                _ = save_bands(path, {})
        return original(target)

    monkeypatch.setattr(policy_storage, "open_reader_lock", _deny_readonly_lock)
    monkeypatch.setattr(type(path), "read_bytes", interleaved_read)
    observed = load_policy(path, 210)
    assert observed.version == 5
    assert observed.seller.accept_margin == 0.16
    assert reads >= 4
