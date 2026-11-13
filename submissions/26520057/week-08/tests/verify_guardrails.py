"""Offline safety checks: no cloud/paid fallback, invalid JSON refusal, stock spend bounds."""
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path.home() / "submissions/26520057/week-08"
sys.path.insert(0, str(ROOT))
import harness
import buyer


def reply(value):
    return io.BytesIO(json.dumps(value).encode())


with tempfile.TemporaryDirectory() as tmp:
    testroot = Path(tmp)
    (testroot / "logs").mkdir()
    local = {"models": [{"name": harness.MODEL, "digest": harness.DIGEST,
                         "details": {"format": "gguf"}}]}
    cases = [
        ([reply({"models": [{"name": harness.MODEL, "details": {"format": "cloud"}}]})], RuntimeError),
        ([reply(local), reply({"model": harness.MODEL, "done": True,
                              "message": {"content": "invalid json"}})], ValueError),
        ([reply(local), reply({"model": "qwen3:4b-cloud", "done": True,
                              "message": {"content": "{}"}})], RuntimeError),
    ]
    for responses, error in cases:
        with patch.object(harness, "ROOT", testroot), patch.object(
                harness.urllib.request, "urlopen", side_effect=responses) as network:
            try:
                harness.ask("Safety check")
            except error:
                pass
            else:
                raise AssertionError("Unsafe model result was accepted")
            for call in network.call_args_list:
                req = call.args[0]
                url = req if isinstance(req, str) else req.full_url
                assert url.startswith("http://127.0.0.1:12571/"), url
            assert network.call_count == len(responses), "Unexpected fallback request"
    print("PASS local-only model identity, invalid JSON and no fallback")
    assert buyer.cap_offer(1532160, 1532160, "Asking 444119") == 444119
    assert buyer.cap_offer(999999, 9000, "Counteroffer: 6,000.") == 6000
    assert buyer.cap_offer(7000, 9000, "3-day warranty, SKU G123456") == 7000
    assert buyer.cap_offer(7000, 5000, "No explicit asking price") == 5000
    print("PASS offers cannot exceed budget or an explicit current asking price")
