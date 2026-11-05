"""Adapt an existing Python harness to the TypeScript A2A agents.

Set DECISION_MODULE to an importable Python module that exports
    decide(request: dict) -> dict
This wrapper intentionally contains no fixed trading policy or mock model.
"""

import importlib
import json
import os
import sys



def decision_for(request: dict) -> dict:
    module_name = os.environ.get("DECISION_MODULE", "harness.model_decisions")
    decide = getattr(importlib.import_module(module_name), "decide")
    decision = decide(request)
    if not isinstance(decision, dict):
        raise TypeError("decide(request) must return a JSON-serializable dict")
    return decision


def main() -> None:
    request = json.load(sys.stdin)
    decision = decision_for(request)
    print(json.dumps(decision, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Harness error: {exc}", file=sys.stderr)
        sys.exit(1)
