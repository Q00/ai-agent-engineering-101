"""Create a fresh event log; redact credentials before first persistence."""
from datetime import datetime, timezone
import json
from pathlib import Path

from negotiation.config import ROOT


class RunLog:
    def __init__(self, path, secret=""):
        self.path = Path(path).resolve()
        if not self.path.is_relative_to(ROOT / "logs"):
            raise ValueError("Log must stay inside this submission's logs directory")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.open("x", encoding="utf-8").close()
        self._secret = secret

    def record(self, event, **fields):
        line = json.dumps({"at": datetime.now(timezone.utc).isoformat(), "event": event, **fields}, ensure_ascii=False)
        if self._secret:
            line = line.replace(self._secret, "[REDACTED]")
        with self.path.open("a", encoding="utf-8") as output:
            output.write(line + "\n")
