"""Prompt -> structured model decision -> validate -> at most one repair.

The API worker runs in an empty directory. The A2A process never invents prices.
"""
import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys
import uuid

import httpx

from negotiation.config import ROOT, load_settings
from negotiation.contracts import (
    BuyerContext, DecisionError, SellerContext, decision_schema,
    parse_buyer_decision, parse_seller_decision,
)
from negotiation.runlog import RunLog
from negotiation.catalog_contracts import (
    NeedContext, SelectionContext, catalog_schema, parse_need_decision, parse_selection_decision,
)


PARSERS = {"seller": parse_seller_decision, "buyer": parse_buyer_decision,
           "need": parse_need_decision, "select": parse_selection_decision}
CONTEXTS = {"seller": SellerContext, "buyer": BuyerContext,
            "need": NeedContext, "select": SelectionContext}


class HarnessError(RuntimeError):
    pass


def decide(role, context, observation, settings, log, client):
    if not settings.api_key:
        raise HarnessError("api_key_missing")
    instructions = (ROOT / "prompts" / f"{role}.md").read_text(encoding="utf-8")
    trusted = asdict(context)
    input_data = {"trusted_context": trusted, "observation": observation}
    parser = PARSERS[role]
    schema = decision_schema(role) if role in {"seller", "buyer"} else catalog_schema(role)
    for attempt in (1, 2):
        log.record("model_request", role=role, attempt=attempt, model=settings.model,
                   instructions=instructions, input=input_data)
        try:
            response = client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": "Bearer " + settings.api_key},
                json={"model": settings.model, "instructions": instructions,
                      "input": json.dumps(input_data, ensure_ascii=False),
                      "text": {"format": {"type": "json_schema", "name": role + "_decision",
                                          "strict": True, "schema": schema}},
                      "temperature": 0, "max_output_tokens": 512, "store": False},
            )
        except httpx.HTTPError:
            log.record("api_error", code="network_error", attempt=attempt)
            raise HarnessError("api_network_error") from None
        if response.status_code != 200:
            log.record("api_error", http_status=response.status_code, attempt=attempt)
            # Invalid-key errors can echo credentials: never log the raw body.
            raise HarnessError(f"api_http_{response.status_code}")
        try:
            payload = response.json()
            raw = "".join(part.get("text", "") for output in payload.get("output", [])
                          if output.get("type") == "message"
                          for part in output.get("content", []) if part.get("type") == "output_text")
        except (TypeError, ValueError, AttributeError):
            raise HarnessError("invalid_api_response") from None
        log.record("model_response", attempt=attempt, response_model=payload.get("model"),
                   response_id=payload.get("id"), status=payload.get("status"),
                   usage=payload.get("usage"), raw_decision=raw)
        if payload.get("status") != "completed" or not raw:
            raise HarnessError("model_did_not_complete")
        try:
            decision = parser(raw, context)
        except DecisionError as error:
            log.record("validation_failed", attempt=attempt, code=str(error))
            if attempt == 2:
                raise HarnessError("decision_invalid_after_repair") from None
            input_data = {"trusted_context": trusted, "observation": observation,
                          "repair": {"previous_decision": raw, "validation_error": str(error),
                                     "instruction": "Return a corrected decision under the unchanged trusted constraints."}}
        else:
            log.record("decision_validated", attempt=attempt, decision=decision.model_dump())
            return decision


class ModelHarness:
    def __init__(self, log_dir):
        self.log_dir = Path(log_dir).resolve()
        self.work_dir = ROOT / ".runtime" / "negotiation-empty"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        if list(self.work_dir.iterdir()):
            raise HarnessError("worker_directory_not_empty")

    async def seller(self, context, observation):
        return await self._run("seller", context, observation)

    async def buyer(self, context, observation):
        return await self._run("buyer", context, observation)

    async def need(self, context, observation):
        return await self._run("need", context, observation)

    async def select(self, context, observation):
        return await self._run("select", context, observation)

    async def _run(self, role, context, observation):
        log_path = self.log_dir / ("model-" + uuid.uuid4().hex + ".jsonl")
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(ROOT / "harness_cli.py"), "--role", role, "--log", str(log_path),
            cwd=self.work_dir, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        request = json.dumps({"context": asdict(context), "observation": observation}).encode("utf-8")
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(request), timeout=40)
        except (asyncio.TimeoutError, asyncio.CancelledError) as error:
            if process.returncode is None:
                process.kill()
            await process.wait()
            if isinstance(error, asyncio.CancelledError):
                raise
            raise HarnessError("worker_timeout") from None
        if process.returncode != 0:
            # The worker only emits safe error codes. Its detailed attempts live in log_path.
            raise HarnessError("worker_failed")
        try:
            parser = PARSERS[role]
            return parser(stdout.decode("utf-8"), context)
        except (ValueError, UnicodeError):
            raise HarnessError("invalid_worker_decision") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=list(PARSERS), required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    settings = load_settings()
    log = RunLog(args.log, settings.api_key)
    try:
        request = json.load(sys.stdin)
        context_class = CONTEXTS[args.role]
        context = context_class(**request["context"])
        with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
            decision = decide(args.role, context, request["observation"], settings, log, client)
        print(decision.model_dump_json())
        return 0
    except HarnessError as error:
        log.record("harness_failed", code=str(error))
        print(str(error), file=sys.stderr)
    except Exception:
        log.record("harness_failed", code="unexpected_worker_error")
        print("unexpected_worker_error", file=sys.stderr)
    return 1
