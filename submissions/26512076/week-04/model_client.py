import os
import time

from openai import OpenAI


MODEL = os.environ["AGENT_MODEL"]
TEMPERATURE = 0.2

client = OpenAI()


def call_model(system_prompt, history, max_retries=5):
    messages = [
        {"role": "system", "content": system_prompt},
        *history,
    ]

    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                temperature=TEMPERATURE,
                max_tokens=300,
                messages=messages,
                extra_body={
                    "reasoning": {
                        "enabled": False
                    }
                },
            )

            return response.choices[0].message.content.strip()

        except Exception:
            if attempt == max_retries - 1:
                raise

            wait_seconds = 2 ** (attempt + 1)
            time.sleep(wait_seconds)