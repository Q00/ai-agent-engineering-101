"""Run on the server once the key is in place: do the three models answer, and do they call tools?

    python check_models.py          (OPENAI_API_KEY in the environment)

For each model, a plain answer (ask) and one tool round trip through run_tools with a small add
tool: OK when the model calls add(2, 3) and then answers 5. Prints the reasoning effort and the
time each took (the trading model must answer within ask()'s 20 seconds). Costs under a cent.
"""
import json
import sys
import time

import harness

ADD = {"type": "function", "function": {"name": "add", "description": "Add two integers.",
                                        "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                                                       "required": ["a", "b"]}}}


def main() -> int:
    good = True
    for model in (harness.MODEL, harness.MODEL_MID, harness.MODEL_STRONG):
        t = time.time()
        said = harness.ask("Reply with OK and nothing else.", model, timeout=60)
        plain = time.time() - t
        used = []

        def run(name, args):
            used.append((name, args))
            return json.dumps({"sum": int(args.get("a", 0)) + int(args.get("b", 0))})
        t = time.time()
        text = harness.run_tools([{"role": "user", "content": "Use the add tool to add 2 and 3, then reply with the sum only."}],
                                 model, [ADD], run, 2)
        tooled = time.time() - t
        ok = said.upper().startswith("OK") and used == [("add", {"a": 2, "b": 3})] and "5" in text
        good &= ok
        print(f"{model:15} effort {harness.EFFORT.get(model):6}  ask {said[:12]!r:14} {plain:5.1f} s   "
              f"tool {used} -> {text[:12]!r:14} {tooled:5.1f} s   {'OK' if ok else 'FAIL'}", flush=True)
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
