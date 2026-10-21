"""Copy only an existing OpenRouter key into an exclusive private model file."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path


def main(source: Path, destination: Path) -> None:
    """Preserve the OpenAI rollback file and never overwrite existing credentials."""
    values: dict[str, str] = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        name, _, raw = line.partition("=")
        name = name.removeprefix("export ").strip()
        if name in {"OPENROUTER_API_KEY", "OPENAI_API_KEY"}:
            tokens = shlex.split(raw)
            if tokens:
                values[name] = tokens[0]
    key = values.get("OPENROUTER_API_KEY") or values.get("OPENAI_API_KEY")
    if not key or not key.startswith("sk-or-") or "\n" in key:
        message = "Expected one existing OpenRouter credential"
        raise ValueError(message)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        _ = stream.write("AX_MODEL_PROVIDER=openrouter-free\n")
        _ = stream.write("OPENROUTER_API_KEY=" + key + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chown(destination, 10001, 10001)
    _ = sys.stdout.write("Configured private OpenRouter free-only environment\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
