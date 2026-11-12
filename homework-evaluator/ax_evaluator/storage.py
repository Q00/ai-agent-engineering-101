"""Canonical serialization and evidence references."""

import hashlib
import json
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_new(path, value):
    """Never overwrite an earlier evaluation or its evidence."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = value if isinstance(value, str) else canonical(value)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)


def evidence(snapshot, path, start=1, end=None):
    text = snapshot["files"][path]["text"]
    count = len(text.splitlines())
    if not count:
        return []
    return [{"path": path, "start": start, "end": end or count}]


def validate_refs(refs, snapshot):
    if not isinstance(refs, list):
        raise ValueError("evidence must be a list")
    for ref in refs:
        if set(ref) != {"path", "start", "end"}:
            raise ValueError("evidence needs path, start and end")
        doc = snapshot["files"].get(ref["path"])
        if doc is None or doc["text"] is None:
            raise ValueError("evidence path is not readable in this snapshot")
        start, end = ref["start"], ref["end"]
        if (
            type(start) is not int
            or type(end) is not int
            or not 1 <= start <= end <= len(doc["text"].splitlines())
        ):
            raise ValueError("evidence line range is outside its source")
