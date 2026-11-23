"""One-turn API harness for lecture STEP 6, not a negotiation agent.

Read a prompt from stdin, call OpenAI Responses, write only the answer to stdout.
The local .env is read privately; credentials never become command arguments.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

import httpx


SUBMISSION = Path(__file__).resolve().parent.parent


def configuration():
    values = {}
    path = SUBMISSION / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.strip().partition("=")
            if separator and name in {"OPENAI_API_KEY", "AGENT_MODEL"}:
                values[name] = value.strip().strip("\"'")
    key = os.environ.get("OPENAI_API_KEY") or values.get("OPENAI_API_KEY", "")
    model = os.environ.get("AGENT_MODEL") or values.get("AGENT_MODEL") or "gpt-4o-mini"
    return key, model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe", action="store_true", help="Show model and key presence only, without an API call")
    parser.add_argument("--metadata-log", type=Path)
    args = parser.parse_args()
    key, model = configuration()
    if args.describe:
        print(json.dumps({"harness": "one-turn OpenAI Responses API", "model": model,
                          "key_configured": bool(key)}))
        return 0
    if not key:
        print("OPENAI_API_KEY is not configured; no API call made.", file=sys.stderr)
        return 1
    prompt = sys.stdin.read().strip()
    if not prompt:
        print("No stdin prompt; no API call made.", file=sys.stderr)
        return 1
    if args.metadata_log:
        log_path = args.metadata_log.resolve()
        if not log_path.is_relative_to(SUBMISSION / "logs"):
            print("Metadata log must be inside this submission's logs directory.", file=sys.stderr)
            return 1
        if log_path.exists():
            print("Refusing to overwrite an existing log.", file=sys.stderr)
            return 1
    try:
        with httpx.Client(timeout=60, trust_env=False, follow_redirects=False) as client:
            response = client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": "Bearer " + key},
                json={"model": model, "input": prompt, "max_output_tokens": 128,
                      "temperature": 0, "store": False},
            )
        if response.status_code != 200:
            # Authentication errors can echo a key. Never print their raw body.
            print(f"OpenAI API HTTP {response.status_code}; response body omitted to protect credentials.", file=sys.stderr)
            return 1
        payload = response.json()
        answer = "".join(
            part.get("text", "") for output in payload.get("output", [])
            if output.get("type") == "message"
            for part in output.get("content", []) if part.get("type") == "output_text"
        ).strip()
        if payload.get("status") != "completed" or not answer:
            print("OpenAI response did not complete with text output.", file=sys.stderr)
            return 1
        answer = answer.replace(key, "[REDACTED]")
        if args.metadata_log:
            metadata = {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "endpoint": "https://api.openai.com/v1/responses",
                "requested_model": model,
                "response_model": payload.get("model"),
                "response_id": payload.get("id"),
                "status": payload.get("status"),
                "usage": payload.get("usage"),
                "answer": answer,
            }
            with log_path.open("x", encoding="utf-8") as log:
                json.dump(metadata, log, ensure_ascii=False, indent=2)
        print(answer)
        return 0
    except httpx.HTTPError:
        print("OpenAI API network request failed; no credentials logged.", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError):
        print("Unable to read the response or write the metadata log; no credentials logged.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
