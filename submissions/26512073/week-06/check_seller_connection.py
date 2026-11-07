"""Development HTTP check with real harness calls; not the official trade test."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

import httpx
from seller import build


async def check(capture):
    def record(value):
        line = json.dumps(value)
        print(line, flush=True)
        capture.write(line + "\n")
        capture.flush()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=build(9999)),
                                 base_url="http://127.0.0.1:9999") as client:
        async def rpc(method, params):
            response = await client.post("/", headers={"A2A-Version": "1.0"}, json={
                "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params})
            record({"method": method, "params": params, "http": response.status_code,
                    "response": response.json()})
            response.raise_for_status()
            return response.json()

        async def send(text, task_id=None):
            message = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()),
                       "parts": [{"text": text}]}
            if task_id:
                message["taskId"] = task_id
            return await rpc("SendMessage", {"message": message})

        def task(reply, expected):
            value = reply["result"].get("task", reply["result"])
            assert value["status"]["state"] == expected, value
            return value

        card = await client.get("/.well-known/agent-card.json")
        record({"card": card.json()})
        assert card.json()["supportedInterfaces"][0]["protocolVersion"] == "1.0"
        first = task(await send("I want to buy a lamp"), "TASK_STATE_INPUT_REQUIRED")
        tid = first["id"]
        task(await send("80", tid), "TASK_STATE_INPUT_REQUIRED")
        done = task(await send("100", tid), "TASK_STATE_COMPLETED")
        prices = [p["data"]["price"] for a in done["artifacts"] for p in a["parts"] if "data" in p]
        assert prices == [100], prices
        assert "error" in await send("100", tid)

        first = task(await send("I want to buy a bookshelf"), "TASK_STATE_INPUT_REQUIRED")
        tid = first["id"]
        task(await send("200", tid), "TASK_STATE_INPUT_REQUIRED")
        task(await send("230", tid), "TASK_STATE_REJECTED")

        first = task(await send("I want to buy a chair"), "TASK_STATE_INPUT_REQUIRED")
        task(await rpc("CancelTask", {"id": first["id"]}), "TASK_STATE_CANCELED")
        record({"development_check": "passed", "official_trade_test": False})


if __name__ == "__main__":
    log_dir = Path(__file__).resolve().parent / "development_logs"
    log_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    with (log_dir / f"seller_connection_{stamp}.txt").open("x", encoding="utf-8") as capture:
        try:
            asyncio.run(check(capture))
        except Exception as error:
            capture.write(f"FAILED: {type(error).__name__}: {error}\n")
            raise
