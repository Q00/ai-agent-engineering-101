"""Read only the locally authorized key and model; never expose the key in repr."""
from dataclasses import dataclass, field
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    model: str = "gpt-4o-mini"


def load_settings():
    values = {}
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.strip().partition("=")
            if separator and name in {"OPENAI_API_KEY", "AGENT_MODEL"}:
                values[name] = value.strip().strip("\"'")
    return Settings(
        api_key=os.environ.get("OPENAI_API_KEY") or values.get("OPENAI_API_KEY", ""),
        model=os.environ.get("AGENT_MODEL") or values.get("AGENT_MODEL") or "gpt-4o-mini",
    )
