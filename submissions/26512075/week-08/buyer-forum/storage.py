"""Small local persistence helpers used by the forum tools."""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


def read_json(path, default=None):
    try:
        with Path(path).open(encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return default


def read_jsonl(path):
    try:
        with Path(path).open(encoding="utf-8") as stream:
            return [json.loads(line) for line in stream if line.strip()]
    except FileNotFoundError:
        return []


def write_text_atomic(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".forum-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path, value):
    write_text_atomic(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_jsonl(path, rows):
    write_text_atomic(path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


@contextmanager
def lock(state):
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True)
    with (state / "forum-tool.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield
