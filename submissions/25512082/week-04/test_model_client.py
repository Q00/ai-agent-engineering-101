import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from model_client import ModelClient, ModelConfig, RateLimitError


class HttpError(Exception):
    def __init__(self, status_code, headers=None, message="request failed"):
        super().__init__(message)
        self.status_code = status_code
        self.response = SimpleNamespace(
            status_code=status_code,
            headers=headers or {},
        )


def response(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def config(max_retries=2):
    return ModelConfig(
        provider="test",
        model="test-model",
        base_url="https://example.invalid/v1",
        temperature=0,
        max_tokens=32,
        max_retries=max_retries,
        backoff_seconds=2,
    )


def client_with_create(side_effect, max_retries=2):
    sleeps = []
    retries = []
    client = ModelClient(
        config(max_retries),
        sleep=sleeps.append,
        on_retry=lambda retry, maximum, delay: retries.append(
            (retry, maximum, delay)
        ),
    )
    create = Mock(side_effect=side_effect)
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return client, create, sleeps, retries


class RetryTests(unittest.TestCase):
    def test_first_429_uses_retry_after_then_succeeds(self):
        client, create, sleeps, retries = client_with_create(
            [HttpError(429, {"Retry-After": "0.25"}), response("ok")]
        )

        result = client.complete([{"role": "user", "content": "hi"}], "agent")

        self.assertEqual(result, "ok")
        self.assertEqual(create.call_count, 2)
        self.assertEqual(sleeps, [0.25])
        self.assertEqual(retries, [(1, 2, 0.25)])

    def test_repeated_429_uses_backoff_then_stops(self):
        client, create, sleeps, retries = client_with_create(
            [HttpError(429), HttpError(429), HttpError(429)], max_retries=2
        )

        with self.assertRaisesRegex(RateLimitError, "after 3 attempts") as caught:
            client.complete([{"role": "user", "content": "hi"}], "agent")

        self.assertEqual(create.call_count, 3)
        self.assertEqual(sleeps, [2, 4])
        self.assertEqual(retries, [(1, 2, 2), (2, 2, 4)])
        self.assertNotIn("secret-key", str(caught.exception))

    def test_non_429_errors_fail_immediately(self):
        for status_code in (401, 403, 404):
            with self.subTest(status_code=status_code):
                client, create, sleeps, retries = client_with_create(
                    [HttpError(status_code)], max_retries=2
                )
                with self.assertRaises(HttpError):
                    client.complete(
                        [{"role": "user", "content": "hi"}], "agent"
                    )
                self.assertEqual(create.call_count, 1)
                self.assertEqual(sleeps, [])
                self.assertEqual(retries, [])


if __name__ == "__main__":
    unittest.main()
