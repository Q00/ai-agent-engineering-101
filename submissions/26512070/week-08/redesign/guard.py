"""Keeps the redesign harness apart from the live week-08 harness.

The live harness (seller, buyer, outer, kit) runs Round 3 until 11/17 23:59. This package may read its
files as data, and nothing more:

- import: install() blocks every top-level module of the live harness (core, agents, outer, seller, ...),
  found by listing the week-08 folder, so `py -m redesign` started there cannot load it by accident.
- write: write() only accepts paths under redesign/work/, plus the rule book redesign/rules.json.
- read: read() opens harness files read-only, as text or bytes; nothing is executed.
- git: git_log() is the one outside command (git log, read-only) - the commit history is a source.
"""
import importlib.abc
import os
import subprocess
import sys
from pathlib import Path

OWN = Path(__file__).resolve().parent             # week-08/redesign
HARNESS = OWN.parent                               # week-08 (the live harness)
WORK = OWN / "work"                                # where this package writes
RULE_BOOK = OWN / "rules.json"                     # and the rule book


class IsolationError(RuntimeError):
    pass


def harness_modules():
    """Top-level names the live harness can be imported by."""
    names = set()
    for p in HARNESS.iterdir():
        if p.name == OWN.name or p.name.startswith((".", "__")):
            continue
        if p.is_dir() and (p / "__init__.py").exists():
            names.add(p.name)
        elif p.suffix == ".py":
            names.add(p.stem)
    return names


class _Block(importlib.abc.MetaPathFinder):
    def __init__(self, names):
        self.names = names

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in self.names:
            raise IsolationError(f"redesign must not import the live harness: {fullname}")
        return None


def install():
    if not any(isinstance(f, _Block) for f in sys.meta_path):
        sys.meta_path.insert(0, _Block(harness_modules()))


def _inside(p, root):
    try:
        Path(os.path.realpath(p)).relative_to(os.path.realpath(root))
        return True
    except ValueError:
        return False


def _writable(p):
    return _inside(p, WORK) or os.path.realpath(p) == os.path.realpath(RULE_BOOK)


def write(path, data):
    """Write text or bytes under redesign/work/ (or the rule book) only."""
    p = Path(path) if Path(path).is_absolute() else WORK / path
    if not _writable(p):
        raise IsolationError(f"redesign writes only under {WORK}: {p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    if isinstance(data, bytes):
        tmp.write_bytes(data)
    else:
        tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, p)
    return p


def append(path, line):
    p = Path(path) if Path(path).is_absolute() else WORK / path
    if not _inside(p, WORK):
        raise IsolationError(f"redesign writes only under {WORK}: {p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")
    return p


def read(rel, binary=False):
    """Read a live-harness file (path relative to week-08) as data."""
    p = HARNESS / rel
    if not _inside(p, HARNESS) or _inside(p, OWN):
        raise IsolationError(f"not a harness path: {rel}")
    return p.read_bytes() if binary else p.read_text(encoding="utf-8", errors="replace")


def git_log():
    """Commit messages that touch week-08, oldest first: [(short hash, iso date, message)]."""
    r = subprocess.run(["git", "log", "--reverse", "--date=iso-strict", "--format=%h%x1f%ad%x1f%B%x1e", "--", "."],
                       cwd=HARNESS, capture_output=True, text=True, encoding="utf-8", check=True)
    out = []
    for rec in r.stdout.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) == 3:
            out.append((parts[0].strip(), parts[1], parts[2].strip()))
    return out
