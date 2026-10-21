from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Final

import anyio
import httpx2
from a2a.types import TaskState
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from google.protobuf.json_format import MessageToDict
from pydantic import JsonValue, TypeAdapter

from http_client import create_client
from market_a2a.catalog import Catalog
from market_a2a.harness import Harness
from market_a2a.model_provider import FREE_MODEL, create_model_client
from market_a2a.receipts import ReceiptSigner, SignedReceipt
from market_a2a.seller import create_app
from tests.test_signed_seller import send, task_from

ROOT: Final = Path(__file__).resolve().parents[1]
EVIDENCE: Final = ROOT / ".omo/evidence/openrouter-funded-20261021"
CALL_LIMIT: Final = 20


async def main() -> None:
    signer = ReceiptSigner(Ed25519PrivateKey.generate())
    with (EVIDENCE / "local-seller.log").open("x", encoding="utf-8") as trace:
        def emit(line: str) -> None:
            _ = trace.write(line + "\n")
            trace.flush()
            if line.startswith("MODEL_USAGE"):
                assert harness.calls < CALL_LIMIT

        async with (
            create_client(model=True) as http,
            create_model_client(http) as model,
        ):
            harness = Harness(model, emit)
            assert harness.model == FREE_MODEL
            app = create_app(
                "http://seller/", harness, Catalog(ROOT / "catalog.json"),
                "Local Free Model Test", signer=signer,
            )
            records: list[dict[str, str | int | bool]] = []
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://seller", timeout=90,
            ) as client:
                for item, floor in (("lamp", 100), ("chair", 180), ("bookshelf", 260)):
                    start_calls = harness.calls
                    opening = task_from(await send(client, f"I want to buy a {item}"))
                    assert opening.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
                    low = task_from(await send(client, "1", opening.id))
                    assert low.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
                    assert not low.artifacts
                    closed = task_from(await send(client, str(floor), opening.id))
                    assert closed.status.state == TaskState.TASK_STATE_COMPLETED
                    receipt = TypeAdapter(dict[str, JsonValue]).validate_python(
                        MessageToDict(closed.artifacts[0].parts[0].data)
                    )
                    assert receipt["price"] == floor
                    signed = SignedReceipt.model_validate({
                        **{key: receipt[key] for key in
                           ("shop", "item", "task", "sig_seller")}, "price": floor,
                    })
                    assert signed.task == opening.id
                    assert signed.item == item
                    signer.key.public_key().verify(
                        base64.b64decode(signed.sig_seller), signed.canonical(),
                    )
                    calls_before_replay = harness.calls
                    replay = await send(client, str(floor), opening.id)
                    assert isinstance(replay, dict)
                    assert "error" in replay
                    assert harness.calls == calls_before_replay
                    records.append({"item": item, "receipt_price": floor,
                                    "signature_verified": True,
                                    "below_reserve_rejected": True,
                                    "calls": harness.calls - start_calls})
            summary = {"model": harness.model, "calls": harness.calls,
                       "trades": records, "signing_key": "ephemeral test key",
                       "market_result_submitted": False}
            _ = (EVIDENCE / "local-seller.json").write_text(
                json.dumps(summary, indent=2) + "\n", encoding="utf-8",
            )
            print(json.dumps(summary))


if __name__ == "__main__":
    anyio.run(main)
