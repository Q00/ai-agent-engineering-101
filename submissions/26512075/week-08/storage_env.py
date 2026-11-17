"""Load only seller storage settings; never print or copy connection secrets."""
from pathlib import Path
import os

from dotenv import dotenv_values

STORAGE_KEYS = ("REDIS_URL", "TASK_TTL_SECONDS")


def load_storage_environment(project_root: Path | None = None) -> None:
    root = (project_root or Path(__file__).resolve().parent).resolve()
    # Deployment can use week-08/.env; this repository keeps .env at its root.
    # Resolve from the source file, not the shell's working directory.
    repository = next((p for p in root.parents if (p / ".git").exists()), None)
    paths = [root / ".env"]
    if repository is not None:
        paths.append(repository / ".env")
    for path in paths:
        if not path.is_file():
            continue
        # Preserve literal dollar signs in URLs/passwords. No shell execution.
        values = dotenv_values(path, interpolate=False)
        for key in STORAGE_KEYS:
            value = values.get(key)
            if key not in os.environ and value is not None:
                os.environ[key] = value
