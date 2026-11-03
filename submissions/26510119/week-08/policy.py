"""The rulebook: policy.json, read fresh on every call so a change by tuner.py (or a human) applies at once."""
import json
from pathlib import Path

POLICY = Path(__file__).with_name("policy.json")


def load():
    return json.loads(POLICY.read_text(encoding="utf-8"))


def get(section):
    return load()[section]
