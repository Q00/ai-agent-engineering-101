"""One model call per bid, through an OpenAI-compatible endpoint.

The experiment ran against a local vLLM server (see serve_model.sh). Any
OpenAI-compatible server works; only the environment variables change.

  OPENAI_BASE_URL     default http://127.0.0.1:8011/v1
  OPENAI_API_KEY      default "none" (vLLM ignores it)
  AGENT_MODEL         default Qwen/Qwen3.8-27B
  AGENT_TEMPERATURE   default 0.2
  AGENT_MAX_TOKENS    default 200
  AGENT_THINKING      "on" to let the model think first; default off
"""
import os

from openai import OpenAI

BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8011/v1")
MODEL = os.environ.get("AGENT_MODEL", "Qwen/Qwen3.8-27B")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.2"))
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "200"))
THINKING = os.environ.get("AGENT_THINKING", "off") == "on"
TIMEOUT = float(os.environ.get("AGENT_TIMEOUT", "120"))


class Meter:
    """Model calls and tokens for one run."""

    def __init__(self):
        self.calls = 0
        self.tokens = 0

    def add(self, usage):
        self.calls += 1
        if usage is not None:
            self.tokens += int(usage.prompt_tokens or 0) + int(usage.completion_tokens or 0)


class LLM:
    def __init__(self, meter: Meter):
        self.meter = meter
        self.client = OpenAI(base_url=BASE_URL,
                             api_key=os.environ.get("OPENAI_API_KEY", "none"),
                             timeout=TIMEOUT, max_retries=0)

    @staticmethod
    def settings_line() -> str:
        return (f"provider=vllm(openai-compatible) base_url={BASE_URL} model={MODEL} "
                f"temperature={TEMPERATURE} max_tokens={MAX_TOKENS} "
                f"enable_thinking={'true' if THINKING else 'false'}")

    def ask(self, system: str, user: str) -> str:
        """System prompt + one user message, no tools. Returns the raw text."""
        resp = self.client.chat.completions.create(
            model=MODEL,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            extra_body={"chat_template_kwargs": {"enable_thinking": THINKING}},
        )
        self.meter.add(resp.usage)
        return resp.choices[0].message.content or ""
