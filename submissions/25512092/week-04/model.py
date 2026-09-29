"""모델 호출 래퍼.

3주차에 쓰던 call_model 이 있으면 이 파일 대신 그걸 import 해도 된다.
여기서는 anthropic SDK 를 직접 쓴다. API 키는 환경변수로만 읽는다.
"""

import os
import time

import anthropic

PROVIDER = "anthropic-api"
MODEL = "claude-haiku-4-5"
TEMPERATURE = 0.7
MAX_TOKENS = 300

_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


class Meter:
    """한 에피소드에서 쓴 reader 호출 수와 토큰을 센다."""

    def __init__(self):
        self.reader_calls = 0
        self.agent_calls = 0
        self.in_tokens = 0
        self.out_tokens = 0


def call_model(system, history, meter=None, is_reader=False):
    """history 는 [{"role": "user"|"assistant", "content": str}, ...]"""
    last_err = None
    for attempt in range(6):
        try:
            r = _client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                system=system,
                messages=history,
            )
            if meter is not None:
                if is_reader:
                    meter.reader_calls += 1
                else:
                    meter.agent_calls += 1
                meter.in_tokens += r.usage.input_tokens
                meter.out_tokens += r.usage.output_tokens
            return "".join(b.text for b in r.content if b.type == "text").strip()
        except anthropic.RateLimitError as e:          # HTTP 429
            last_err = e
            time.sleep(2 ** attempt)
        except anthropic.APIStatusError as e:
            last_err = e
            if e.status_code < 500:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError(f"model call failed after retries: {last_err}")