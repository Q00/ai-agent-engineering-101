"""PC test of the model door (harness.py) against a fake Responses API: what goes out, what comes back.

    python test_harness.py

No key and no network: httpx.post is replaced by a fake that records each request body and
answers with Responses API output items. What it checks is our side of the API: the endpoint,
each model's reasoning effort, function tools in the Responses shape, a tool loop that gives the
API's own items (reasoning included) back with the tool's answer, and the last call after the
tool budget is spent. Whether the real models accept it is check_models.py's job, on the server.
"""
import json
import os
import sys

os.environ["OPENAI_API_KEY"] = "test-key-not-real"
os.environ.pop("HARNESS_FAKE_REPLY", None)

import httpx     # noqa: E402

import harness   # noqa: E402

results = []
SCHEMA = {"type": "function", "function": {"name": "replay", "description": "Replay a rule change.",
                                           "parameters": {"type": "object", "properties": {"field": {"type": "string"}}}}}


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail != "" else ""), flush=True)


class Answer:
    def __init__(self, status, data):
        self.status_code, self.data = status, data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPError(f"{self.status_code}")

    def json(self):
        return self.data


class FakeAPI:
    """Answers with the output item lists it was given, in order (an int: that HTTP error)."""
    def __init__(self, *outputs):
        self.outputs, self.bodies, self.urls = list(outputs), [], []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.urls.append(url)
        self.bodies.append(json)
        out = self.outputs.pop(0)
        return Answer(out, {}) if isinstance(out, int) else Answer(200, {"id": f"resp_{len(self.bodies)}", "output": out})


class DecisionsAPI(FakeAPI):
    """Answers a decisions request with the given probabilities by question name."""
    def __init__(self, probs):
        super().__init__()
        self.probs = probs

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.urls.append(url)
        self.bodies.append(json)
        return Answer(200, {"model": json["model"], "answers": [{"type": "predicate", "name": q["name"], "probability": self.probs[q["name"]]}
                                                               for q in json["questions"] if q["name"] in self.probs]})


def text(t):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": t}]}


def call(cid, name, args):
    return {"type": "function_call", "id": f"fc_{cid}", "call_id": cid, "name": name, "arguments": json.dumps(args)}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    real = harness.httpx.post
    try:
        print("\n[ask] the trading model")
        api = harness.httpx.post = FakeAPI([text("  9500  ")])
        got = harness.ask("What price did the shop ask?")
        b = api.bodies[0]
        check("one request to the Responses API, the trading model without reasoning, the prompt as input",
              api.urls[0].endswith("/v1/responses") and b["model"] == harness.MODEL and b["reasoning"] == {"effort": "none"}
              and b["input"] == "What price did the shop ask?", b)
        check("the answer's text, trimmed", got == "9500")
        harness.httpx.post = FakeAPI(500)
        check("an API error: an empty answer, so the caller falls back to its rule", harness.ask("x") == "")

        print("\n[run_tools] a tool loop through the Responses API")
        reasoning = {"type": "reasoning", "id": "rs_1", "summary": []}
        api = harness.httpx.post = FakeAPI([reasoning, call("call_1", "replay", {"field": "seller.price_adjust"})],
                                           [text('{"report": "ok"}')])
        ran = []
        out = harness.run_tools([{"role": "user", "content": "analyse"}], harness.MODEL_MID, [SCHEMA],
                                lambda n, a: ran.append((n, a)) or '{"deals": 1}', 3)
        first, second = api.bodies
        check("the tools go out in the Responses shape, not strict, one call at a time; the analysis model reasons low",
              first["tools"] == [{"type": "function", "name": "replay", "description": "Replay a rule change.",
                                  "parameters": SCHEMA["function"]["parameters"], "strict": False}]
              and first["parallel_tool_calls"] is False and first["reasoning"] == {"effort": "low"}, first.get("tools"))
        check("the code runs the tool the model called", ran == [("replay", {"field": "seller.price_adjust"})])
        check("the next request carries the API's own items back (reasoning and the call) and the tool's answer by call id",
              second["input"] == [{"role": "user", "content": "analyse"}, reasoning,
                                  call("call_1", "replay", {"field": "seller.price_adjust"}),
                                  {"type": "function_call_output", "call_id": "call_1", "output": '{"deals": 1}'}], second["input"])
        check("the final text comes back", out == '{"report": "ok"}')

        print("\n[run_tools] the budget spent")
        api = harness.httpx.post = FakeAPI([call("c1", "replay", {})], [text("done"), call("c2", "replay", {})])
        out = harness.run_tools([{"role": "user", "content": "go"}], harness.MODEL_STRONG, [SCHEMA], lambda n, a: "{}", 1)
        last = api.bodies[1]
        check("the integrated analysis reasons medium", api.bodies[0]["reasoning"] == {"effort": "medium"})
        check("after the last allowed call: no tools offered, earlier calls and answers sent as plain text",
              "tools" not in last and all("type" not in i for i in last["input"])
              and any("(tool answer)" in i["content"] for i in last["input"]), last["input"])
        check("a call asked for after that is not run; the text is taken", out == "done")
        harness.httpx.post = FakeAPI(400)
        check("an API error inside the loop: no answer, the analysis records that", harness.run_tools(
            [{"role": "user", "content": "go"}], harness.MODEL_MID, [SCHEMA], lambda n, a: "{}", 3) == "")

        print("\n[decide] the Decisions API")
        api = harness.httpx.post = DecisionsAPI({"q0": 0.85, "q1": 0.1})
        got = harness.decide("One negotiation.", {"buyer_messages": ["13000 is my final offer"]},
                             ["The buyer leaves if countered.", "The buyer is a person."])
        b = api.bodies[0]
        check("POST /v1/decisions with gpt-6-luna, the note as text and the evidence as JSON data, one predicate per statement",
              api.urls[0].endswith("/v1/decisions") and b["model"] == "gpt-6-luna"
              and b["input"] == [{"role": "user", "content": [{"type": "input_text", "text": "One negotiation."},
                                                              {"type": "input_json", "data": {"buyer_messages": ["13000 is my final offer"]}}]}]
              and b["questions"] == [{"type": "predicate", "name": "q0", "instructions": "The buyer leaves if countered."},
                                     {"type": "predicate", "name": "q1", "instructions": "The buyer is a person."}], b)
        check("the probabilities come back in the order asked", got == [0.85, 0.1], got)
        harness.httpx.post = DecisionsAPI({"q0": 0.5})
        check("a question left unanswered is None (decide by the rule)", harness.decide("n", {}, ["a", "b"]) == [0.5, None])
        harness.httpx.post = FakeAPI(503)
        check("an API error: None, so the caller decides by its rule", harness.decide("n", {}, ["a"]) is None)
        os.environ.pop("OPENAI_API_KEY")
        check("no key: no call at all", harness.chat([{"role": "user", "content": "x"}], harness.MODEL) is None
              and harness.ask("x") == "" and harness.decide("n", {}, ["a"]) is None)
    finally:
        harness.httpx.post = real
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
