"""Explicit per-provider clients; keys never enter prompts, events, or manifests."""
from dataclasses import asdict, dataclass
import os
import time


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    limit: int = 200
    calls: int = 0

    def claim(self):
        if self.calls >= self.limit:
            raise BudgetExceeded("Model-call budget exhausted")
        self.calls += 1


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str
    key_env: str
    base_url: str
    reasoning_effort: str | None = None


def specs():
    return {
        "gpt": ModelSpec("openai", os.getenv("META_GPT_MODEL", "gpt-5.6-luna"),
                         "OPENAI_API_KEY", "https://api.openai.com/v1", "none"),
        "gemini": ModelSpec("google", os.getenv("META_GEMINI_MODEL", "gemini-3.8-flash"),
                            "GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai/"),
        "solar": ModelSpec("upstage", os.getenv("META_SOLAR_MODEL", "solar-pro4"),
                           "UPSTAGE_API_KEY", "https://api.upstage.ai/v1"),
    }


@dataclass
class Completion:
    message: dict
    input_tokens: int | None
    output_tokens: int | None
    elapsed_seconds: float

    @property
    def tokens(self):
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens


class APIClient:
    def __init__(self, spec, budget, emit):
        from openai import OpenAI
        self.spec, self.budget, self.emit = spec, budget, emit
        key = os.environ.get(spec.key_env, "").strip()
        if not key:
            raise ValueError(f"Missing local configuration: {spec.key_env}")
        self.client = OpenAI(api_key=key, base_url=spec.base_url, timeout=45.0,
                             max_retries=0)

    def complete(self, messages, *, tools=None, role="executor"):
        self.budget.claim()
        kwargs = {"model": self.spec.model, "messages": messages}
        # Keep provider-specific options out of other vendors' requests.
        if self.spec.provider == "openai":
            kwargs["max_completion_tokens"] = 2048
        else:
            kwargs["max_tokens"] = 2048
        if self.spec.reasoning_effort is not None:
            kwargs["reasoning_effort"] = self.spec.reasoning_effort
        if tools:
            kwargs["tools"] = tools
        start = time.monotonic()
        self.emit("request", role=role, config=asdict(self.spec), messages=messages,
                  tools=tools, output_limit=2048)
        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            # Provider error text can contain credential fragments; retain type/status.
            self.emit("api_error", role=role, error_type=type(exc).__name__,
                      status=getattr(exc, "status_code", None))
            raise RuntimeError(f"{self.spec.provider} request failed: {type(exc).__name__}") from None
        message = response.choices[0].message.model_dump(exclude_none=True)
        usage = response.usage
        result = Completion(message, getattr(usage, "prompt_tokens", None),
                            getattr(usage, "completion_tokens", None), time.monotonic() - start)
        self.emit("response", role=role, config=asdict(self.spec),
                  message=message, input_tokens=result.input_tokens,
                  output_tokens=result.output_tokens, elapsed_seconds=result.elapsed_seconds)
        return result

    def close(self):
        self.client.close()
