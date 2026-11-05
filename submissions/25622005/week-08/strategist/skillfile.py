"""Rewrite policy numbers in strategy/SKILL.md in place, without disturbing anything else.

The file is the user's: its comments, alignment and prose body carry the reasoning, and the
user may edit it at any time. So the strategist never re-serialises the front matter. It
swaps only the value bytes of the targeted lines (a number, or a flow list of SKUs such as
`focus_skus: [A, B]`) and bumps `version`, refuses to write when
the file changed since it was read (Conflict), checks the result with the same parser and
validator the shop uses, and replaces the file atomically so the shop's hot reload never
sees a half-written file.

A key in policy.DEFAULTS that the file does not have yet (added to the code after the file was written) is
inserted at the end of its section (the section after the policy block when it is missing too), with the
line and comment of the repository's strategy/SKILL.md when it has one. The body section `## Strategist notes`
holds the strategist's own notes (set_notes): replaced whole, created at the end of the body.

History: every text the strategist writes is also kept next to the file as SKILL.md.<epoch seconds>
(save_copy; _1, _2, ... when the name is taken), and keep_hand_edit saves the file as it is when it differs
from the newest copy (an edit by hand, or the first run).
"""
import math
import os
import re
import stat
from pathlib import Path

import policy

# where a missing key's line and comment come from: the repository's SKILL.md next to this code, and the image's
# copy of it (Dockerfile: /opt/agent; in the container the code runs from /data, whose strategy/ is the live file)
TEMPLATES = (Path(__file__).resolve().parents[1] / "strategy" / "SKILL.md", Path("/opt/agent/strategy/SKILL.md"))
COMMENT_COL = 34
NOTES_HEAD = "## Strategist notes"
NOTES_MAX = 1200
# lines the notes never keep: the front matter's own top-level and section keys, or an indented "key:" line
FRONT_LIKE = re.compile(r"(?:name|version|description|policy|seller|stock|market|buyer|llm|forum)\s*:|\s+[\w.-]+\s*:")


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


def _locate(lines, start, end):
    """{dotted key: line index} of every key line between start and end (section lines too)."""
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
        if dotted in found:
            raise ValueError(f"{dotted}: appears twice")
        found[dotted] = i
        if not rest.strip():
            stack.append((indent, key))
    return found


def _block_end(lines, i, end):
    """Index after the last line nested under the section line i."""
    indent = len(lines[i]) - len(lines[i].lstrip(" "))
    last = i
    for j in range(i + 1, end):
        code = policy._strip_comment(lines[j].rstrip("\n"))
        if not code.strip():
            continue
        if len(code) - len(code.lstrip(" ")) <= indent:
            break
        last = j
    return last + 1


def _template_line(dotted):
    """The repository SKILL.md's line for dotted (comment and alignment), or None."""
    for t in TEMPLATES:
        try:
            lines = t.read_text(encoding="utf-8").splitlines(keepends=True)
            start, end = _front_lines(lines)
            i = _locate(lines, start, end).get(dotted)
        except (OSError, ValueError):
            continue
        if i is not None:
            return lines[i].rstrip("\n") + "\n"
    return None


def _insert(lines, dotted, value):
    """Insert the line of a policy key the file does not have (dotted 'policy.<section>.<key>')."""
    _, sec, key = dotted.split(".")
    start, end = _front_lines(lines)
    found = _locate(lines, start, end)
    line = _template_line(dotted)
    line = _replace_value(line, value) if line and line.startswith(f"    {key}:") else \
        f"    {key}: {value}".ljust(COMMENT_COL) + "# added by the strategist (a policy.DEFAULTS key)\n"
    new = [line]
    if f"policy.{sec}" in found:
        at = _block_end(lines, found[f"policy.{sec}"], end)
    elif "policy" in found:
        at = _block_end(lines, found["policy"], end)
        new.insert(0, f"  {sec}:\n")
    else:
        raise ValueError("no policy block in front matter")
    if at > 0 and not lines[at - 1].endswith("\n"):
        lines[at - 1] += "\n"
    lines[at:at] = new


def _edit(text, changes):
    lines = text.splitlines(keepends=True)
    start, end = _front_lines(lines)
    targets = {"policy." + k: fmt(v) for k, v in changes.items()}
    found = {k: i for k, i in _locate(lines, start, end).items() if k in targets or k == "version"}
    missing = targets.keys() - found.keys()
    unknown = sorted(k for k in missing if k.count(".") != 2 or _default(k[len("policy."):]) is None)
    if unknown or "version" not in found:
        raise ValueError(f"not found in front matter: {', '.join(unknown or ['version'])}")
    old_version = policy._scalar(policy._strip_comment(lines[found["version"]]).partition(":")[2])
    if isinstance(old_version, bool) or not isinstance(old_version, int):
        raise ValueError(f"version: expected a whole number, got {old_version!r}")
    for dotted, i in found.items():
        lines[i] = _replace_value(lines[i], str(old_version + 1) if dotted == "version" else targets[dotted])
    for dotted in sorted(missing):
        _insert(lines, dotted, targets[dotted])
    return "".join(lines)


def _default(key):
    try:
        return policy.get(policy.DEFAULTS, key)
    except (KeyError, TypeError):
        return None


def clean_notes(notes):
    """The model's notes as plain text for the body: no HTML tags, control characters, code fences, '---'
    lines, headings or front-matter-like lines; at most NOTES_MAX characters. None when nothing is left."""
    if not isinstance(notes, str):
        return None
    notes = re.sub(r"<[^<>]*>", "", notes).replace("<", "").replace(">", "")
    notes = "".join(c for c in notes if c in "\n" or (c.isprintable() and c != "\x7f"))
    out = []
    for line in notes.splitlines():
        t = line.rstrip()
        if (re.match(r"\s*(```|~~~)", t) or re.fullmatch(r"\s*([-=*_]\s*){3,}", t) or FRONT_LIKE.match(t)):
            continue
        t = re.sub(r"^\s*#+\s*", "", t)
        if t or (out and out[-1]):
            out.append(t)
    text = "\n".join(out).strip()[:NOTES_MAX].rstrip()
    return text or None


def notes_of(text):
    """The text of the body's `## Strategist notes` section, or None without one."""
    lines = text.splitlines()
    try:
        _, end = _front_lines([x + "\n" for x in lines])
        head = lines.index(NOTES_HEAD, end + 1)
    except ValueError:
        return None
    stop = next((i for i in range(head + 1, len(lines)) if re.match(r"#{1,2} ", lines[i])), len(lines))
    return "\n".join(lines[head + 1:stop]).strip()


def set_notes(text, notes):
    """text with the `## Strategist notes` section holding notes (already clean_notes'd)."""
    lines = text.splitlines(keepends=True)
    _, end = _front_lines(lines)
    block = [NOTES_HEAD + "\n", "\n", notes + "\n"]
    head = next((i for i in range(end + 1, len(lines)) if lines[i].rstrip("\n") == NOTES_HEAD), None)
    if head is None:
        body = "".join(lines)
        return body + ("" if body.endswith("\n") else "\n") + "\n" + "".join(block)
    stop = next((i for i in range(head + 1, len(lines)) if re.match(r"#{1,2} ", lines[i])), len(lines))
    return "".join(lines[:head] + block + (["\n"] if stop < len(lines) else []) + lines[stop:])


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


def apply(path, expected_text, changes, notes=None):
    """Write changes {"seller.floor_list": 0.55, "stock.focus_skus": ["G1"], ...} (and notes, clean_notes'd, into
    the body) into the file and return the new text. Conflict when the file is not expected_text; ValueError when
    the result would not validate. In both cases the file is left as it was."""
    path = Path(path)
    if path.read_text(encoding="utf-8") != expected_text:
        raise Conflict(f"{path.name} changed since it was read")
    new_text = _edit(expected_text, changes)
    if notes is not None:
        new_text = set_notes(new_text, notes)
    _check(new_text, changes)
    _atomic_write(path, new_text, expected_text)
    return new_text


# --- history: SKILL.md.<epoch seconds> next to the file

def copies(path):
    """The history copies of path, oldest first."""
    path = Path(path)
    pat = re.compile(re.escape(path.name) + r"\.(\d+)(?:_(\d+))?")
    out = []
    for p in path.parent.iterdir() if path.parent.is_dir() else ():
        m = pat.fullmatch(p.name)
        if m and p.is_file():
            out.append((int(m.group(1)), int(m.group(2) or 0), p))
    return [p for *_, p in sorted(out)]


def save_copy(path, text, ts):
    """Write text to <path>.<int(ts)> (or _1, _2, ... when taken) atomically, never over an existing copy.
    A file system without hard links (some NAS shares) gets a rename to a name that does not exist yet."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.copy.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        for n in range(1000):
            target = path.with_name(f"{path.name}.{int(ts)}" + (f"_{n}" if n else ""))
            try:
                os.link(tmp, target)
                return target
            except FileExistsError:
                continue
            except OSError:                             # no hard links here
                if target.exists():
                    continue
                os.replace(tmp, target)
                return target
        raise OSError(f"{path.name}.{int(ts)}: no free name")
    finally:
        tmp.unlink(missing_ok=True)


def keep_hand_edit(path, text, ts):
    """Save text as a copy when no copy exists or it differs from the newest one; the new copy or None."""
    newest = (copies(path) or [None])[-1]
    if newest is not None and newest.read_text(encoding="utf-8") == text:
        return None
    return save_copy(path, text, ts)
