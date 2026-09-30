"""Week 04 — 에이전트 통신 언어 실험의 공통 부품.

세 조건이 다른 곳은 정확히 두 군데다.
  1) system prompt의 형식 문단 (FORMAT)
  2) 메시지를 읽는 프로토콜 계층 (read_message)

역할 문단(ROLE), 네 행위의 뜻(COMMON), reader 프롬프트(READER_SYSTEM),
모델, temperature, 턴 한도는 세 조건에서 전부 같다. 이 파일을 읽으면
"형식 문단 하나만 바뀌었다"를 눈으로 확인할 수 있어야 한다.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field

import anthropic

PROVIDER = "Anthropic Messages API"
MODEL = os.environ.get("AGENT_MODEL", "claude-haiku-4-5-20251001")
MAX_TOKENS = 512
MAX_TURNS = 8
ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")

# temperature는 환경변수로 준다. 모델이 거부하면 TEMPERATURE_STATE에 기록만 하고
# 파라미터 없이 다시 부른다. 3주차에서 claude-opus-5가 이 파라미터를 400으로
# 거부했기 때문에, "지정했다"가 아니라 "지정되었는지"를 로그에 남기려는 것이다.
_env_temp = os.environ.get("AGENT_TEMPERATURE", "0")
TEMPERATURE: float | None = None if _env_temp == "" else float(_env_temp)
TEMPERATURE_STATE = {"requested": TEMPERATURE, "accepted": None}

_client = anthropic.Anthropic()


@dataclass
class Meter:
    """호출과 토큰을 센다. reader 호출은 따로 센다 — 조건별 '읽는 비용'이 지표다."""
    calls: int = 0
    reader_calls: int = 0
    in_tokens: int = 0
    out_tokens: int = 0

    def __str__(self) -> str:
        return (f"calls={self.calls} reader_calls={self.reader_calls} "
                f"tokens={self.in_tokens + self.out_tokens}")


def call_model(system: str, messages: list[dict], meter: Meter, *, reader: bool = False) -> str:
    """모델 한 번 호출. 429와 과부하는 기다렸다 다시 시도한다."""
    kwargs = dict(model=MODEL, max_tokens=MAX_TOKENS, system=system, messages=messages)
    if TEMPERATURE is not None and TEMPERATURE_STATE["accepted"] is not False:
        kwargs["temperature"] = TEMPERATURE

    delay = 2.0
    for attempt in range(6):
        try:
            resp = _client.messages.create(**kwargs)
            if "temperature" in kwargs and TEMPERATURE_STATE["accepted"] is None:
                TEMPERATURE_STATE["accepted"] = True
            break
        except anthropic.BadRequestError as e:
            # temperature를 받지 않는 모델이면 한 번만 벗겨내고 다시 시도한다.
            if "temperature" in kwargs and "temperature" in str(e).lower():
                TEMPERATURE_STATE["accepted"] = False
                kwargs.pop("temperature")
                continue
            raise
        except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError):
            if attempt == 5:
                raise
            time.sleep(delay)
            delay *= 2
    else:  # pragma: no cover
        raise RuntimeError("retries exhausted")

    meter.calls += 1
    if reader:
        meter.reader_calls += 1
    meter.in_tokens += resp.usage.input_tokens
    meter.out_tokens += resp.usage.output_tokens
    return "".join(b.text for b in resp.content if b.type == "text").strip()


# ─── 프롬프트 ────────────────────────────────────────────────────────────────
# 역할 문단. 조건과 무관하게 같다.
ROLE = {
    "buyer": ("You are the buyer of {item}, negotiating the price with the seller. "
              "Your private limit: you can pay at most {limit}. Never agree to a price "
              "above {limit}. Pay as little as you can, but a deal within your limit is "
              "better than no deal."),
    "seller": ("You are the seller of {item}, negotiating the price with the buyer. "
               "Your private limit: you can accept at least {limit}. Never agree to a price "
               "below {limit}. Get as much as you can, but a deal at or above your limit is "
               "better than no deal."),
}

# 네 행위의 뜻. 조건과 무관하게 같다.
COMMON = (" Four acts are available: propose (offer a price), accept-proposal (agree to the "
          "other side's last price, which ends the negotiation with a deal), reject-proposal "
          "(decline the last price and keep negotiating), refuse (leave the negotiation for "
          "good, no deal). Every message you send is exactly one of these four acts. "
          "Keep it to one or two short sentences.")

# 형식 문단. 세 조건의 유일한 차이다.
FORMAT = {
    "free": " Write your message as one or two plain English sentences.",
    "tagged": (" Start your message with exactly one performative tag in parentheses, one of "
               "(propose), (accept-proposal), (reject-proposal), (refuse), then write one "
               "plain English sentence."),
    "structured": (' Reply with exactly one JSON object and nothing else: '
                   '{"performative": "propose" | "accept-proposal" | "reject-proposal" | '
                   '"refuse", "content": {"price": <whole number or null>}}.'),
}

# reader 프롬프트. free와 tagged가 같은 것을 쓴다 — 조건에 따라 바뀌지 않는다.
READER_SYSTEM = ("You are an observer reading a price negotiation between a buyer and a seller. "
                 "Label the LAST message only. Reply with exactly one JSON object and nothing "
                 'else: {"performative": "propose" | "accept-proposal" | "reject-proposal" | '
                 '"refuse", "price": <whole number or null>}. price is the number the last '
                 "message puts on the table, or null if it names no price.")


def system_prompt(role: str, item: str, limit: int, condition: str) -> str:
    return ROLE[role].format(item=item, limit=limit) + COMMON + FORMAT[condition]


# ─── 프로토콜 계층 ───────────────────────────────────────────────────────────
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)
_TAG = re.compile(r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)", re.I)


def _as_price(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


def _read_json_reply(text: str) -> tuple[str | None, int | None]:
    """reader의 답을 읽는다. 전체가 JSON 객체여야 한다."""
    try:
        obj = json.loads(_FENCE.sub("", text.strip()))
    except json.JSONDecodeError:
        return None, None
    if not isinstance(obj, dict):
        return None, None
    perf = obj.get("performative")
    return (perf if perf in ACTS else None), _as_price(obj.get("price"))


def _reader_label(transcript: list[str], meter: Meter) -> tuple[str | None, int | None]:
    """대화 전체를 보여 주고 마지막 메시지 하나를 라벨링하게 한다."""
    convo = "\n".join(transcript)
    raw = call_model(READER_SYSTEM, [{"role": "user", "content": convo}], meter, reader=True)
    return _read_json_reply(raw)


def read_message(condition: str, text: str, transcript: list[str], meter: Meter):
    """(performative, price, ok, label) — ok=False면 format_errors에 센다.

    읽지 못해도 메시지는 그대로 상대에게 간다. label은 로그에 적을 문자열이다.
    """
    if condition == "structured":
        # 모델 호출 없음. JSON만 읽는다.
        try:
            obj = json.loads(_FENCE.sub("", text.strip()))
        except json.JSONDecodeError:
            return None, None, False, "parse error (not one JSON object)"
        if not isinstance(obj, dict):
            return None, None, False, "parse error (not an object)"
        perf = obj.get("performative")
        if perf not in ACTS:
            return None, None, False, f"parse error (performative={perf!r})"
        content = obj.get("content")
        price = _as_price(content.get("price")) if isinstance(content, dict) else None
        if perf == "propose" and price is None:
            return None, None, False, "parse error (propose without a price)"
        return perf, price, True, f"parse {{'performative': {perf!r}, 'price': {price!r}}}"

    if condition == "tagged":
        # 태그는 정규식이 읽고, propose의 가격만 reader가 읽는다.
        m = _TAG.match(text)
        if not m:
            return None, None, False, "no leading tag"
        perf = m.group(1).lower()
        if perf != "propose":
            return perf, None, True, f"tag {perf!r}"
        _, price = _reader_label(transcript, meter)
        if price is None:
            return None, None, False, f"tag 'propose' but reader found no price"
        return perf, price, True, f"tag 'propose' + reader price={price!r}"

    # free — 프로그램이 읽을 수 있는 것이 없다. 메시지마다 reader를 부른다.
    perf, price = _reader_label(transcript, meter)
    if perf is None:
        return None, None, False, "reader None"
    if perf == "propose" and price is None:
        return None, None, False, "reader 'propose' without a price"
    return perf, price, True, f"reader {{'performative': {perf!r}, 'price': {price!r}}}"
