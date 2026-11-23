"""Candidate copy of the fail-closed restock contract."""
import json
import argparse


def decision() -> dict:
    return {"buy": []}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--state", required=True); parser.parse_args()
    print(json.dumps(decision(), separators=(",", ":")))
