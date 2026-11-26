"""Restore exact original commit IDs without changing published or existing refs."""
import argparse
import base64
import json
from pathlib import Path
import subprocess


def git(*args, data=None):
    return subprocess.run(["git", *args], input=data, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=True).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", default="week-07-original-restored")
    args = parser.parse_args()
    git("check-ref-format", "refs/heads/" + args.branch)
    root = Path(__file__).resolve().parent
    metadata = json.loads((root / "history-map.json").read_text())
    # The original public base is also an ancestor of the submission branch.
    git("bundle", "verify", str(root / metadata["original_bundle"]))
    git("bundle", "unbundle", str(root / metadata["original_bundle"]))
    objects = json.loads((root / metadata["restore_objects"]).read_text())
    for expected, entry in objects.items():
        if entry["type"] not in ("commit", "tree"):
            raise ValueError("Unexpected recovery object type")
        actual = git("hash-object", "-w", "-t", entry["type"], "--stdin",
                     data=base64.b64decode(entry["base64"], validate=True)).decode().strip()
        if actual != expected:
            raise ValueError("Recovery object hash mismatch")
    tip = metadata["source_tip"]
    git("fsck", "--no-reflogs", "--connectivity-only", tip)
    ref = "refs/heads/" + args.branch
    existing = subprocess.run(["git", "rev-parse", "--verify", ref], capture_output=True, text=True)
    if existing.returncode == 0:
        if existing.stdout.strip() != tip:
            raise RuntimeError("Existing recovery branch differs; no ref was overwritten")
    else:
        git("update-ref", ref, tip, "0" * 40)
    print(json.dumps({"original_tip": tip, "recovery_branch": ref,
                      "original_objects_verified": len(objects),
                      "published_history_changed": False}))


if __name__ == "__main__":
    main()
