"""Rewrite policy numbers in strategy/SKILL.md in place, without disturbing anything else.

The file is the user's: its comments, alignment and prose body carry the reasoning, and the
user may edit it at any time. So the strategist never re-serialises the front matter. It
swaps only the value bytes of the targeted lines (a number, or a flow list of SKUs such as
`focus_skus: [A, B]`) and bumps `version`, refuses to write when
the file changed since it was read (Conflict), checks the result with the same parser and
validator the shop uses, and replaces the file atomically so the shop's hot reload never
sees a half-written file.
"""
import math
import os
import re
import stat
from pathlib import Path

import policy


class Conflict(Exception):
    """The file on disk is not the text the change was computed from."""


def read(path):
    """(text, front, body, errors). errors is empty when the policy block validates once keys missing from
    the file are filled from policy.DEFAULTS, as policy.load does for the shop."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        return "", {}, "", [f"{Path(path).name}: {e}"]
    try:
        front, body = policy.split_skill(text)
    except ValueError as e:
        return text, {}, "", [f"{Path(path).name}: {e}"]
    return text, front, body, policy.validate(policy.fill_defaults(front.get("policy"))[0])


def ver(text):
    """'version/sha8' exactly as policy.load reports it for this text."""
    front, _ = policy.split_skill(text)
    return f"{front.get('version', 0)}/{policy._sha8(front['policy'])}"


def fmt(v):
    """A finite number, or a list of SKU strings as a flow list "[A, B]"."""
    if isinstance(v, list):
        if not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", x) for x in v):
            raise ValueError(f"not a list of SKUs: {v!r}")
        return "[" + ", ".join(v) + "]"
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"not a finite number: {v!r}")
    return str(v) if isinstance(v, int) else repr(float(round(v, 4)))


def _front_lines(lines):
    """Index range (start, end) of the front matter lines between the first two '---' lines."""
    if not lines or lines[0].rstrip("\n") != "---":
        raise ValueError("no front matter between --- lines")
    for i in range(1, len(lines)):
        if lines[i].rstrip("\n") == "---":
            return 1, i
    raise ValueError("no closing --- line")


def _replace_value(line, value):
    """The line with only its value bytes replaced; indent, spacing and comment kept."""
    raw = line.rstrip("\n")
    code = policy._strip_comment(raw)
    colon = code.index(":") + 1
    start = colon + len(code[colon:]) - len(code[colon:].lstrip())
    return raw[:start] + value + raw[len(code):] + line[len(raw):]


def _edit(text, changes):
    lines = text.splitlines(keepends=True)
    start, end = _front_lines(lines)
    targets = {"policy." + k: fmt(v) for k, v in changes.items()}
    targets["version"] = None
    found, stack = {}, []
    for i in range(start, end):
        code = policy._strip_comment(lines[i].rstrip("\n"))
        if not code.strip():
            continue
        indent = len(code) - len(code.lstrip(" "))
        key, _, rest = code.strip().partition(":")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        dotted = ".".join([k for _, k in stack] + [key])
        if not rest.strip():
            stack.append((indent, key))
        elif dotted in targets:
            if dotted in found:
                raise ValueError(f"{dotted}: appears twice")
            found[dotted] = i
    missing = targets.keys() - found.keys()
    if missing:
        raise ValueError(f"not found in front matter: {', '.join(sorted(missing))}")
    old_version = policy._scalar(policy._strip_comment(lines[found["version"]]).partition(":")[2])
    if isinstance(old_version, bool) or not isinstance(old_version, int):
        raise ValueError(f"version: expected a whole number, got {old_version!r}")
    targets["version"] = str(old_version + 1)
    for dotted, value in targets.items():
        lines[found[dotted]] = _replace_value(lines[found[dotted]], value)
    return "".join(lines)


def _check(new_text, changes):
    front, _ = policy.split_skill(new_text)
    errors = policy.validate(policy.fill_defaults(front.get("policy"))[0])
    if errors:
        raise ValueError("validate: " + "; ".join(errors))
    for k, v in changes.items():
        if policy.get(front["policy"], k) != (v if isinstance(v, list) else float(fmt(v))):
            raise ValueError(f"{k}: reads back as {policy.get(front['policy'], k)!r}, wanted {v!r}")


def _atomic_write(path, text, expected_text):
    """Temp file, fsync, then replace, but only if the file is still expected_text: the fsync can take
    hundreds of ms on a NAS disk, and a save landing in that window must not be overwritten."""
    mode = stat.S_IMODE(os.stat(path).st_mode)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        if path.read_text(encoding="utf-8") != expected_text:
            raise Conflict(f"{path.name} changed while the new version was being written")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def apply(path, expected_text, changes):
    """Write changes {"seller.floor_list": 0.55, "stock.focus_skus": ["G1"], ...} into the file and return the new text.
    Conflict when the file is not expected_text; ValueError when the result would not validate.
    In both cases the file is left as it was."""
    path = Path(path)
    if path.read_text(encoding="utf-8") != expected_text:
        raise Conflict(f"{path.name} changed since it was read")
    new_text = _edit(expected_text, changes)
    _check(new_text, changes)
    _atomic_write(path, new_text, expected_text)
    return new_text
