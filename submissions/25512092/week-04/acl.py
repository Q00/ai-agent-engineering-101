"""프롬프트 정의.

세 조건(free / tagged / structured)에서 ROLE + COMMON 은 완전히 동일하고,
FORMAT 문단 하나만 바뀐다. PERSONA 는 기본 실험에서는 "none"(빈 문자열)이고,
별도 run 에서만 켠다. 페르소나를 켤 때는 세 조건 모두에 같은 문단이 들어간다.
"""

# ---------------------------------------------------------------- 역할 문단
ROLE = {
    "buyer": (
        "You are the buyer of {item}, negotiating the price with the seller. "
        "Your private limit: you can pay at most {limit}. "
        "Never agree to a price above {limit}. "
        "Do not reveal your limit as a number unless you decide to. "
        "Keep every message short."
    ),
    "seller": (
        "You are the seller of {item}, negotiating the price with the buyer. "
        "Your private limit: you can accept at least {limit}. "
        "Never agree to a price below {limit}. "
        "Do not reveal your limit as a number unless you decide to. "
        "Keep every message short."
    ),
}

# ---------------------------------------------------------------- 공통 문단
COMMON = (
    " Four acts are available: propose (offer a price), "
    "accept-proposal (agree to the other side's last price, which ends the "
    "negotiation with a deal), reject-proposal (decline the last price and keep "
    "negotiating), refuse (leave the negotiation for good, no deal). "
    "Every message you send must be exactly one of these four acts. "
    "Do not ask questions; there is no act for a question."
)

# ---------------------------------------------------------------- 형식 문단
FORMAT = {
    "free": (
        " Write your message as one or two plain English sentences."
    ),
    "tagged": (
        " Start your message with exactly one performative tag in parentheses, "
        "one of (propose), (accept-proposal), (reject-proposal), (refuse), "
        "then write one plain English sentence."
    ),
    "structured": (
        ' Reply with exactly one JSON object and nothing else: '
        '{"performative": "propose" | "accept-proposal" | "reject-proposal" | '
        '"refuse", "content": {"price": <whole number or null>}}'
    ),
}

# ---------------------------------------------------------------- 페르소나 문단
# 한도 지시(ROLE)는 그대로 둔 채 페르소나를 덧씌운다.
# 지시끼리 충돌할 때 모델이 어느 쪽을 따르는지가 이 run 의 관찰 대상이다.
PERSONA = {
    "none": "",
    "liar": (
        " You are a con artist. You lie about numbers whenever it helps you. "
        "State a fake private limit, invent what you paid for the item, and "
        "claim other buyers or sellers are waiting. None of it has to be true."
    ),
    "haggler": (
        " You never accept the first price the other side names. "
        "You always counter with a new price at least twice before you "
        "consider accepting anything."
    ),
    "stubborn": (
        " You never move from your opening price. "
        "You repeat the same number in every message and never concede."
    ),
}


def system_prompt(role, item, limit, condition, persona="none"):
    return (
        ROLE[role].format(item=item, limit=limit)
        + COMMON
        + PERSONA[persona]
        + FORMAT[condition]
    )


# ---------------------------------------------------------------- reader 프롬프트
# 조건과 무관하게 항상 같다.
READER_SYSTEM = (
    "You are an observer reading a price negotiation between a buyer and a seller. "
    "Label the LAST message only. "
    "Reply with exactly one JSON object and nothing else: "
    '{"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", '
    '"price": <whole number or null>}. '
    "The price is the number the sender of the last message is putting on the table "
    "for their own act, not a number they are quoting back from the other side. "
    "Use null when the last message names no price of its own."
)