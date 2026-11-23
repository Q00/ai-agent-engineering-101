"""Load and format-check an agent's harness dir (human code, not editable by the model).

The check is format only (validate level): required files, parseable configs, script routes that exist,
scripts that compile, skill front matter, a size cap on the always-read text, nothing outside the dir. It never judges
performance.
"""

import dataclasses
import json
import os
import py_compile
import tempfile

ALWAYS = ("systemprompt.md", "guardrails.md", "LongTermMEMORY.md", "tool_guidance.md")
REQUIRED = ALWAYS + ("routing.json", "memory.json", "VERSION")
MAX_ALWAYS_CHARS = 8000
ROUTE_BY = ("model", "script")


@dataclasses.dataclass
class Harness:
    name: str
    dir: str
    always_text: str
    routing: dict
    memory_cfg: dict
    version: int


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _always_text(d):
    parts = []
    for fn in ALWAYS:
        parts.append(f"## {fn}\n\n{_read(os.path.join(d, fn)).strip()}\n")
    return "\n".join(parts)


def load(dirpath):
    d = os.path.realpath(dirpath)
    return Harness(
        name=os.path.basename(d),
        dir=d,
        always_text=_always_text(d),
        routing=json.loads(_read(os.path.join(d, "routing.json"))),
        memory_cfg=json.loads(_read(os.path.join(d, "memory.json"))),
        version=int(_read(os.path.join(d, "VERSION")).strip()),
    )


def _json_obj(path, errors):
    try:
        obj = json.loads(_read(path))
    except (OSError, ValueError) as e:
        errors.append(f"{os.path.basename(path)} does not parse: {e}")
        return None
    if not isinstance(obj, dict):
        errors.append(f"{os.path.basename(path)} must be a JSON object")
        return None
    return obj


def _front_matter_ok(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return False
    keys = {ln.split(":", 1)[0].strip() for ln in lines[1:end] if ":" in ln}
    return {"name", "description"} <= keys


def validate_dir(dirpath):
    """Return a list of format errors; empty means the harness can be used."""
    errors = []
    if not os.path.isdir(dirpath) or os.path.islink(dirpath):
        return [f"{dirpath} is not a directory"]
    base = os.path.realpath(dirpath)
    for top, dirs, files in os.walk(base):
        for n in dirs + files:
            p = os.path.join(top, n)
            if os.path.islink(p):
                errors.append(f"symlink not allowed: {os.path.relpath(p, base)}")
            elif os.path.commonpath([os.path.realpath(p), base]) != base:
                errors.append(f"path outside harness: {os.path.relpath(p, base)}")
    for fn in REQUIRED:
        if not os.path.isfile(os.path.join(base, fn)):
            errors.append(f"missing {fn}")
    if errors:
        return errors

    try:
        v = int(_read(os.path.join(base, "VERSION")).strip())
        if v < 1:
            errors.append("VERSION must be a positive int")
    except ValueError:
        errors.append("VERSION must be an int")

    routing = _json_obj(os.path.join(base, "routing.json"), errors)
    _json_obj(os.path.join(base, "memory.json"), errors)
    scripts_dir = os.path.join(base, "scripts")
    if routing is not None:
        for kind, route in routing.items():
            if not isinstance(route, dict):
                errors.append(f"routing {kind}: must be an object")
                continue
            if route.get("by") not in ROUTE_BY:
                errors.append(f"routing {kind}: by must be one of {ROUTE_BY}")
            names = [route.get("script")] if route.get("by") == "script" else []
            if route.get("fallback_script") is not None:
                names.append(route.get("fallback_script"))
            for s in names:
                p = os.path.join(scripts_dir, str(s))
                if (
                    not isinstance(s, str)
                    or not s.endswith(".py")
                    or os.path.dirname(os.path.normpath(p)) != scripts_dir
                    or not os.path.isfile(p)
                ):
                    errors.append(f"routing {kind}: script {s!r} not found in scripts/")

    if os.path.isdir(scripts_dir):
        with tempfile.TemporaryDirectory(prefix="r3a2-pyc-") as tmp:
            for fn in sorted(os.listdir(scripts_dir)):
                if not fn.endswith(".py"):
                    continue
                try:
                    py_compile.compile(
                        os.path.join(scripts_dir, fn),
                        cfile=os.path.join(tmp, fn + "c"),
                        doraise=True,
                    )
                except py_compile.PyCompileError as e:
                    errors.append(
                        f"scripts/{fn} does not compile: {e.msg.strip()[:200]}"
                    )

    skills_dir = os.path.join(base, "skills")
    if os.path.isdir(skills_dir):
        for name in sorted(os.listdir(skills_dir)):
            sd = os.path.join(skills_dir, name)
            if not os.path.isdir(sd):
                continue
            sp = os.path.join(sd, "SKILL.md")
            if not os.path.isfile(sp):
                errors.append(f"skills/{name}/SKILL.md missing")
            elif not _front_matter_ok(_read(sp)):
                errors.append(
                    f"skills/{name}/SKILL.md needs front matter with name: and description:"
                )

    size = len(_always_text(base))
    if size > MAX_ALWAYS_CHARS:
        errors.append(f"always-read text is {size} chars, cap {MAX_ALWAYS_CHARS}")
    return errors
