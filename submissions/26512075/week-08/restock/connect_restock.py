"""Prepare or apply a one-field agent.json change, preserving all other fields.

Defaults to a diff. --apply writes an exclusive backup first. It refuses to
replace an existing different restock command unless --replace-restock is given.
Never touches keys, seller/buyer code, trade databases, or .market state.
"""
import argparse
import difflib
import json
import os
from pathlib import Path

COMMAND = "python restock/restock_policy.py --state {state} --explain"


def connect(path, *, apply=False, replace=False):
    path = Path(path)
    original = path.read_bytes()
    data = json.loads(original.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("agent.json must be an object")
    desired = {"cmd": COMMAND}
    if data.get("restock") == desired:
        return "already_connected\n"
    if "restock" in data and not replace:
        raise ValueError("existing restock command found: review it before using --replace-restock")
    changed = dict(data, restock=desired)
    rendered = (json.dumps(changed, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    diff = "".join(difflib.unified_diff(original.decode("utf-8").splitlines(True),
                                     rendered.decode("utf-8").splitlines(True),
                                     fromfile="agent.json (before)", tofile="agent.json (after)"))
    if apply:
        backup = path.with_name(path.name + ".before-restock")
        with backup.open("xb") as f:
            f.write(original)
        temporary = path.with_name(path.name + ".restock.tmp")
        try:
            with temporary.open("xb") as f:
                f.write(rendered)
                f.flush()
                os.fsync(f.fileno())
            if path.read_bytes() != original:
                raise ValueError("agent.json changed while preparing the update")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return diff


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--agent", default="agent.json")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--replace-restock", action="store_true")
    a = p.parse_args()
    print(connect(a.agent, apply=a.apply, replace=a.replace_restock), end="")








