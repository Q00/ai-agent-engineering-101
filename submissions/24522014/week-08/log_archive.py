"""Keep every generation of the kit log. The kit overwrites market-kit.log.1 at each rotation, so this copies it once per generation.

The name comes from the source's size and modified time, so the check that runs on every restock hook is one stat and one exists().
A copy only gets its final name after the source is seen unchanged and the copy's hash and size match. No model call and no .market state.
Called from the restock hook, which needs no kit restart. Failures are recorded and never raised. Commits stay a person's step.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = Path("logs/market-kit.log.1")
ARCHIVE = Path("logs/market-kit-archive")
FAILURES = Path("logs/kit-log-archive-failures.jsonl")
PARTIAL = ".in-progress.partial"
CHUNK = 1 << 20


class SourceChanged(Exception):
    """The source moved or changed during the copy."""


def fingerprint(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def archive_name(size, mtime_ns):
    stamp = datetime.fromtimestamp(mtime_ns // 1_000_000_000, timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"market-kit-{stamp}-{mtime_ns % 1_000_000_000:09d}-{size}.log"


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_with_hash(source, target):
    digest = hashlib.sha256()
    with open(source, "rb") as reader, open(target, "wb") as writer:
        for chunk in iter(lambda: reader.read(CHUNK), b""):
            writer.write(chunk)
            digest.update(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return digest.hexdigest()


def archive_once(root=ROOT):
    """Returns the final path when a new copy was made, else None. Raises SourceChanged or OSError."""
    root = Path(root)
    source = root / SOURCE
    if not source.exists():
        return None
    size, mtime_ns = fingerprint(source)
    folder = root / ARCHIVE
    final = folder / archive_name(size, mtime_ns)
    if final.exists():
        return None
    folder.mkdir(parents=True, exist_ok=True)
    partial = folder / PARTIAL
    try:
        digest = copy_with_hash(source, partial)
        if fingerprint(source) != (size, mtime_ns):
            raise SourceChanged("the source changed during the copy")
        if partial.stat().st_size != size or sha256_of(partial) != digest:
            raise SourceChanged("the copy does not match what was read")
        (folder / (final.name + ".sha256")).write_text(f"{digest}  {final.name}\n", encoding="ascii", newline="\n")
        os.replace(partial, final)
    finally:
        try:
            partial.unlink()
        except OSError:
            pass
    return final


def record_failure(root, error, now=None):
    try:
        path = Path(root) / FAILURES
        path.parent.mkdir(parents=True, exist_ok=True)
        moment = datetime.now(timezone.utc) if now is None else now
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": moment.isoformat(), "error": type(error).__name__, "detail": str(error)[:300]}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def observe(root=ROOT):
    """For the hook: never raises, and the next hook tries again."""
    try:
        made = archive_once(root)
        if made:
            print(f"[log-archive] kept {made.name}", flush=True)
        return made
    except Exception as error:
        record_failure(root, error)
        return None


if __name__ == "__main__":
    print(archive_once() or "nothing new")
