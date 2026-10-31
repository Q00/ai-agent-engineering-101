"""Create a credential-free build context from an explicit source allowlist."""

from __future__ import annotations

import hashlib
import json
import shutil
from typing import TYPE_CHECKING, Final

from market_a2a.models import HarnessError
from r3.kit_pin import KIT_SHA256

if TYPE_CHECKING:
    from pathlib import Path

FILES: Final = (
    "harness_app.py",
    "http_client.py",
    "catalog.json",
    "requirements-kit.lock",
)
DIRECTORIES: Final = ("market_a2a", "r3", "deploy", "assets")
SUFFIXES: Final = frozenset((".py", ".json", ".html", ".js", ".css", ".yml"))


def build_context(source: Path, destination: Path) -> dict[str, str]:
    """Only source and the pinned public kit enter the image; no runtime state."""
    if destination.exists():
        message = "Build context destination must be new"
        raise HarnessError(message)
    instructor = source.parents[2] / "scripts" / "market_kit.py"
    if hashlib.sha256(instructor.read_bytes()).hexdigest() != KIT_SHA256:
        message = "Instructor kit hash differs from the audited v4 source"
        raise HarnessError(message)
    selected = [source / name for name in FILES]
    selected.extend(
        path
        for name in DIRECTORIES
        for path in (source / name).rglob("*")
        if path.is_file()
        and path.suffix in SUFFIXES
        and "__pycache__" not in path.parts
    )
    selected.append(source / "deploy" / "vps" / "Dockerfile")
    if any(path.is_symlink() for path in selected):
        message = "Source symlinks cannot enter the build context"
        raise HarnessError(message)
    destination.mkdir(parents=True)
    manifest: dict[str, str] = {}
    for path in selected:
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copyfile(path, target)
        manifest[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    kit_target = destination / "scripts" / "market_kit.py"
    kit_target.parent.mkdir()
    _ = shutil.copyfile(instructor, kit_target)
    manifest["scripts/market_kit.py"] = KIT_SHA256
    _ = (destination / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    return manifest
