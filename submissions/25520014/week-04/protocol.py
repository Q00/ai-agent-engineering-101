"""프로토콜 계층. 받은 메시지에서 performative와 가격을 읽어 낸다.

세 조건의 차이가 전부 여기에 있다. 같은 협상, 같은 역할 프롬프트인데 이 계층이 메시지를
읽는 방법만 다르고, 그 차이가 correct / violation / format_errors / reader_calls로 나온다.

    free        reader(모델 호출)가 대화 전체를 보고 마지막 메시지를 라벨링한다.  1 call
    tagged      맨 앞 태그는 정규식, propose의 가격만 reader가 읽는다.          0 or 1 call
    structured  JSON 파싱. 모델 호출 없음.                                       0 calls

읽기 성공 판정은 세 조건에서 같은 규칙을 쓴다. 조건마다 기준이 다르면 format_errors 열을
조건 간에 비교할 수 없기 때문이다.

    ok  ==  performative가 네 행위 중 하나이고,
            propose인 경우 가격이 정수다.

propose인데 가격이 없으면 실패로 센다. 행위는 알아냈지만 그 propose로는 상대의
last_price를 갱신할 수 없어 프로토콜이 진행되지 않기 때문이다. 이것이 "프로토콜 계층이
읽지 못한 메시지"의 실질적인 뜻이다.

읽지 못한 메시지도 상대에게는 원문 그대로 간다. 읽기는 하네스의 일이고, 에이전트끼리
오가는 것은 언제나 원문이다.

코드펜스에 대하여. claude -p로 부른 haiku는 structured 조건에서 JSON을 ```json 펜스로
감싸 보낸다. 스펙("exactly one JSON object and nothing else")대로라면 형식 위반이지만,
이걸 전부 format_error로 세면 structured 조건이 통째로 파싱 실패가 되어 조건 간 비교가
사라진다. 그래서 펜스는 벗겨서 파싱하고, 벗긴 횟수를 따로 센다(Reading.flags의 "fence").
format_errors 열이 잡지 못하는 형식 위반이라는 사실 자체가 보고할 결과다.
"""
import json
import re

from acl import PERFORMATIVES, READER_SYSTEM
from model import call_claude

_TAG = re.compile(r"^\s*\(\s*([a-zA-Z-]+)\s*\)")
_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n?\s*```\s*$", re.DOTALL)
_DECODER = json.JSONDecoder()


class Reading:
    """메시지 하나를 읽은 결과.

    flags는 ok/실패와는 별개로 기록해 두는 정규화 흔적이다. 결과 판정에는 쓰지 않고
    로그와 REPORT에만 쓴다.
    """

    def __init__(self, performative=None, price=None, ok=False, detail="", flags=()):
        self.performative = performative
        self.price = price
        self.ok = ok
        self.detail = detail
        self.flags = tuple(flags)

    def __repr__(self):
        return f"{{'performative': {self.performative!r}, 'price': {self.price!r}}}"


def _judge(performative, price, detail, flags=()):
    """세 조건 공통의 성공 판정."""
    if performative not in PERFORMATIVES:
        return Reading(None, None, False, detail or f"performative not one of the four: {performative!r}", flags)
    if performative == "propose" and not isinstance(price, int):
        return Reading(performative, None, False, detail or "propose without a whole-number price", flags)
    if not isinstance(price, int):
        price = None
    return Reading(performative, price, True, detail, flags)


def _strip_fence(text):
    """```json ... ``` 를 벗긴다. 벗겼는지 여부를 같이 돌려준다."""
    m = _FENCE.match(text)
    return (m.group(1), True) if m else (text, False)


def render_transcript(transcript):
    """reader에게 보여 줄 대화 기록. transcript는 {"speaker", "text"} 리스트다."""
    lines = [f"{m['speaker']}: {m['text']}" for m in transcript]
    last = transcript[-1]["speaker"] if transcript else "?"
    return ("The negotiation so far:\n\n" + "\n\n".join(lines)
            + f"\n\nLabel the LAST message only (the one from {last}).")


def reader(transcript, meter):
    """관찰자 모델 호출 1회. (performative, price, raw) 를 돌려준다.

    reader는 하네스의 일부이므로 비공개 한도를 모른다. 라벨이 판단이 아니라 측정이어야
    하기 때문이다(acl.READER_SYSTEM 주석 참조).
    """
    raw = call_claude(READER_SYSTEM, render_transcript(transcript), meter)
    body, _ = _strip_fence(raw)
    try:
        obj, _ = _DECODER.raw_decode(body.strip())
    except ValueError:
        return None, None, raw
    if not isinstance(obj, dict):
        return None, None, raw
    return obj.get("performative"), obj.get("price"), raw


def read_structured(text, transcript, meter):
    """JSON 객체 하나를 파싱한다. 모델 호출 없음.

    raw_decode를 쓰므로 JSON 뒤에 문장이 붙어 있어도 앞의 객체만 읽고 나머지는 버린다.
    참조 실행에서 보고된 실패 모드가 정확히 여기다. '"price": null' 뒤 문장에 진짜 제안이
    있어도 파서는 그 가격을 보지 못한다. 버린 꼬리가 있으면 flags에 "trailing"으로 남긴다.
    """
    body, fenced = _strip_fence(text)
    flags = ["fence"] if fenced else []
    body = body.strip()
    try:
        obj, end = _DECODER.raw_decode(body)
    except ValueError:
        return Reading(None, None, False, "not JSON", flags)
    if body[end:].strip():
        flags.append("trailing")
    if not isinstance(obj, dict):
        return Reading(None, None, False, "JSON is not an object", flags)
    content = obj.get("content")
    price = content.get("price") if isinstance(content, dict) else None
    return _judge(obj.get("performative"), price, "", flags)


def read_tagged(text, transcript, meter):
    """맨 앞 태그는 정규식으로, propose의 가격만 reader가 읽는다."""
    m = _TAG.match(text)
    if not m:
        return Reading(None, None, False, "no leading (tag)")
    performative = m.group(1).lower()
    if performative not in PERFORMATIVES:
        return _judge(performative, None, f"tag not one of the four: {performative!r}")
    if performative != "propose":
        return _judge(performative, None, "")
    _, price, raw = reader(transcript, meter)
    return _judge(performative, price, "", ["reader:" + repr(raw)[:120]])


def read_free(text, transcript, meter):
    """reader가 대화 전체를 보고 마지막 메시지를 라벨링한다. 메시지마다 호출 1회."""
    performative, price, raw = reader(transcript, meter)
    if performative is None:
        return Reading(None, None, False, f"reader gave no usable label: {raw[:120]!r}")
    return _judge(performative, price, "")


READERS = {"free": read_free, "tagged": read_tagged, "structured": read_structured}


def read(condition, text, transcript, meter):
    """조건에 맞는 읽기 방법을 고른다. 에피소드 루프가 부르는 유일한 진입점이다."""
    return READERS[condition](text, transcript, meter)
