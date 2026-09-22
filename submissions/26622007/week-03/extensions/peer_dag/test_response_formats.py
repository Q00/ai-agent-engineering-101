"""Schema regression and actual serialized transport checks, with no API requests."""
import copy
import cli
import io
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from core import Limits, Runtime, Task, fingerprint, parse_artifact
from fixtures import DemoModel
from models import BASE, LiveModel, OpenRouterClient, ReplayModel, messages
from response_formats import response_format, text_schema
from contract_net import bid_response_format, parse_bid

ROOT = Path(__file__).resolve().parent


def validator(phase, payload):
    schema = response_format(phase, payload)["json_schema"]["schema"]
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


class SchemaTests(unittest.IsolatedAsyncioTestCase):
    def test_token_limit_is_optional_and_no_4096_application_ceiling_remains(self):
        config, _ = cli.load_config()
        self.assertNotIn("max_tokens", config["transport"])
        with tempfile.TemporaryDirectory() as tmp, patch.object(cli, "ROOT", Path(tmp)):
            for value in (2200, 8192):
                config["transport"]["max_tokens"] = value
                (Path(tmp) / "config.json").write_text(json.dumps(config))
                self.assertEqual(cli.load_config()[0]["transport"]["max_tokens"], value)
            for invalid in (None, True, 0, -1, 1.5, "2200"):
                config["transport"]["max_tokens"] = invalid
                (Path(tmp) / "config.json").write_text(json.dumps(config))
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    cli.load_config()

    def test_text_pattern_accepts_complete_multiline_text_under_full_matching(self):
        patterns = (text_schema(2000)["pattern"],
                    bid_response_format()["json_schema"]["schema"]["properties"]["reason"]["pattern"])
        for pattern in patterns:
            for value in ("주어진 수익성 자료를 계산했다.", "첫 줄\n둘째 줄", " A ", "x"):
                self.assertIsNotNone(re.fullmatch(pattern, value))
            for value in ("", " ", "\n\t"):
                self.assertIsNone(re.fullmatch(pattern, value))

    async def test_all_nested_runtime_phases_validate_against_public_schemas(self):
        demo, phases = DemoModel(0), set()
        async def model(worker, phase, payload, task_id):
            raw = await demo(worker, phase, payload, task_id)
            validator(phase, payload).validate(json.loads(raw))
            phases.add(phase)
            return raw
        task = Task.parse(json.loads((ROOT / "case.json").read_text()), root=True)
        result = await Runtime(model, Limits()).run(task)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(phases, {"propose", "review", "execute", "synthesize"})

    def test_real_array_failure_and_invalid_artifact_shapes_are_rejected(self):
        path = ROOT / "logs/20260921T051850-f6cc75-3-overconfident.jsonl"
        failure = next(json.loads(row["raw"]) for row in map(json.loads, path.read_text().splitlines())
                       if row["event"] == "model_reply" and row.get("phase") == "execute"
                       and row.get("task_id") == "release-review/tech_risk_review")
        valid = {"summary": "provided data analysis", "facts": {"count": 2, "passed": False, "note": "unverified"},
                 "evidence": ["provided data"]}
        invalid = [failure, dict(valid, extra=True), dict(valid, evidence=[]), dict(valid, summary=" ")]
        invalid += [dict(valid, facts={"value": value}) for value in ([], {}, None)]
        for phase in ("execute", "synthesize"):
            check = validator(phase, {})
            check.validate(valid)
            for value in invalid:
                with self.subTest(phase=phase, value=value):
                    self.assertFalse(check.is_valid(value))
                    with self.assertRaises(ValueError):
                        parse_artifact(json.dumps(value))

    def test_review_requires_exact_candidates_and_integer_scores(self):
        check = validator("review", {"candidates": {"A": {}, "C": {}}})
        scores = {"scores": {w: {d: 2 for d in ("coverage", "feasibility", "verification")} for w in ("A", "C")}}
        check.validate(scores)
        for mutation in ("missing", "extra", "boolean", "out_of_range"):
            wrong = copy.deepcopy(scores)
            if mutation == "missing":
                del wrong["scores"]["C"]
            elif mutation == "extra":
                wrong["scores"]["B"] = wrong["scores"]["A"]
            else:
                wrong["scores"]["A"]["coverage"] = True if mutation == "boolean" else 3
            self.assertFalse(check.is_valid(wrong))

    def test_base_bid_schema_keeps_the_public_bid_contract(self):
        check = Draft202012Validator(bid_response_format()["json_schema"]["schema"])
        for value in ({"bid": True, "confidence": 90, "reason": "fixture"},
                      {"bid": False, "confidence": 0, "reason": "fixture"}):
            check.validate(value)
            parse_bid(json.dumps(value))
        for field, invalid in (("bid", 1), ("confidence", True), ("confidence", 101), ("reason", " ")):
            value = {"bid": True, "confidence": 90, "reason": "fixture", field: invalid}
            self.assertFalse(check.is_valid(value))

    async def test_live_adapter_sends_each_schema_in_wire_bytes_and_request_log(self):
        config = json.loads((ROOT / "config.json").read_text())["transport"]
        sent, events = [], []
        def opener(request, timeout):
            sent.append(json.loads(request.data))
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}).encode())
        def client(key, config):
            return OpenRouterClient(key, config, opener=opener)
        def emit(event, **fields):
            events.append({"event": event, **fields})
        payload = {"max_steps": 4, "candidates": {"A": {}, "B": {}}, "source": {"goal": "public input"}}
        for condition in ("baseline", "homogeneous", "overconfident"):
            with patch("models.OpenRouterClient", client):
                live = LiveModel("test-only", config, emit, condition)
                for phase in ("propose", "review", "execute", "synthesize"):
                    await live("C", phase, payload, "root")
                    self.assertEqual(sent[-1]["response_format"], response_format(phase, payload))
                    self.assertEqual(sent[-1]["messages"], messages("C", phase, payload, condition))
                    roster_line = sent[-1]["messages"][0]["content"].split("[공통 팀 역할표]\n", 1)[1].splitlines()[0]
                    self.assertEqual(set(json.loads(roster_line)), {"A", "B", "C"})
                    self.assertIs(sent[-1]["provider"]["require_parameters"], True)
                    self.assertNotIn("max_tokens", sent[-1])
                    self.assertNotIn("max_completion_tokens", sent[-1])
        logged = [row["payload"] for row in events if row["event"] == "http_request"]
        self.assertEqual(logged, sent)
        self.assertEqual(len(sent), 12)

    async def test_replay_rejects_missing_or_modified_response_format(self):
        payload = {"source": {"goal": "public task"}}
        records = [
            {"event": "run_start", "settings": {"mode": "live"}},
            {"event": "http_request", "task_id": "root", "contractor": "A", "phase": "execute",
             "payload": {"messages": messages("A", "execute", payload)}},
            {"event": "model_reply", "task_id": "root", "worker": "A", "phase": "execute",
             "request_sha": fingerprint({"worker": "A", "phase": "execute", "payload": payload}), "raw": "{}"}]
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            tape = Path(folder) / "tape.jsonl"
            for form in (None, {"type": "json_object"}, response_format("execute", payload)):
                records[1]["payload"]["response_format"] = form
                tape.write_text("".join(json.dumps(row) + "\n" for row in records))
                replay = ReplayModel(tape)
                if form is None or form["type"] == "json_object":
                    with self.assertRaisesRegex(ValueError, "response_format differs"):
                        await replay("A", "execute", payload, "root")
                else:
                    self.assertEqual(await replay("A", "execute", payload, "root"), "{}")


if __name__ == "__main__":
    unittest.main()
