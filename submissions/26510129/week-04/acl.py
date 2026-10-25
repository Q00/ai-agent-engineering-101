"""프롬프트. 역할 문단과 공통 문단은 세 조건에서 글자 하나까지 같고, 뒤에 붙는 형식
문단 하나만 조건 이름으로 골라 바뀐다. reader 프롬프트는 조건과 무관하게 하나다.

이 파일에는 조건별 분기가 FORMAT 딕셔너리 한 곳밖에 없다. 그것이 이 실습의 독립변수다.
"""

# ---------------------------------------------------------------- 역할 문단 (공통)

ROLE = {
    "buyer": (
        "You are the buyer of {item}, negotiating the price with the seller. "
        "Your private limit: you can pay at most {limit}. "
        "Never agree to a price above {limit}. Do not reveal your limit. "
        "Try to close a deal below your limit if one is available."
    ),
    "seller": (
        "You are the seller of {item}, negotiating the price with the buyer. "
        "Your private limit: you can accept at least {limit}. "
        "Never agree to a price below {limit}. Do not reveal your limit. "
        "Try to close a deal above your limit if one is available."
    ),
}

# 네 행위의 뜻. FIPA Communicative Act Library에서 가져온 넷이고, 세 조건에서 같다.
COMMON = (
    " Four acts are available to you: "
    "propose (offer a price), "
    "accept-proposal (agree to the other side's last price, which ends the negotiation "
    "with a deal), "
    "reject-proposal (decline the last price and keep negotiating), "
    "refuse (leave the negotiation for good, with no deal). "
    "Send exactly one act per message. Prices are whole numbers of dollars. "
    "If no agreement is possible within your limit, refuse rather than break your limit."
)

# ---------------------------------------------------------------- 형식 문단 (독립변수)

FORMAT = {
    "free": (
        " Write your message as one or two plain English sentences. "
        "Do not use tags, labels or JSON."
    ),
    "tagged": (
        " Start your message with exactly one performative tag in parentheses, one of "
        "(propose), (accept-proposal), (reject-proposal), (refuse), and then write one "
        "plain English sentence. The tag comes first, nothing before it."
    ),
    "structured": (
        " Reply with exactly one JSON object and nothing else, no code fence and no text "
        "before or after it: "
        '{"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", '
        '"content": {"price": <whole number or null>}}. '
        "price is a number when you propose, and null otherwise."
    ),
}

CONDITIONS = ("free", "tagged", "structured")


def system_prompt(role: str, item: str, limit: int, condition: str) -> str:
    """역할 + 공통 + 형식. 조건이 바꾸는 것은 마지막 한 문단뿐이다."""
    return ROLE[role].format(item=item, limit=limit) + COMMON + FORMAT[condition]


# 첫 화자에게 주는 시작 신호. 세 조건에서 같고, 협상 내용은 담지 않는다.
# (모델 호출은 user 턴 하나를 요구하므로 buyer의 빈 history를 이 한 줄로 연다.)
KICKOFF = "The negotiation starts now. Send your first message."


# ---------------------------------------------------------------- reader 프롬프트

# 조건과 무관하게 하나. free에서는 대화 전체를 보고 마지막 메시지를 라벨링하고,
# tagged에서는 태그가 propose일 때 그 메시지의 가격만 읽는 데 쓴다.
READER_SYSTEM = (
    "You are an observer reading a price negotiation between a buyer and a seller. "
    "Label the LAST message only, using the message before it as context. "
    "Choose exactly one performative from these four: "
    "propose (offers a price), "
    "accept-proposal (agrees to the other side's last price), "
    "reject-proposal (declines the last price but keeps negotiating), "
    "refuse (leaves the negotiation for good). "
    "price is the whole-number price that the last message itself puts forward, "
    "or null if the last message puts forward no price of its own. "
    "Reply with exactly one JSON object and nothing else, no code fence and no explanation: "
    '{"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", '
    '"price": <whole number or null>}'
)

READER_USER = "Transcript so far:\n\n{transcript}\n\nLabel the LAST message."
