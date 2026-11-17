import asyncio
import json
import unittest

from public_market import json_heartbeat


class HeartbeatTests(unittest.IsolatedAsyncioTestCase):
    async def test_heartbeat_arrives_before_slow_reply_and_preserves_receipt(self):
        release = asyncio.Event()
        payload = b'{"jsonrpc":"2.0","id":"t","result":{"receipt":{"price":120,"sig":"unchanged"}}}'

        async def fetch():
            await release.wait()
            return payload

        stream = json_heartbeat(fetch, "t", interval=.01)
        prefix = await asyncio.wait_for(anext(stream), .2)
        prefix += await asyncio.wait_for(anext(stream), .2)
        release.set()
        chunks = [chunk async for chunk in stream]
        self.assertEqual(json.loads(prefix + b"".join(chunks)), json.loads(payload))
        self.assertEqual(chunks[-1], payload)

    async def test_empty_upstream_and_transport_error_become_json_rpc_errors(self):
        for outcome in (b"", b"<html>502</html>", ConnectionError("offline")):
            async def fetch():
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            data = b"".join([part async for part in json_heartbeat(fetch, "original-id", .01)])
            reply = json.loads(data)
            self.assertEqual(reply["id"], "original-id")
            self.assertEqual(reply["error"]["code"], -32603)

    async def test_disconnected_client_cancels_upstream(self):
        started, canceled = asyncio.Event(), asyncio.Event()

        async def fetch():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                canceled.set()

        stream = json_heartbeat(fetch, "t")
        await anext(stream)
        await started.wait()
        await stream.aclose()
        self.assertTrue(canceled.is_set())


if __name__ == "__main__":
    unittest.main()
