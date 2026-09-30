"""프로토콜 계층. 받은 메시지가 네 행위 가운데 무엇이고 가격이 얼마인지를 읽는다.

세 조건의 차이는 전부 이 파일에 있다.
  structured  JSON 파서. 모델 호출 0회.
  tagged      맨 앞 태그는 정규식, propose의 가격만 reader 호출 1회.
  free        대화 전체를 reader에게 주고 마지막 메시지를 라벨링. 메시지마다 호출 1회.

읽지 못한 메시지는 ok=False로 돌려보내 format_errors에 세지만, 메시지 자체는 그대로
상대에게 전달된다. 프로토콜 계층이 못 읽는 것과 상대 에이전트가 못 읽는 것은 다른 일이다.
"""
import json
import re
from dataclasses import dataclass

import acl
from model import call_model

ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")

TAG_RE = re.compile(r"^\s*\(\s*([a-zA-Z-]+)\s*\)")
JSON_RE = re.compile(r"\{[\s\S]*\}")


@dataclass
class Reading:
    """메시지 하나를 읽은 결과."""
    performative: str = None
    price: int = None
    ok: bool = False
    reader_calls: int = 0
    detail: str = ""          # 로그에 찍을 한 줄. 무엇을 보고 그렇게 읽었는지.


def _as_price(v) -> int:
    """가격을 정수로. 숫자가 아니면 None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if float(v).is_integer() else None
    if isinstance(v, str):
        m = re.search(r"-?\d+", v.replace(",", ""))
        return int(m.group(0)) if m else None
    return None


def _extract_json(text: str):
    """코드펜스와 앞뒤 문장을 걷어내고 첫 JSON 객체를 꺼낸다. 없으면 None."""
    m = JSON_RE.search(text)
    if not m:
        return None
    blob = m.group(0)
    for end in range(len(blob), 0, -1):       # 뒤에 문장이 붙은 경우를 잘라가며 시도
        try:
            obj = json.loads(blob[:end])
        except json.JSONDecodeError:
            continue
        return obj if isinstance(obj, dict) else None
    return None


# ---------------------------------------------------------------- reader (free, tagged)


def _reader(transcript: str, meter) -> tuple:
    """reader를 한 번 불러 마지막 메시지의 performative와 가격을 받는다.

    프롬프트는 조건과 무관하게 acl.READER_SYSTEM 하나다. 반환은 (perf, price, raw).
    """
    raw = call_model(acl.READER_SYSTEM,
                     [{"role": "user", "content": acl.READER_USER.format(transcript=transcript)}],
                     meter, kind="reader")
    obj = _extract_json(raw)
    if not isinstance(obj, dict):
        return None, None, raw.strip().replace("\n", " ")[:120]
    perf = obj.get("performative")
    perf = perf.strip().lower() if isinstance(perf, str) else None
    return (perf if perf in ACTS else None), _as_price(obj.get("price")), json.dumps(obj)


# ---------------------------------------------------------------- 세 조건


def _read_structured(text: str, transcript: str, meter) -> Reading:
    obj = _extract_json(text)
    if not isinstance(obj, dict):
        return Reading(detail="no JSON object in the message")
    perf = obj.get("performative")
    perf = perf.strip().lower() if isinstance(perf, str) else None
    if perf not in ACTS:
        return Reading(detail=f"performative not one of the four: {obj.get('performative')!r}")
    content = obj.get("content")
    price = _as_price(content.get("price")) if isinstance(content, dict) else None
    if perf == "propose" and price is None:
        return Reading(performative=perf,
                       detail="propose without a price in content.price")
    return Reading(performative=perf, price=price, ok=True,
                   detail=f"parser: {perf}" + (f" {price}" if price is not None else ""))


def _read_tagged(text: str, transcript: str, meter) -> Reading:
    m = TAG_RE.match(text)
    if not m:
        return Reading(detail="no performative tag at the start of the message")
    perf = m.group(1).strip().lower()
    if perf not in ACTS:
        return Reading(detail=f"tag not one of the four: ({m.group(1)})")
    if perf != "propose":
        return Reading(performative=perf, ok=True, detail=f"regex: {perf}")
    # 태그가 propose일 때만 가격을 reader가 읽는다.
    _, price, raw = _reader(transcript, meter)
    if price is None:
        return Reading(performative=perf, reader_calls=1,
                       detail=f"regex: propose, reader found no price ({raw})")
    return Reading(performative=perf, price=price, ok=True, reader_calls=1,
                   detail=f"regex: propose, reader price {price} ({raw})")


def _read_free(text: str, transcript: str, meter) -> Reading:
    perf, price, raw = _reader(transcript, meter)
    if perf is None:
        return Reading(reader_calls=1, detail=f"reader gave no usable performative ({raw})")
    if perf == "propose" and price is None:
        return Reading(performative=perf, reader_calls=1,
                       detail=f"reader: propose without a price ({raw})")
    return Reading(performative=perf, price=price, ok=True, reader_calls=1,
                   detail=f"reader: {raw}")


READERS = {"free": _read_free, "tagged": _read_tagged, "structured": _read_structured}


def read(condition: str, text: str, transcript: str, meter) -> Reading:
    """조건에 맞는 읽기를 고른다. transcript는 free/tagged의 reader가 보는 대화 전체다."""
    return READERS[condition](text, transcript, meter)
