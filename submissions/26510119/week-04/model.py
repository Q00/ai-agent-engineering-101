import os
import time
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
                extra_body={"reasoning": {"enabled": False}},
            )
            meter.add()
            if not resp.choices:
                if attempt < max_retries - 1:
                    print(f"  [empty] retrying in {2 ** attempt}s...")
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError("API returned empty choices")
            content = resp.choices[0].message.content
            return content.strip() if content else ""
        except Exception as e:
            if ("429" in str(e) or "empty" in str(e)) and attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"  [retry] {e} — waiting {wait}s...")
                time.sleep(wait)
            else:
                raise
