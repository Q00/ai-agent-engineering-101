"""프로토콜 계층.

메시지 하나를 받아 (performative, price, ok) 를 돌려준다.
ok=False 면 format_errors 에 세고, 메시지 자체는 그대로 상대에게 전달된다.

free       : reader(모델 호출)가 대화 전체를 보고 마지막 메시지를 라벨링
tagged     : 맨 앞 태그를 정규식으로 읽고, propose 면 가격만 reader 가 읽음
structured : JSON 파싱, 모델 호출 없음
"""

import json
import re

from acl import READER_SYSTEM
from model import call_model

ACTS = {"propose", "accept-proposal", "reject-proposal", "refuse"}

TAG_RE = re.compile(r"^\s*\(\s*(propose|accept-proposal|reject-proposal|refuse)\s*\)", re.I)
JSON_RE = re.compile(r"\{.*\}", re.S)


def _as_price(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        m = re.search(r"\d+", v)
        if m:
            return int(m.group())
    return None


def _run_reader(transcript, meter):
    """transcript: ["[buyer] ...", "[seller] ...", ...] — 마지막 줄을 라벨링한다."""
    convo = "\n".join(transcript)
    raw = call_model(
        READER_SYSTEM,
        [{"role": "user", "content": convo}],
        meter=meter,
        is_reader=True,
    )
    m = JSON_RE.search(raw)
    if not m:
        return None, None, raw
    try:
        d = json.loads(m.group())
    except json.JSONDecodeError:
        return None, None, raw
    perf = d.get("performative")
    perf = perf.lower() if isinstance(perf, str) else None
    if perf not in ACTS:
        perf = None
    return perf, _as_price(d.get("price")), raw


def read(condition, text, transcript, meter):
    """(performative, price, ok, reader_raw) 반환. reader_raw 는 로그용."""
    if condition == "structured":
        return _read_structured(text)
    if condition == "tagged":
        return _read_tagged(text, transcript, meter)
    if condition == "free":
        return _read_free(transcript, meter)
    raise ValueError(condition)


def _read_structured(text):
    m = JSON_RE.search(text)
    if not m:
        return None, None, False, None
    try:
        d = json.loads(m.group())
    except json.JSONDecodeError:
        return None, None, False, None
    perf = d.get("performative")
    perf = perf.lower() if isinstance(perf, str) else None
    if perf not in ACTS:
        return None, None, False, None
    content = d.get("content") or {}
    price = _as_price(content.get("price")) if isinstance(content, dict) else None
    if perf == "propose" and price is None:
        return perf, None, False, None
    return perf, price, True, None


def _read_tagged(text, transcript, meter):
    m = TAG_RE.match(text)
    if not m:
        return None, None, False, None
    perf = m.group(1).lower()
    if perf != "propose":
        return perf, None, True, None
    _, price, raw = _run_reader(transcript, meter)   # 가격만 reader 가 읽는다
    if price is None:
        return perf, None, False, raw
    return perf, price, True, raw


def _read_free(transcript, meter):
    perf, price, raw = _run_reader(transcript, meter)
    if perf is None:
        return None, None, False, raw
    if perf == "propose" and price is None:
        return perf, None, False, raw
    return perf, price, True, raw