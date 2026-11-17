"""Tool-free Chat/Meter, adapted from the week-02 OpenAI wrapper."""
import os
import platform
from dataclasses import asdict, dataclass
from importlib.metadata import version


@dataclass(frozen=True)
class Settings:
    provider: str = "openai"
    model: str = "gpt-5.6-luna"
    temperature: float = 0.7
    max_completion_tokens: int = 512
    reasoning_effort: str = "none"

    @classmethod
    def from_env(cls):
        settings = cls(
            provider=os.getenv("AGENT_PROVIDER", "openai"),
            model=os.getenv("AGENT_MODEL", "gpt-5.6-luna"),
            temperature=float(os.getenv("AGENT_TEMPERATURE", "0.7")),
            max_completion_tokens=int(os.getenv("AGENT_MAX_TOKENS", "512")),
            reasoning_effort=os.getenv("AGENT_REASONING_EFFORT", "none"),
        )
        if settings.provider != "openai":
            raise ValueError("This runner uses the OpenAI-compatible Chat Completions API")
        if not 0 <= settings.temperature <= 2 or settings.max_completion_tokens < 1:
            raise ValueError("Invalid temperature or output token limit")
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is required; no API call was made")
        return settings

    def metadata(self):
        return dict(asdict(self), python=platform.python_version(),
                    sdk=f"openai=={version('openai')}",
                    dotenv=version("python-dotenv"), api="chat.completions",
                    endpoint=("https://api.openai.com/v1" if
                              os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
                              == "https://api.openai.com/v1" else "custom; supply OPENAI_BASE_URL"),
                    timeout_seconds=60, max_retries=0, tools=False,
                    response_format="text (JSON requested in prompt only)")


@dataclass
class Meter:
    calls: int = 0
    replies: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def tokens(self):
        return self.input_tokens + self.output_tokens


class Chat:
    def __init__(self, settings):
        from openai import OpenAI
        self.settings = settings
        self.client = OpenAI(timeout=60, max_retries=0)

    def __call__(self, system, user, meter, log):
        s = self.settings
        meter.calls += 1
        response = self.client.chat.completions.create(
            model=s.model, temperature=s.temperature,
            max_completion_tokens=s.max_completion_tokens,
            reasoning_effort=s.reasoning_effort,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        meter.replies += 1
        usage = response.usage
        if usage is not None:
            meter.input_tokens += usage.prompt_tokens
            meter.output_tokens += usage.completion_tokens
        choice = response.choices[0]
        log("model", response_id=response.id, model=response.model,
            finish_reason=choice.finish_reason,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None)
        return choice.message.content or ""
