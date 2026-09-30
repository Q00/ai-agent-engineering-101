"""system prompt 조립. 이 파일이 실험의 통제점이다.

한 에이전트의 system prompt는 세 문단을 이어 붙인 것이다.

    ROLE[역할]  +  COMMON  +  FORMAT[조건]

앞의 둘은 세 조건에서 글자 단위로 같다. 바뀌는 것은 FORMAT 문단 하나뿐이고, 그래야
조건 간 차이를 "메시지 형식의 차이"로 읽을 수 있다. 조건 이름을 ROLE이나 COMMON 안에서
분기시키는 순간 실험이 무너지므로 여기서 분기하지 않는다.

프롬프트에 넣지 않기로 한 것 하나: 턴 한도(8개)를 적지 않았다. 한도는 하네스가 강제하는
바깥쪽 규칙이고, 이걸 프롬프트에 넣으면 에이전트가 "곧 끝난다"를 알고 막판에 양보하는
행동이 생긴다. 그 행동은 형식과 무관하게 세 조건에 다 섞여 들어와 open 집계를 흐린다.
대신 한도에 걸린 에피소드는 outcome=open으로 그대로 남긴다.
"""

ROLE = {
    "buyer": ("You are the buyer of {item}. You are negotiating the price with the seller. "
              "Your private limit: you can pay at most {limit}. "
              "Never agree to a price above {limit}. Keep your limit to yourself. "
              "Try to settle on a price you are happy with, or walk away if you cannot."),
    "seller": ("You are the seller of {item}. You are negotiating the price with the buyer. "
               "Your private limit: you can accept at least {limit}. "
               "Never agree to a price below {limit}. Keep your limit to yourself. "
               "Try to settle on a price you are happy with, or walk away if you cannot."),
}

COMMON = (
    " Four acts are available: propose (offer a price), accept-proposal (agree to the other "
    "side's last price, which ends the negotiation with a deal), reject-proposal (decline the "
    "last price and keep negotiating), refuse (leave the negotiation for good, no deal). "
    "Every message you send performs exactly one of these four acts. Send one message per turn "
    "and nothing else."
)

FORMAT = {
    "free": " Write your message as one or two plain English sentences.",
    "tagged": (" Start your message with exactly one performative tag in parentheses, one of "
               "(propose), (accept-proposal), (reject-proposal), (refuse), then write one plain "
               "English sentence."),
    "structured": (' Reply with exactly one JSON object and nothing else: '
                   '{"performative": "propose" | "accept-proposal" | "reject-proposal" | '
                   '"refuse", "content": {"price": <whole number or null>}}.'),
}

CONDITIONS = ("free", "tagged", "structured")
PERFORMATIVES = ("propose", "accept-proposal", "reject-proposal", "refuse")


def system_prompt(role: str, item: str, limit: int, condition: str) -> str:
    """역할 + 공통 + 형식. 조건에 따라 달라지는 것은 세 번째 조각뿐이다."""
    return ROLE[role].format(item=item, limit=limit) + COMMON + FORMAT[condition]


# reader는 free와 tagged의 프로토콜 계층이 부르는 관찰자다. 에이전트가 아니라 하네스의
# 일부이므로 비공개 한도를 모른다. 한도를 알면 "이 가격은 한도 밖이니 거절이겠지" 같은
# 추론이 섞여 라벨이 측정 도구가 아니라 판단이 된다.
READER_SYSTEM = (
    "You are an observer reading a price negotiation between a buyer and a seller. "
    "Label the LAST message only. "
    'Reply with exactly one JSON object and nothing else: '
    '{"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", '
    '"price": <whole number or null>}. '
    "propose means the message offers a price. accept-proposal means the message agrees to the "
    "price the other side named last. reject-proposal means the message declines and the "
    "negotiation continues. refuse means the message leaves the negotiation for good. "
    "price is the number of currency units the LAST message itself names as its own offer, or "
    "null if the last message names no such number. Do not explain. Do not add any other key."
)
