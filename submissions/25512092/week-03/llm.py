"""모델 호출 모듈.

weeks/week-02/starter/tools_shared.py 의 Meter 와 Chat 을 복사해서
1) 도구(tool) 관련 코드를 빼고
2) temperature 를 고정했다.
contract net 은 도구가 필요 없고, system prompt 와 user 메시지 하나만 보낸다.

provider 선택 규칙은 starter 와 같다.
  ANTHROPIC_API_KEY 가 있으면  -> Anthropic SDK
  없으면                        -> OpenAI 호환 (OPENAI_API_KEY, OPENAI_BASE_URL)
  AGENT_MODEL                   -> 모델 이름 지정
"""
import inspect
import os
import platform
import sys
import time

PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get(
    "AGENT_MODEL",
    "claude-sonnet-4-5" if PROVIDER == "anthropic" else "gpt-4o-mini")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "(default)") if PROVIDER == "openai" else "(anthropic)"

TEMPERATURE = 0.0     # 모든 조건, 모든 실행에서 같은 값 (SDK 가 받을 때만 전달)
MAX_TOKENS = 1024     # 응답 길이 상한
RETRIES = 3           # 네트워크/속도 제한 오류일 때만 다시 시도
RETRY_WAIT = 15       # 초. 재시도마다 15, 30초 대기
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}   # 잠시 뒤 다시 하면 되는 오류만
MIN_INTERVAL = 1.5    # 초. 호출 사이 최소 간격 (분당 약 40회 이하로 유지해 속도 제한을 피함)

_last_call = 0.0

_temp_ok = None       # SDK 가 temperature 인자를 받는지. None = 아직 확인 전


def sdk_version() -> str:
    try:
        import importlib
        m = importlib.import_module("anthropic" if PROVIDER == "anthropic" else "openai")
        return f"{m.__name__}=={m.__version__}"
    except Exception:
        return "sdk=unknown"


def temperature_supported() -> bool:
    """설치된 SDK 의 create() 가 temperature 인자를 받는지 미리 확인한다."""
    global _temp_ok
    if _temp_ok is None:
        try:
            client = _get_client()
            fn = (client.messages.create if PROVIDER == "anthropic"
                  else client.chat.completions.create)
            _temp_ok = "temperature" in inspect.signature(fn).parameters
        except Exception:
            _temp_ok = True
    return _temp_ok


def settings_line() -> str:
    """로그 파일 첫 줄에 적을 설정."""
    if temperature_supported():
        temp = str(TEMPERATURE)
    else:
        temp = "not-settable(SDK create() has no temperature argument; provider default used)"
    return (f"provider={PROVIDER} base_url={BASE_URL} model={MODEL} "
            f"temperature={temp} max_tokens={MAX_TOKENS} "
            f"{sdk_version()} python={platform.python_version()}")


class Meter:
    """모델 호출 횟수와 토큰 수를 한곳에서 센다."""

    def __init__(self):
        self.tokens = 0
        self.calls = 0

    def add(self, input_tokens, output_tokens):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.calls += 1


_client = None


def _get_client():
    global _client
    if _client is None:
        if PROVIDER == "anthropic":
            import anthropic
            _client = anthropic.Anthropic()
        else:
            from openai import OpenAI
            _client = OpenAI()
    return _client


class Chat:
    """모델과의 대화 하나. provider 별 메시지 형식 차이를 여기서 처리한다."""

    def __init__(self, system: str, meter: Meter):
        self.system = system
        self.meter = meter
        self.messages = []
        if PROVIDER == "openai":
            self.messages.append({"role": "system", "content": system})

    def add_user(self, text: str):
        self.messages.append({"role": "user", "content": text})

    def send(self) -> str:
        if PROVIDER == "anthropic":
            kwargs = dict(model=MODEL, max_tokens=MAX_TOKENS,
                          system=self.system, messages=self.messages)
            if temperature_supported():
                kwargs["temperature"] = TEMPERATURE
            resp = _get_client().messages.create(**kwargs)
            self.meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
            return "".join(b.text for b in resp.content if b.type == "text")

        kwargs = dict(model=MODEL, messages=self.messages, max_tokens=MAX_TOKENS)
        if temperature_supported():
            kwargs["temperature"] = TEMPERATURE
        resp = _get_client().chat.completions.create(**kwargs)
        usage = resp.usage
        self.meter.add(getattr(usage, "prompt_tokens", 0),
                       getattr(usage, "completion_tokens", 0))
        return resp.choices[0].message.content or ""   # 내용이 비면 빈 문자열 -> 파싱 실패로 처리됨


def call_model(system: str, user: str, meter: Meter) -> str:
    """system prompt 하나 + user 메시지 하나로 모델을 한 번 부르고 답 텍스트를 돌려준다."""
    global _temp_ok, _last_call
    chat = Chat(system, meter)
    chat.add_user(user)
    for attempt in range(1, RETRIES + 1):
        wait = MIN_INTERVAL - (time.time() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()
        try:
            return chat.send()
        except Exception as e:
            if "temperature" in str(e).lower():
                _temp_ok = False           # 서버가 temperature 를 거부: 이번 실행은 crash, 다음 실행부터 빼고 호출
                raise
            status = getattr(e, "status_code", None)
            retryable = status in RETRY_STATUS or (
                status is None and type(e).__name__ in ("APIConnectionError", "APITimeoutError"))
            if not retryable or attempt == RETRIES:
                raise                      # 코드/인증 오류는 바로, 나머지는 3번 뒤에 crash 로 기록
            print(f"  [retry] {type(e).__name__} status={status} "
                  f"-> {RETRY_WAIT * attempt}s 대기 후 재시도 ({attempt}/{RETRIES - 1})",
                  file=sys.stderr, flush=True)
            time.sleep(RETRY_WAIT * attempt)