"""Check A2A task state rendering without spending model calls."""
import unittest
import uuid
from unittest.mock import patch

from starlette.testclient import TestClient

from harness import HarnessError
from seller_agent import DEFAULT, build


class SellerOpeningTests(unittest.TestCase):
    def send(self, client, item):
        response = client.post("/", headers={"A2A-Version": "1.0"}, json={
            "jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "SendMessage",
            "params": {"message": {
                "role": "ROLE_USER", "messageId": str(uuid.uuid4()),
                "parts": [{"text": f"I want to buy a {item}"}],
            }},
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("error", body)
        return body["result"].get("task", body["result"])

    def test_model_quotes_become_input_required_messages(self):
        for item, reserve, quote in [("lamp", 100, 171), ("chair", 180, 243), ("bookshelf", 260, 397)]:
            with self.subTest(item=item), patch("seller_agent.opening_price", return_value=quote) as decide:
                with TestClient(build(DEFAULT)) as client:
                    task = self.send(client, item)
                decide.assert_called_once_with(item, reserve, f"I want to buy a {item}")
                self.assertEqual(task["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
                text = task["status"]["message"]["parts"][0]["text"]
                self.assertEqual(text, f"{item}: asking {quote}. Send your offer as a number.")

    def test_harness_failure_becomes_failed_task_without_a_quote(self):
        with patch("seller_agent.opening_price", side_effect=HarnessError("test: invalid reply")):
            with TestClient(build(DEFAULT)) as client:
                task = self.send(client, "lamp")
        self.assertEqual(task["status"]["state"], "TASK_STATE_FAILED")
        self.assertEqual(task["status"]["message"]["parts"][0]["text"], "test: invalid reply")
        self.assertFalse(task.get("artifacts"))


if __name__ == "__main__":
    unittest.main()
