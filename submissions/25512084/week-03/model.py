import os

from openai import OpenAI

MODEL = os.environ.get("AGENT_MODEL", "nvidia/nemotron-3.5-lightning:free")


class Meter:
    def __init__(self):
        self.tokens = 0
        self.calls = 0

    def add(self, prompt_tokens, completion_tokens):
        self.tokens += int(prompt_tokens or 0) + int(completion_tokens or 0)
        self.calls += 1


def call_model(system: str, user: str, meter: Meter) -> str:
    client = OpenAI()

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0,
    )

    usage = response.usage
    meter.add(
        getattr(usage, "prompt_tokens", 0),
        getattr(usage, "completion_tokens", 0),
    )

    return response.choices[0].message.content or ""
