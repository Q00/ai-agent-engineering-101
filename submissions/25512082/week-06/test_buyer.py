"""Model-free tests for Buyer A2A protocol and safety behavior."""
import json
import unittest
from unittest.mock import MagicMock, patch

import httpx

import buyer
import harness


def task(state, task_id="task-1", text=None, price=None):
    value = {"id": task_id, "contextId": "ctx-1", "status": {"state": state}}
    if text is not None:
        value["status"]["message"] = {"parts": [{"text": text}]}
    if price is not None:
        value["artifacts"] = [{"name": "receipt", "parts": [{"data": {"price": price}}]}]
    return {"jsonrpc": "2.0", "id": "response", "result": value}


class ScriptedSeller:
    def __init__(self, responses, rpc_url="http://seller.test/rpc"):
        self.responses = iter(responses)
        self.rpc_url = rpc_url
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={
                "name": "Mock Shop", "skills": [{"id": "buy"}],
                "supportedInterfaces": [{"protocolBinding": "JSONRPC", "protocolVersion": "1.0",
                                         "url": self.rpc_url}],
            })
        response = next(self.responses)
        return response if isinstance(response, httpx.Response) else httpx.Response(200, json=response)


class BuyerTests(unittest.TestCase):
    def make_client(self, responses):
        seller = ScriptedSeller(responses)
        http = httpx.Client(transport=httpx.MockTransport(seller))
        self.addCleanup(http.close)
        return buyer.BuyerClient("http://seller.test", http), seller

    def test_cli_and_url_validation(self):
        args = buyer.parse_args(["--url", "http://localhost:9999", "--item", "lamp", "--budget", "120"])
        self.assertEqual((args.item, args.budget), ("lamp", 120))
        for argv in (
            ["--url", "file:///tmp/shop", "--item", "lamp", "--budget", "120"],
            ["--url", "http://user:pass@shop", "--item", "lamp", "--budget", "120"],
            ["--url", "http://shop", "--item", "lamp", "--budget", "0"],
        ):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                buyer.parse_args(argv)

    def test_agent_card_selects_v1_jsonrpc_interface(self):
        client, seller = self.make_client([])
        card = client.fetch_agent_card()
        self.assertEqual(card["name"], "Mock Shop")
        self.assertEqual(client.rpc_url, "http://seller.test/rpc")
        self.assertEqual(str(seller.requests[0].url), "http://seller.test/.well-known/agent-card.json")

    def test_deal_uses_header_and_same_task_id(self):
        responses = [
            task(buyer.INPUT_REQUIRED, text="lamp asking price: 130"),
            task(buyer.INPUT_REQUIRED, text="Counteroffer: 120"),
            task(buyer.COMPLETED, price=120),
        ]
        client, seller = self.make_client(responses)
        decisions = [
            {"action": "offer", "price": 100, "reason": "negotiate"},
            {"action": "accept", "price": 120, "reason": "within budget"},
        ]
        with patch("buyer.decide_buyer", side_effect=decisions) as decide:
            outcome = client.buy("lamp", 120)
        self.assertEqual((outcome.outcome, outcome.price), ("deal", 120))
        posts = seller.requests[1:]
        self.assertEqual(len(posts), 3)
        self.assertTrue(all(request.headers["A2A-Version"] == "1.0" for request in posts))
        bodies = [json.loads(request.content) for request in posts]
        self.assertNotIn("taskId", bodies[0]["params"]["message"])
        self.assertEqual(bodies[1]["params"]["message"]["taskId"], "task-1")
        self.assertEqual(bodies[2]["params"]["message"]["taskId"], "task-1")
        self.assertEqual([b["params"]["message"]["parts"][0]["text"] for b in bodies],
                         ["I want to buy a lamp", "100", "120"])
        self.assertEqual(decide.call_args_list[1].args[0].last_buyer_offer, 100)

    def test_walk_away_calls_cancel_once(self):
        responses = [task(buyer.INPUT_REQUIRED, text="bookshelf asking price: 338"),
                     task(buyer.CANCELED)]
        client, seller = self.make_client(responses)
        with patch("buyer.decide_buyer", return_value={"action": "walk_away", "price": None, "reason": "too high"}):
            outcome = client.buy("bookshelf", 230)
        self.assertEqual(outcome.outcome, "walked_away")
        cancel = json.loads(seller.requests[-1].content)
        self.assertEqual(cancel["method"], "CancelTask")
        self.assertEqual(cancel["params"], {"id": "task-1"})

    def test_rejected_and_canceled_are_clean_no_deal(self):
        for state, expected in ((buyer.REJECTED, "rejected"), (buyer.CANCELED, "canceled")):
            with self.subTest(state=state):
                client, _ = self.make_client([task(state)])
                with patch("buyer.decide_buyer") as decide:
                    outcome = client.buy("lamp", 120)
                self.assertEqual(outcome.outcome, expected)
                decide.assert_not_called()

    def test_second_decision_completed_response_is_checked_at_round_limit(self):
        responses = [
            task(buyer.INPUT_REQUIRED, text="lamp asking price: 150"),
            task(buyer.INPUT_REQUIRED, text="Counteroffer: 130"),
            task(buyer.COMPLETED, price=120),
        ]
        client, seller = self.make_client(responses)
        decisions = [
            {"action": "offer", "price": 110, "reason": "negotiate"},
            {"action": "offer", "price": 120, "reason": "final offer"},
        ]
        with patch("buyer.MAX_ROUNDS", 2), \
             patch("buyer.decide_buyer", side_effect=decisions) as decide:
            outcome = client.buy("lamp", 120)
        self.assertEqual((outcome.outcome, outcome.price), ("deal", 120))
        self.assertEqual(decide.call_count, 2)
        methods = [json.loads(request.content)["method"] for request in seller.requests[1:]]
        self.assertEqual(methods, ["SendMessage", "SendMessage", "SendMessage"])

    def test_second_decision_input_required_response_ends_at_round_limit(self):
        responses = [
            task(buyer.INPUT_REQUIRED, text="lamp asking price: 150"),
            task(buyer.INPUT_REQUIRED, text="Counteroffer: 130"),
            task(buyer.INPUT_REQUIRED, text="Counteroffer: 125"),
            task(buyer.CANCELED),
        ]
        client, seller = self.make_client(responses)
        decisions = [
            {"action": "offer", "price": 110, "reason": "negotiate"},
            {"action": "offer", "price": 120, "reason": "final offer"},
        ]
        with patch("buyer.MAX_ROUNDS", 2), \
             patch("buyer.decide_buyer", side_effect=decisions) as decide:
            outcome = client.buy("lamp", 120)
        self.assertEqual(outcome.outcome, "round_limit")
        self.assertEqual(decide.call_count, 2)
        methods = [json.loads(request.content)["method"] for request in seller.requests[1:]]
        self.assertEqual(methods, ["SendMessage", "SendMessage", "SendMessage", "CancelTask"])

    def test_over_budget_offer_is_blocked_and_canceled(self):
        responses = [task(buyer.INPUT_REQUIRED, text="lamp asking price: 130"), task(buyer.CANCELED)]
        client, seller = self.make_client(responses)
        with patch("buyer.decide_buyer", return_value={"action": "offer", "price": 121, "reason": "bad"}):
            with self.assertRaisesRegex(buyer.BuyerError, "budget"):
                client.buy("lamp", 120)
        bodies = [json.loads(request.content) for request in seller.requests[1:]]
        self.assertEqual([body["method"] for body in bodies], ["SendMessage", "CancelTask"])

    def test_over_budget_completed_deal_is_rejected(self):
        client, _ = self.make_client([task(buyer.COMPLETED, price=121)])
        with self.assertRaisesRegex(buyer.BuyerError, "exceeds the budget"):
            client.buy("lamp", 120)

    def test_model_error_cancels_and_fails(self):
        responses = [task(buyer.INPUT_REQUIRED, text="lamp asking price: 130"), task(buyer.CANCELED)]
        client, seller = self.make_client(responses)
        with patch("buyer.decide_buyer", side_effect=harness.HarnessError("mock timeout")):
            with self.assertRaisesRegex(buyer.BuyerError, "decision failed"):
                client.buy("lamp", 120)
        self.assertEqual(json.loads(seller.requests[-1].content)["method"], "CancelTask")

    def test_invalid_json_http_error_and_bad_card_fail(self):
        cases = [
            httpx.Response(200, content=b"not json"),
            httpx.Response(503, json={"error": "down"}),
        ]
        for response in cases:
            with self.subTest(status=response.status_code):
                transport = httpx.MockTransport(lambda request, r=response: r)
                http = httpx.Client(transport=transport)
                self.addCleanup(http.close)
                with self.assertRaises(buyer.BuyerError):
                    buyer.BuyerClient("http://seller.test", http).fetch_agent_card()
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"supportedInterfaces": []}))
        http = httpx.Client(transport=transport)
        self.addCleanup(http.close)
        with self.assertRaisesRegex(buyer.BuyerError, "no A2A"):
            buyer.BuyerClient("http://seller.test", http).fetch_agent_card()

    def test_timeout_cancels_without_retry(self):
        responses = [task(buyer.INPUT_REQUIRED, text="lamp asking price: 130"), task(buyer.CANCELED)]
        client, seller = self.make_client(responses)
        with patch("buyer.time.monotonic", side_effect=[0, 241]):
            with self.assertRaisesRegex(buyer.BuyerError, "time limit"):
                client.buy("lamp", 120)
        methods = [json.loads(request.content)["method"] for request in seller.requests[1:]]
        self.assertEqual(methods, ["SendMessage", "CancelTask"])

    def test_round_limit_cancels(self):
        responses = [task(buyer.INPUT_REQUIRED, text=f"Counteroffer: {130 - i}")
                     for i in range(buyer.MAX_ROUNDS + 1)] + [task(buyer.CANCELED)]
        client, seller = self.make_client(responses)
        with patch("buyer.decide_buyer", return_value={"action": "offer", "price": 100, "reason": "continue"}) as decide:
            outcome = client.buy("lamp", 120)
        self.assertEqual(outcome.outcome, "round_limit")
        self.assertEqual(decide.call_count, buyer.MAX_ROUNDS)
        self.assertEqual(json.loads(seller.requests[-1].content)["method"], "CancelTask")

    def test_owned_http_client_is_closed(self):
        fake = MagicMock()
        with patch("buyer.httpx.Client", return_value=fake):
            client = buyer.BuyerClient("http://seller.test")
            client.close()
        fake.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
