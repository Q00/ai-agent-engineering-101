"""Offline check of react.py with a scripted fake model: no harness calls, a few seconds.

    py test_react.py
"""
import react

TOOLS = {"check": {"args": "(price)", "description": "is the price allowed?",
                   "fn": lambda a: "ok" if a.get("price", 0) >= 100 else "below 100"}}
FINALS = {"counter": {"args": "(price)", "description": "counter at price"}}


def validate(action, args):
    return (args.get("price", 0) >= 100, "price is below 100")


def fake(*answers):
    """A model that returns the given answers in order, and records the prompts it saw."""
    seen = []

    def model(prompt, system=None):
        seen.append(prompt)
        return answers[len(seen) - 1]
    model.seen = seen
    return model


def case(name, model, want_action, want_steps, want_in_last_prompt=None):
    d, trace = react.run("system", "situation", TOOLS, FINALS, validate, max_steps=3, model=model)
    got = d["action"] if d else None
    ok = got == want_action and len(trace) == want_steps
    if ok and want_in_last_prompt:
        ok = want_in_last_prompt in model.seen[-1]
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: action={got} steps={len(trace)}")
    return ok


results = [
    case("valid final at once", fake('{"thought": "t", "action": "counter", "args": {"price": 115}}'),
         "counter", 1),
    case("code fence around JSON", fake('```json\n{"thought": "t", "action": "counter", "args": {"price": 120}}\n```'),
         "counter", 1),
    case("bad JSON, then valid", fake("I think 115.", '{"action": "counter", "args": {"price": 115}}'),
         "counter", 2, "FORMAT ERROR"),
    case("tool, then final", fake('{"action": "check", "args": {"price": 90}}', '{"action": "counter", "args": {"price": 100}}'),
         "counter", 2, "Observation: below 100"),
    case("rule break, then fixed", fake('{"action": "counter", "args": {"price": 50}}', '{"action": "counter", "args": {"price": 100}}'),
         "counter", 2, "REJECTED BY RULES: price is below 100"),
    case("unknown action, then valid", fake('{"action": "dance"}', '{"action": "counter", "args": {"price": 100}}'),
         "counter", 2, "unknown action"),
    case("never valid: stops at 3 steps", fake("x", "y", '{"action": "counter", "args": {"price": 1}}'),
         None, 3),
]
print(f"\n{sum(results)}/{len(results)} passed")
raise SystemExit(0 if all(results) else 1)
