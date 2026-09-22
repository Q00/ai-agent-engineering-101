import os
import time

from openai import OpenAI, RateLimitError


MODEL = os.environ.get(
    "AGENT_MODEL",
    "nvidia/nemotron-3-super-120b-a12b:free",
)


class Meter:
    def __init__(self):
        self.tokens = 0
        self.calls = 0

    def add(self, prompt_tokens, completion_tokens):
        self.tokens += int(prompt_tokens or 0) + int(completion_tokens or 0)
        self.calls += 1


def call_model(system, messages, meter, temperature=0, max_retries=4):
    client = OpenAI()

    full_messages = [
        {"role": "system", "content": system},
        *messages,
    ]

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=full_messages,
                temperature=temperature,
                extra_body={"reasoning": {"enabled": False}},
            )

            usage = response.usage
            meter.add(
                getattr(usage, "prompt_tokens", 0),
                getattr(usage, "completion_tokens", 0),
            )

            return response.choices[0].message.content or ""

        except RateLimitError:
            if attempt == max_retries - 1:
                raise

            wait = 2 ** (attempt + 1)
            print(f"[rate-limit] retrying in {wait}s")
            time.sleep(wait)
