"""The rulebook: policy.json, read fresh on every call so a change by tuner.py (or a human) applies at once.

policy.json may hold running experiments, at most one per section:
  "experiments": [{"id", "section", "key", "arms": {"a": current, "b": challenger}, ...}]
section(name, deal_id) returns the section with that key set to the deal's arm. The arm is fixed by a hash of the
deal id, so half the deals get each value and a deal never switches arm halfway.
"""
import hashlib
import json
import os
from pathlib import Path

POLICY = Path(__file__).with_name("policy.json")


def load():
    return json.loads(POLICY.read_text(encoding="utf-8"))


def save(d):
    tmp = POLICY.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, POLICY)                  # readers never see half a file


def get(section):
    return load()[section]


def arm(deal_id):
    return "b" if int(hashlib.sha1(str(deal_id).encode()).hexdigest(), 16) % 2 else "a"


def section(name, deal_id):
    """(section dict with the experiment applied, arm or None, experiment id or None)."""
    d = load()
    sec = dict(d[name])
    exp = next((e for e in d.get("experiments", []) if e.get("section") == name), None)
    if exp is None:
        return sec, None, None
    a = arm(deal_id)
    sec[exp["key"]] = exp["arms"][a]
    return sec, a, exp["id"]
