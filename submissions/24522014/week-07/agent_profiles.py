"""Choose agent.json or construct native commands for the same profile definitions."""

import argparse
import json
from pathlib import Path
import shlex
from string import Formatter
import sys

from harness import MODELS


ROOT = Path(__file__).resolve().parent
PROFILES = ("claude", "codex", "mixed-claude-seller", "mixed-codex-seller")


def validate_profile(config):
    if not isinstance(config, dict) or config.get("profile") not in PROFILES:
        raise ValueError("Unknown profile")
    if not isinstance(config.get("harness"), str) or not config["harness"].strip():
        raise ValueError("harness must describe both agents and models")
    for role, required in (("seller", {"port"}), ("buyer", {"url", "item", "budget"})):
        entry = config.get(role)
        if not isinstance(entry, dict) or entry.get("harness") not in MODELS:
            raise ValueError(f"{role} harness is invalid")
        if entry.get("model") != MODELS[entry["harness"]]:
            raise ValueError(f"{role} model does not match its harness")
        command = entry.get("cmd")
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"{role} command is missing")
        fields = {field for _, field, _, _ in Formatter().parse(command) if field is not None}
        if fields != required:
            raise ValueError(f"{role} command must contain exactly {sorted(required)}")
    return config


def load_profile(name):
    if name not in PROFILES:
        raise ValueError("Unknown profile")
    return validate_profile(json.loads((ROOT / "profiles" / (name + ".json")).read_text(encoding="utf-8")))


def command_for(config, role, **values):
    validate_profile(config)
    # Split our controlled templates BEFORE inserting values; never run a shell.
    command = [part.format(**values) for part in shlex.split(config[role]["cmd"])]
    if command[0] != "python":
        raise ValueError("Profiles must use the active Python interpreter")
    command[0] = sys.executable
    return command


def select_profile(name):
    config = load_profile(name)
    target = ROOT / "agent.json"
    # Change only the selected configuration. Existing evidence is never edited here.
    target.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return config


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, required=True)
    args = parser.parse_args()
    try:
        selected = select_profile(args.profile)
    except ValueError as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps(selected, ensure_ascii=False, indent=2))
