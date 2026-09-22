import os
import time
import json
from openai import OpenAI

PROVIDER = "openrouter"
MODEL = os.environ.get("AGENT_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
TEMPERATURE = 0.7

_client = None

def get_client():
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1"),
            api_key=os.environ.get("OPENAI_API_KEY"),
        )
    return _client


class Meter:
    def __init__(self):
        self.calls = 0

    def add(self):
        self.calls += 1


def call_model(system, messages, meter, max_retries=5):
    client = get_client()
    full = [{"role": "system", "content": system}] + messages
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=full,
                temperature=TEMPERATURE,
                max_tokens=256,
            )
            meter.add()
            return resp.choices[0].message.content.strip()
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"  [429] retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise
