"""모델 호출부. 3주차 contract_net.py의 call_model을 다중 턴 대화용으로 고쳤다.

3주차는 공고 하나에 호출 하나여서 user 문자열 하나만 받으면 됐지만, 협상은 대화가
이어지므로 messages 리스트를 받는다. 그 외(provider 선택, Meter, 429 재시도, 호출
간격)는 3주차와 같다.

API 키는 환경변수로만 쓴다. 코드에 적지 않는다.

환경변수:
  OPENAI_API_KEY     OpenAI 키 (필수). SDK가 직접 읽는다.
  OPENAI_BASE_URL    OpenAI 직접 호출이면 설정하지 않는다. OpenRouter면 그 주소.
  ANTHROPIC_API_KEY  설정되어 있으면 Anthropic SDK를 쓴다.
  AGENT_MODEL        모델 이름. 기본 gpt-4o-mini.
  AGENT_TEMPERATURE  기본 0. 세 조건에서 같아야 한다.
  AGENT_MAX_TOKENS   기본 200.
  AGENT_MIN_INTERVAL 호출 간 최소 간격(초). 기본 0. OpenRouter 무료면 3.2.
  AGENT_MAX_RETRIES  429 재시도 횟수. 기본 6.
  AGENT_NO_REASONING 1이면 reasoning을 끈다 (OpenRouter 무료 추론 모델용).
"""
import os
import time

PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get(
    "AGENT_MODEL",
    "claude-sonnet-4-5" if PROVIDER == "anthropic" else "gpt-4o-mini")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0"))
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "200"))
MIN_INTERVAL = float(os.environ.get("AGENT_MIN_INTERVAL", "0"))
MAX_RETRIES = int(os.environ.get("AGENT_MAX_RETRIES", "6"))
NO_REASONING = os.environ.get("AGENT_NO_REASONING", "") == "1"

BASE_URL = os.environ.get("OPENAI_BASE_URL", "") or "https://api.openai.com/v1"

_client = None


def _get_client():
    global _client
    if _client is None:
        if PROVIDER == "anthropic":
            import anthropic
            _client = anthropic.Anthropic()
        else:
            # OpenAI()는 OPENAI_API_KEY와 OPENAI_BASE_URL을 환경에서 스스로 읽는다.
            from openai import OpenAI
            _client = OpenAI()
    return _client


class Meter:
    """토큰과 모델 호출 횟수. 협상에서는 두 축을 따로 센다.

    agent_calls  에이전트가 말하기 위해 쓴 호출
    reader_calls 프로토콜 계층이 메시지를 읽기 위해 쓴 호출
    """

    def __init__(self):
        self.tokens = 0
        self.agent_calls = 0
        self.reader_calls = 0
        self.retries = 0

    def add(self, input_tokens: int, output_tokens: int, kind: str):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        if kind == "reader":
            self.reader_calls += 1
        else:
            self.agent_calls += 1


_last_call = 0.0


def _is_rate_limit(e: Exception) -> bool:
    name = type(e).__name__
    return name == "RateLimitError" or getattr(e, "status_code", None) == 429


def call_model(system: str, messages: list, meter: Meter, kind: str = "agent") -> str:
    """system prompt 하나와 대화 messages로 한 번 부르고 텍스트를 돌려준다.

    messages는 [{"role": "user"|"assistant", "content": str}, ...]. 상대의 말이 user,
    자기 말이 assistant다. 429가 오면 5s, 10s, 20s, 40s, 60s, 60s 물러나 다시 보낸다.
    재시도는 전송 계층의 일이므로 프로토콜 메시지 수에는 들어가지 않고 meter.retries에 센다.
    """
    global _last_call
    for attempt in range(MAX_RETRIES + 1):
        wait = MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        try:
            return _call_once(system, messages, meter, kind)
        except Exception as e:
            if not _is_rate_limit(e) or attempt == MAX_RETRIES:
                raise
            meter.retries += 1
            backoff = min(5 * 2 ** attempt, 60)
            print(f"  [429] rate limited, retry {attempt + 1}/{MAX_RETRIES} in {backoff}s",
                  flush=True)
            time.sleep(backoff)
    raise RuntimeError("unreachable")


def _call_once(system: str, messages: list, meter: Meter, kind: str) -> str:
    if PROVIDER == "anthropic":
        resp = _get_client().messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
            system=system, messages=messages)
        meter.add(resp.usage.input_tokens, resp.usage.output_tokens, kind)
        return "".join(b.text for b in resp.content if b.type == "text")

    extra = {"reasoning": {"enabled": False}} if NO_REASONING else None
    resp = _get_client().chat.completions.create(
        model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
        messages=[{"role": "system", "content": system}] + messages,
        **({"extra_body": extra} if extra else {}))
    usage = resp.usage
    meter.add(getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0), kind)
    return resp.choices[0].message.content or ""


def settings_line() -> str:
    """로그 첫 줄. provider, 모델, temperature, 턴 한도는 실행마다 기록한다."""
    return (f"provider={PROVIDER} base_url={BASE_URL if PROVIDER == 'openai' else 'anthropic'} "
            f"model={MODEL} temperature={TEMPERATURE} max_tokens={MAX_TOKENS}")
